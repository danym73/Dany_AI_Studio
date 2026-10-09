#!/usr/bin/env python3
"""
DANY AI STUDIO - Control Hub & Production Server (v2.3)

Servidor local sin dependencias externas (libreria estandar de Python).
Seguridad:
  - Escucha solo en 127.0.0.1 (configurable en config.json -> server.host)
  - Comprueba la cabecera Host (anti DNS-rebinding) y Origin / Sec-Fetch-Site (anti CSRF)
  - Sin CORS: solo la propia interfaz puede llamar a la API
  - Toda la API (salvo /api/session) exige el token X-Studio-Token (nuevo en cada arranque)
  - POST solo con Content-Type: application/json y cuerpo limitado
Toda la logica de negocio vive en studio_core.py (compartida con mcp_server.py).
"""

import http.server
import json
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import traceback
import urllib.parse
import hmac
from pathlib import Path

import studio_core as core

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

CFG = core.load_config()
HOST = str(CFG["server"]["host"])
PORT = int(CFG["server"]["port"])
STATIC_DIR = core.BASE_DIR / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)

SESSION_TOKEN = secrets.token_urlsafe(32)
MAX_BODY = 256 * 1024
MAX_AUDIO_UPLOAD = 1024 ** 3
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
ALLOWED_HOSTS = LOCAL_HOSTS | {h.lower() for h in CFG["server"].get("allowed_hosts", [])}

CSP = (
    "default-src 'self'; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src https://fonts.gstatic.com; "
    "img-src 'self' data: blob:; "
    "media-src 'self' blob:; "
    "connect-src 'self'; "
    "object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)


class UnsupportedMedia(Exception):
    """Content-Type distinto de application/json."""


def _host_only(value: str) -> str:
    value = (value or "").strip().lower()
    if value.startswith("["):
        return value[1:value.find("]")] if "]" in value else value
    return value.split(":")[0]


class StudioRequestHandler(http.server.SimpleHTTPRequestHandler):
    server_version = "DanyStudio/2.4"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    # -- respuestas ---------------------------------------------------------
    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", CSP)
        # Archivos estaticos: que el navegador revalide siempre. Sin esto puede seguir usando un
        # app.js viejo en cache despues de actualizar la app, y la interfaz "no funciona".
        if not any(h.lower().startswith(b"cache-control:") for h in getattr(self, "_headers_buffer", [])):
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), (fmt % args).split("?")[0]))
        sys.stderr.flush()

    # -- seguridad ----------------------------------------------------------
    def _origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        if origin is None:
            return self.headers.get("Sec-Fetch-Site") in (None, "same-origin", "none")
        try:
            u = urllib.parse.urlparse(origin)
            return (
                u.scheme == "http"
                and (u.hostname or "").lower() in ALLOWED_HOSTS
                and (u.port or 80) == PORT
            )
        except ValueError:
            return False

    def _guard(self, need_token=True) -> bool:
        if _host_only(self.headers.get("Host", "")) not in ALLOWED_HOSTS:
            self._send_json({"error": "Host no permitido"}, 403)
            return False
        if not self._origin_ok():
            self._send_json({"error": "Origen no permitido"}, 403)
            return False
        if need_token:
            supplied = self.headers.get("X-Studio-Token", "")
            if not supplied and hasattr(self, "_query"):
                supplied = (self._query.get("token") or [""])[0]
            if not hmac.compare_digest(supplied.encode("utf-8"), SESSION_TOKEN.encode("utf-8")):
                self._send_json({"error": "Token de sesion invalido o ausente"}, 401)
                return False
        return True

    def _read_json(self) -> dict:
        if self.headers.get_content_type() != "application/json":
            raise UnsupportedMedia("Content-Type debe ser application/json")
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            raise ValueError("Content-Length no valido")
        if length < 0 or length > MAX_BODY:
            raise ValueError("Cuerpo demasiado grande")
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        try:
            payload = json.loads(raw)
        except ValueError:
            raise ValueError("JSON mal formado")
        if not isinstance(payload, dict):
            raise ValueError("Se esperaba un objeto JSON")
        return payload

    def _master_uploaded_audio(self):
        if self.headers.get_content_type() != "application/octet-stream":
            raise UnsupportedMedia("Content-Type debe ser application/octet-stream")
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            raise ValueError("Content-Length no valido")
        if length <= 0:
            raise ValueError("El archivo esta vacio")
        if length > MAX_AUDIO_UPLOAD:
            raise ValueError("El archivo supera el limite de 1 GB")

        file_name = urllib.parse.unquote(self.headers.get("X-File-Name", ""), errors="replace")
        file_name = file_name.replace("\\", "/").rsplit("/", 1)[-1]
        extension = Path(file_name).suffix.lower()
        if extension not in core.AUDIO_EXTS:
            raise ValueError(f"Formato no admitido. Usa: {', '.join(sorted(core.AUDIO_EXTS))}")

        output_dir = core.get_paths(CFG)["outputs"]
        output_dir.mkdir(parents=True, exist_ok=True)
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                prefix=".studio_upload_", suffix=extension, dir=output_dir, delete=False
            ) as uploaded:
                temp_path = Path(uploaded.name)
                remaining = length
                while remaining:
                    chunk = self.rfile.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise ValueError("La subida del archivo quedo incompleta")
                    uploaded.write(chunk)
                    remaining -= len(chunk)

            stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", Path(file_name).stem).strip(" .")[:100]
            stem = stem or "audio"
            output_path = output_dir / f"{stem}_EBUR128_master.mp3"
            suffix = 2
            while output_path.exists():
                output_path = output_dir / f"{stem}_EBUR128_master_{suffix}.mp3"
                suffix += 1

            result = core.master_audio(str(temp_path), str(output_path), CFG)
            result["input_name"] = file_name
            return result
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    def _run(self, fn):
        try:
            self._send_json(fn())
        except UnsupportedMedia as exc:
            self._send_json({"error": str(exc)}, 415)
        except core.LLMError as exc:
            self._send_json({"error": str(exc)}, 502)
        except PermissionError as exc:
            self._send_json({"error": str(exc)}, 403)
        except FileNotFoundError as exc:
            self._send_json({"error": str(exc)}, 404)
        except ValueError as exc:
            self._send_json({"error": str(exc)}, 400)
        except subprocess.TimeoutExpired:
            self._send_json({"error": "La operacion excedio el tiempo maximo"}, 504)
        except RuntimeError as exc:
            self._send_json({"error": str(exc)}, 500)
        except Exception:
            traceback.print_exc()
            self._send_json({"error": "Error interno (revisa la consola del servidor)"}, 500)

    def _serve_media(self, raw_path, resolver, mime_map):
        """GET de audio/video ya generado: valida la ruta con `resolver` y transmite los bytes con soporte Range/206 para streaming."""
        try:
            f = resolver(raw_path)
        except UnsupportedMedia as exc:
            self._send_json({"error": str(exc)}, 415)
            return
        except core.LLMError as exc:
            self._send_json({"error": str(exc)}, 502)
            return
        except PermissionError as exc:
            self._send_json({"error": str(exc)}, 403)
            return
        except FileNotFoundError as exc:
            self._send_json({"error": str(exc)}, 404)
            return
        except ValueError as exc:
            self._send_json({"error": str(exc)}, 400)
            return
        except Exception:
            traceback.print_exc()
            self._send_json({"error": "Error interno (revisa la consola del servidor)"}, 500)
            return
        try:
            total_size = f.stat().st_size
        except OSError as exc:
            self._send_json({"error": str(exc)}, 404)
            return

        ctype = mime_map.get(f.suffix.lower(), "application/octet-stream")
        range_header = self.headers.get("Range")
        byte_start = 0
        byte_end = total_size - 1

        if range_header and range_header.startswith("bytes="):
            try:
                parts = range_header[6:].split("-")
                if parts[0].strip():
                    byte_start = int(parts[0].strip())
                if len(parts) > 1 and parts[1].strip():
                    byte_end = int(parts[1].strip())
            except ValueError:
                byte_start = 0
                byte_end = total_size - 1

            if byte_start >= total_size or byte_end >= total_size or byte_start > byte_end:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{total_size}")
                self.end_headers()
                return

            chunk_len = byte_end - byte_start + 1
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {byte_start}-{byte_end}/{total_size}")
            self.send_header("Content-Length", str(chunk_len))
        else:
            self.send_response(200)
            self.send_header("Content-Length", str(total_size))

        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-cache")

        ascii_name = f.name.encode("ascii", "replace").decode("ascii").replace('"', "'").replace("?", "_")
        self.send_header(
            "Content-Disposition",
            f"inline; filename=\"{ascii_name}\"; filename*=UTF-8''{urllib.parse.quote(f.name)}",
        )
        self.end_headers()

        try:
            with open(f, "rb") as fh:
                if byte_start > 0:
                    fh.seek(byte_start)
                bytes_left = byte_end - byte_start + 1
                buf_size = 64 * 1024
                while bytes_left > 0:
                    read_len = min(buf_size, bytes_left)
                    data = fh.read(read_len)
                    if not data:
                        break
                    self.wfile.write(data)
                    bytes_left -= len(data)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass  # el navegador cambio de pista o salto de posicion

    # -- metodos ------------------------------------------------------------
    def do_OPTIONS(self):
        # Sin CORS: no se anuncia ningun origen permitido.
        self.send_response(204)
        self.send_header("Allow", "GET, POST")
        self.end_headers()

    def do_GET(self):
        try:
            self._do_GET_impl()
        except Exception:
            traceback.print_exc(file=sys.stderr)
            sys.stderr.flush()
            try:
                self._send_json({"error": "Error interno"}, 500)
            except Exception:
                pass

    def _do_GET_impl(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        self._query = query

        if not path.startswith("/api/"):
            if _host_only(self.headers.get("Host", "")) not in ALLOWED_HOSTS:
                self._send_json({"error": "Host no permitido"}, 403)
                return
            super().do_GET()
            return

        if path == "/api/session":
            if self._guard(need_token=False):
                self._send_json({"token": SESSION_TOKEN, "version": core.VERSION})
            return

        # Transmision binaria de imagen (admite carga directa desde <img> en navegador)
        if path == "/api/image/stream":
            if not self._guard(need_token=False):
                return
            self._serve_media(query.get("path", [""])[0], core.resolve_image_file, core.IMAGE_MIME)
            return

        if not self._guard():
            return

        # Transmision binaria de audio/video ya generado (no encaja en _send_json)
        if path == "/api/audio/stream":
            self._serve_media(query.get("path", [""])[0], core.resolve_audio_file, core.AUDIO_MIME)
            return
        if path == "/api/video/stream":
            self._serve_media(query.get("path", [""])[0], core.resolve_video_file, core.VIDEO_MIME)
            return

        routes = {
            "/api/system_info": lambda: core.system_info(),
            "/api/presets": lambda: core.get_presets(),
            "/api/llm/config": lambda: core.llm_public_config(),
            "/api/colab/policy": lambda: core.colab_policy(),
            "/api/colab/jobs": lambda: core.colab_jobs(),
            "/api/colab/status": lambda: core.colab_job_status(query.get("id", [""])[0]),
            "/api/audio/list": lambda: core.list_audio_tracks(),
            "/api/video/list": lambda: core.list_video_files(),
            "/api/cover/list": lambda: core.list_cover_images(),
            "/api/video/status": lambda: core.video_status(query.get("prompt_id", [""])[0]),
            "/api/video/minimax_status": lambda: core.video_status(query.get("prompt_id", [""])[0]),
            "/api/cover/status": lambda: core.cover_image_status(query.get("prompt_id", [""])[0]),
            "/api/acestep/status": lambda: core.acestep_job_status(query.get("job_id", [""])[0]),
            "/api/yue2/status": lambda: core.yue2_status(),
            "/api/yue2/loras": lambda: core.list_yue2_loras(),
            "/api/yue2/job": lambda: core.yue2_job_status(query.get("job_id", [""])[0]),
            "/api/nim/test": lambda: core.nim_test(),
            "/api/comfy/image": lambda: core.serve_comfy_image(query.get("seed", [""])[0]),
        }
        if path in routes:
            self._run(routes[path])
        else:
            self._send_json({"error": "Ruta no encontrada"}, 404)

    def do_POST(self):
        try:
            self._do_POST_impl()
        except Exception:
            traceback.print_exc(file=sys.stderr)
            sys.stderr.flush()
            try:
                self._send_json({"error": "Error interno"}, 500)
            except Exception:
                pass

    def _do_POST_impl(self):
        path = urllib.parse.urlparse(self.path).path
        if not path.startswith("/api/"):
            self._send_json({"error": "Ruta no encontrada"}, 404)
            return
        if not self._guard():
            return

        if path == "/api/master_audio_upload":
            self._run(self._master_uploaded_audio)
            return

        def dispatch():
            payload = self._read_json()
            if path == "/api/generate_music_config":
                return core.create_music_config(payload)
            if path == "/api/ai/compose":
                return core.compose(payload.get("idea", ""), payload.get("style", "auto"))
            if path == "/api/ai/video_prompt":
                return core.generate_video_prompt(payload)
            if path == "/api/ai/video_scenes":
                return core.generate_video_scenes(payload)
            if path == "/api/master_audio":
                return core.master_audio(payload.get("input_file", ""), payload.get("output_file"))
            if path in ("/api/video/generate", "/api/video/minimax_generate"):
                return core.generate_video(payload)
            if path == "/api/video/concat":
                return core.concat_video_clips(payload)
            if path == "/api/acestep/run":
                return core.run_acestep_local(payload)
            if path == "/api/yue2/run":
                return core.yue2_generate(payload)
            if path == "/api/yue2/compose":
                return core.yue2_compose(payload)
            if path == "/api/yue2/compose_and_run":
                return core.yue2_compose_and_run(payload)
            if path in ("/api/cover/generate", "/api/ai/cover-image"):
                return core.generate_cover_image(payload)
            if path == "/api/colab/dispatch":
                if core.load_config()["colab"]["require_confirm"] and payload.get("confirm") is not True:
                    raise PermissionError("Confirmacion explicita requerida para enviar a Colab")
                return core.dispatch_colab_job(payload.get("command", ""), payload.get("id"), source="ui")
            if path == "/api/llm/config":
                return core.save_llm_config(payload)
            if path == "/api/llm/models":
                return core.llm_models(payload)
            if path == "/api/llm/test":
                return core.llm_test(payload)
            raise FileNotFoundError("Ruta no encontrada")

        self._run(dispatch)


def run_server():
    core.ensure_dirs(CFG)
    core.load_secrets()

    print("=" * 65)
    print(" DANY AI STUDIO HUB v%s - CONTROL CENTRAL" % core.VERSION)
    print("=" * 65)
    try:
        httpd = http.server.ThreadingHTTPServer((HOST, PORT), StudioRequestHandler)
    except OSError as exc:
        print(f" [ERROR] No se pudo abrir {HOST}:{PORT} -> {exc}")
        print(" Cierra la otra instancia (o cambia server.port en config.json).")
        sys.exit(1)

    print(f" Servidor activo en: http://localhost:{PORT}  (escuchando en {HOST})")
    print(f" Interfaz: {STATIC_DIR}")
    bridge = core.get_paths(CFG)["bridge"]
    print(f" Bridge Colab: {bridge if bridge else '(no configurado)'}")
    if HOST not in ("127.0.0.1", "localhost", "::1"):
        print(" [AVISO] Escuchas fuera de localhost. Cualquiera en tu red podra intentar acceder;")
        print("         anade tu host a server.allowed_hosts y protege el puerto con firewall.")
    print(" Presiona Ctrl+C para detener el servidor.")
    print("=" * 65)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nDeteniendo Dany AI Studio...")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    run_server()
