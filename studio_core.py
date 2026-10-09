#!/usr/bin/env python3
"""
DANY AI STUDIO - Nucleo compartido (v2.1)

Lo usan studio_server.py (interfaz web) y mcp_server.py (agentes IA), asi que
ambos se comportan igual. Contiene:
  - Configuracion (config.json) y secretos (secrets.json)
  - Validacion de rutas / nombres / numeros
  - Generacion de TOML valido para ACE-Step 1.5 (con escapado correcto)
  - Masterizacion EBU R128 de 2 pasadas (FFmpeg)
  - Colab Bridge con trabajos FIRMADOS (HMAC-SHA256), allowlist y auditoria
  - Conector LLM manual (Ollama o cualquier servidor compatible con OpenAI)
  - Compositor: usa el LLM si esta conectado; si no, plantillas de respaldo

IMPORTANTE: este modulo nunca imprime en stdout al importarse (el servidor MCP
usa stdout para JSON-RPC). Solo el bloque __main__ imprime.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import math
import os
import re
import secrets
import shlex
import shutil
import subprocess
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from pathlib import Path

VERSION = "2.7"
BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.json"
SECRETS_FILE = BASE_DIR / "secrets.json"
LOG_DIR = BASE_DIR / "logs"

_lock = threading.RLock()
_HOME = Path.home()


class LLMError(Exception):
    """Fallo al hablar con el modelo conectado manualmente."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------------------
# Configuracion y secretos
# ---------------------------------------------------------------------------

DEFAULT_CONFIG = {
    "server": {"host": "127.0.0.1", "port": 7877, "allowed_hosts": []},
    "paths": {
        "acestep_dir": "",
        "comfy_dir": "",
        "colab_bridge_dir": "",
        "outputs_dir": str(_HOME / "Music" / "Nuevas_Producciones_Agente"),
        "shorts_dirs": [],
        "extra_audio_roots": [str(_HOME / "Music")],
    },
    "music": {
        "lm_model": "acestep-5Hz-lm-1.7B",
        "inference_steps": 8,
        "guidance_scale": 7.0,
        "backend": "pt",
        "audio_format": "flac",
        "lora_paths": {},
    },
    "mastering": {"target_lufs": -11.0, "true_peak": -0.5, "lra": 7.0, "bitrate": "320k"},
    "yue2": {
        "dir": "",
        "cot": "full",
        "output_dir": "",
    },
    "llm": {
        "enabled": False,
        "provider": "ollama",
        "base_url": "http://127.0.0.1:11434",
        "model": "",
        "temperature": 0.8,
        "timeout_s": 120,
    },
    "colab": {
        "require_confirm": True,
        "max_age_s": 600,
        "max_dispatch_per_min": 6,
        "allow_mcp_dispatch": False,
        "allowed_executables": ["python", "python3"],
        "allowed_scripts": [
            "/content/*.py",
            "/content/ACE-Step-1.5/*.py",
            "/content/ComfyUI/*.py",
            "/content/drive/MyDrive/Produccion_Musical/Colab_Bridge/scripts/*.py",
        ],
    },
}


def _merge(base, over):
    out = json.loads(json.dumps(base))
    for key, value in (over or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def _atomic_write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def _restrict_file(path: Path):
    """Best-effort: solo tu usuario puede leer el archivo (Windows: icacls)."""
    try:
        if os.name == "nt":
            user = os.environ.get("USERNAME")
            if user:
                subprocess.run(
                    ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:F"],
                    capture_output=True, timeout=10,
                )
        else:
            os.chmod(path, 0o600)
    except Exception:
        pass


def load_config() -> dict:
    with _lock:
        data = {}
        if CONFIG_FILE.exists():
            try:
                data = json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError):
                data = {}
        return _merge(DEFAULT_CONFIG, data)


def save_config(cfg: dict):
    with _lock:
        _atomic_write(CONFIG_FILE, json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")


def load_secrets() -> dict:
    with _lock:
        data = {}
        if SECRETS_FILE.exists():
            try:
                data = json.loads(SECRETS_FILE.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError):
                data = {}
        if not isinstance(data, dict):
            data = {}
        if not data.get("colab_hmac_secret"):
            data["colab_hmac_secret"] = secrets.token_urlsafe(32)
            _atomic_write(SECRETS_FILE, json.dumps(data, indent=2) + "\n")
            _restrict_file(SECRETS_FILE)
        return data


def save_secrets(data: dict):
    with _lock:
        _atomic_write(SECRETS_FILE, json.dumps(data, indent=2) + "\n")
        _restrict_file(SECRETS_FILE)


def _opt_path(value):
    if isinstance(value, str) and value.strip():
        return Path(value.strip())
    return None


def get_paths(cfg=None) -> dict:
    cfg = cfg or load_config()
    p = cfg["paths"]
    return {
        "acestep": _opt_path(p.get("acestep_dir")),
        "comfy": _opt_path(p.get("comfy_dir")),
        "bridge": _opt_path(p.get("colab_bridge_dir")),
        "outputs": Path(p["outputs_dir"]),
        "shorts": [Path(x) for x in p.get("shorts_dirs", []) if x],
        "extra_roots": [Path(x) for x in p.get("extra_audio_roots", []) if x],
    }


def audio_roots(cfg=None) -> list:
    pp = get_paths(cfg)
    return [pp["outputs"], *pp["shorts"], BASE_DIR / "outputs", *pp["extra_roots"]]


def ensure_dirs(cfg=None):
    pp = get_paths(cfg)
    for d in [pp["outputs"], *pp["shorts"], BASE_DIR / "outputs", LOG_DIR]:
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass


def audit(event: str, data: dict):
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"t": time.strftime("%Y-%m-%d %H:%M:%S"), "event": event, **data}, ensure_ascii=False)
        with _lock, open(LOG_DIR / "audit.log", "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Validacion
# ---------------------------------------------------------------------------

_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
JOB_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MODEL_TIER_RE = re.compile(r"^[A-Za-z0-9._-]{1,80}$")
LORA_RE = re.compile(r"^[\w .\-]{1,80}$", re.UNICODE)
LLM_MODEL_RE = re.compile(r"^[\w.:/@+\-]{1,200}$", re.UNICODE)
KEY_RE = re.compile(r"^([A-Ga-g])([#b\u266f\u266d]?)\s*(major|minor|maj|min)$", re.IGNORECASE)
LANG_RE = re.compile(r"^(unknown|[a-z]{2,3}(-[A-Za-z]{2,4})?)$")
AUDIO_EXTS = {".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac", ".wma", ".opus"}


def fold(text: str) -> str:
    """Minusculas y sin acentos."""
    return "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))


def slugify(text: str, default="pista", maxlen=60) -> str:
    s = re.sub(r"[^a-z0-9_-]+", "_", fold(str(text or "")).replace(" ", "_"))
    s = re.sub(r"_+", "_", s).strip("_-")[:maxlen].strip("_-")
    if not s or s in _RESERVED:
        return default
    return s


def is_within(path: Path, roots) -> bool:
    try:
        resolved = Path(path).resolve()
    except (OSError, RuntimeError):
        return False
    for root in roots:
        try:
            resolved.relative_to(Path(root).resolve())
            return True
        except (ValueError, OSError):
            continue
    return False


def normalize_key(text: str) -> str:
    """Devuelve la tonalidad en el formato exacto que valida ACE-Step (ej. 'E minor', 'F# minor')."""
    m = KEY_RE.match(str(text or "").strip())
    if not m:
        raise ValueError("key no valida (ej: 'E minor', 'G major', 'F# minor')")
    note, acc, mode = m.groups()
    return f"{note.upper()}{acc} {'major' if mode.lower().startswith('maj') else 'minor'}"


def _num(payload, key, default, lo, hi, cast=float):
    raw = payload.get(key, default)
    if raw is None or raw == "":
        raw = default
    try:
        value = cast(raw)
    except (TypeError, ValueError):
        raise ValueError(f"'{key}' debe ser numerico")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"'{key}' no es un numero valido")
    if not (lo <= value <= hi):
        raise ValueError(f"'{key}' fuera de rango ({lo} a {hi})")
    return value


def _clamp(value, lo, hi, default, cast=float):
    try:
        v = cast(value)
        if isinstance(v, float) and not math.isfinite(v):
            return default
        return max(lo, min(hi, v))
    except (TypeError, ValueError):
        return default


def _bool(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "si", "on")
    return bool(value)


# ---------------------------------------------------------------------------
# TOML (ACE-Step 1.5) - escapado correcto
# ---------------------------------------------------------------------------

def _toml_escape_char(ch: str, allow_newline_tab: bool) -> str:
    o = ord(ch)
    if ch == "\\":
        return "\\\\"
    if ch == '"':
        return '\\"'
    if allow_newline_tab and ch in "\n\t":
        return ch
    if ch == "\n":
        return "\\n"
    if ch == "\t":
        return "\\t"
    if ch == "\r":
        return "\\r"
    if o < 0x20 or o == 0x7F:
        return f"\\u{o:04x}"
    return ch


def toml_str(value) -> str:
    return '"' + "".join(_toml_escape_char(c, False) for c in str(value)) + '"'


def toml_multiline(value) -> str:
    s = str(value).replace("\r\n", "\n").replace("\r", "\n")
    return '"""\n' + "".join(_toml_escape_char(c, True) for c in s) + '"""'


_INSTR_WORDS = (
    "guitar|dobro|bass|drum|piano|solo|synth|instrumental|slide|strings|fade|beat|riff|"
    "organ|violin|sax|horn|intro|outro|verse|chorus|bpm|kick|snare|pad"
)
_INSTR_RE = re.compile(rf"\b({_INSTR_WORDS})", re.IGNORECASE)
_BPM_RE = re.compile(r"\b\d{2,3}\s*bpm\b", re.IGNORECASE)


def lint_lyrics(text: str):
    """Quita acotaciones tecnicas (BPM, '(guitar solo...)') que la IA intentaria cantar."""
    removed, kept = [], []
    for i, line in enumerate(str(text).replace("\r\n", "\n").replace("\r", "\n").split("\n"), 1):
        s = line.strip()
        parenthesized = s.startswith("(") and s.endswith(")")
        if s and (_BPM_RE.search(s) or (parenthesized and _INSTR_RE.search(s))):
            removed.append({"line": i, "text": s[:120]})
            continue
        kept.append(line.rstrip())
    cleaned = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()
    return cleaned, removed



def detect_loras(cfg=None) -> list:
    """Devuelve la lista de LoRAs disponibles en lora_models."""
    ace = get_paths(cfg)["acestep"]
    names = []
    if ace and (ace / "lora_models").is_dir():
        for d in sorted((ace / "lora_models").iterdir()):
            if d.is_dir() and (
                (d / "final" / "adapter_model.safetensors").exists()
                or (d / "adapter_model.safetensors").exists()
                or any(d.glob("*.safetensors"))
            ):
                names.append(d.name)
    return names


def _lora_in_dim(lora_path):
    """Dimension de entrada de la LoRA (= hidden_size del DiT para el que se entreno) leyendo la cabecera safetensors."""
    import struct
    p = Path(lora_path)
    f = p if p.is_file() else p / "adapter_model.safetensors"
    if not f.exists() and p.is_dir():
        cands = list(p.glob("*.safetensors"))
        if cands:
            f = cands[0]
        else:
            return None
    try:
        with open(f, "rb") as fh:
            n = struct.unpack("<Q", fh.read(8))[0]
            if n > 50_000_000:
                return None
            header = json.loads(fh.read(n))
        for key, meta in header.items():
            if "lora_A" in key and key.endswith("weight") and "shape" in meta and len(meta["shape"]) >= 2:
                return int(meta["shape"][-1])
    except (OSError, ValueError, KeyError, IndexError, struct.error):
        pass
    return None


def _tier_hidden(tier, cfg):
    ace = get_paths(cfg)["acestep"]
    if not ace:
        return None
    try:
        return int(json.loads((ace / "checkpoints" / tier / "config.json").read_text(encoding="utf-8"))["hidden_size"])
    except (OSError, ValueError, KeyError):
        return None


def _available_tiers(cfg):
    ace = get_paths(cfg)["acestep"]
    if not ace or not (ace / "checkpoints").is_dir():
        return []
    return sorted(d.name for d in (ace / "checkpoints").iterdir() if d.name.startswith("acestep-v15") and (d / "config.json").exists())


def resolve_lora_path(name: str, cfg=None, target_tier=None):
    """(v2.5) Devuelve la ruta REAL del adaptador (lora_path de ACE-Step) o None.
    Si se indica target_tier y la variante base no coincide en dimension con el,
    prueba variantes bidireccionales (_xl <-> base) antes de rendirse."""
    if not name:
        return None
    cfg = cfg or load_config()
    ace = get_paths(cfg)["acestep"]
    mapped = (cfg["music"].get("lora_paths") or {}).get(name)

    def _find_folder(cand_name):
        if not ace:
            return None
        slug = re.sub(r"[^a-z0-9]+", "_", cand_name.lower()).strip("_")
        for cn in (cand_name, slug):
            cand = ace / "lora_models" / cn
            for p in (cand / "final", cand):
                if (p / "adapter_model.safetensors").exists() or any(p.glob("*.safetensors")):
                    return p
        return None

    def _mapped_path(value):
        p = Path(value)
        if not p.is_absolute() and ace:
            p = ace / "lora_models" / value
        return p if p.exists() else None

    base = _mapped_path(mapped) if mapped else _find_folder(name)

    target_dim = _tier_hidden(target_tier, cfg) if target_tier else None
    if target_dim is None:
        return base
    if base is not None and _lora_in_dim(base) == target_dim:
        return base

    candidates = []
    for raw in ([name, mapped] if mapped else [name]):
        raw = raw.strip().strip("/\\")
        raw = Path(raw).name if ("/" in raw or "\\" in raw) else raw
        if target_dim == 2560:
            candidates.append(f"{raw}_xl")
            m = re.match(r"^(.*)_v(\d+)$", raw)
            if m:
                candidates.append(f"{m.group(1)}_xl_v{m.group(2)}")
        elif target_dim == 2048:
            no_xl = re.sub(r"_xl(_v\d+)?$", r"", raw)
            if no_xl != raw:
                candidates.append(no_xl)
            no_xl2 = raw.replace("_xl", "")
            if no_xl2 not in candidates:
                candidates.append(no_xl2)

    for cand_name in candidates:
        alt = _find_folder(cand_name)
        if alt is not None and _lora_in_dim(alt) == target_dim:
            return alt

    return base


def create_music_config(payload: dict, cfg=None) -> dict:
    """(v2.5) Genera un archivo TOML de configuracion para ACE-Step 1.5.
    Escribe backend = 'pt' por defecto para evitar agotamiento de KV cache en vLLM/nanovllm."""
    cfg = cfg or load_config()
    music = cfg["music"]
    out_dir = get_paths(cfg)["outputs"]
    out_dir.mkdir(parents=True, exist_ok=True)

    title = slugify(payload.get("title", ""), default="nueva_pista")
    caption = str(payload.get("caption", "")).strip()
    if not caption:
        raise ValueError("El caption (prompt maestro) no puede estar vacio")
    if len(caption) > 2000:
        raise ValueError("El caption es demasiado largo (max. 2000 caracteres)")
    lyrics_raw = str(payload.get("lyrics", "")).strip()
    if len(lyrics_raw) > 20000:
        raise ValueError("La letra es demasiado larga (max. 20000 caracteres)")
    lyrics, removed = lint_lyrics(lyrics_raw)

    duration = _num(payload, "duration", 200.0, 10.0, 600.0)
    seed = _num(payload, "seed", 42, 0, 2**31 - 1, cast=int)
    lora_scale = _num(payload, "lora_scale", 1.0, 0.0, 2.0)
    model_tier = str(payload.get("model_tier") or "acestep-v15-xl-turbo").strip()
    if not MODEL_TIER_RE.match(model_tier):
        raise ValueError("model_tier no valido (solo letras, numeros, . _ -)")
    lora_name = str(payload.get("lora_name") or "").strip()
    if lora_name and not LORA_RE.match(lora_name):
        raise ValueError("lora_name no valido")

    voice_lora_name = str(payload.get("voice_lora_name") or "").strip()
    if voice_lora_name and not LORA_RE.match(voice_lora_name):
        raise ValueError("voice_lora_name no valido")
    voice_lora_scale = _num(payload, "voice_lora_scale", 1.0, 0.0, 2.0)

    bpm = payload.get("bpm")
    if bpm not in (None, ""):
        bpm = _num(payload, "bpm", 120, 30, 300, cast=int)
    else:
        bpm = None
    key = str(payload.get("key") or "").strip()
    if key:
        key = normalize_key(key)
    language = str(payload.get("vocal_language") or "").strip()
    if language and not LANG_RE.match(language):
        raise ValueError("vocal_language no valido (ej: 'es', 'en', 'ro')")

    backend = str(payload.get("backend") or music.get("backend") or "pt").strip()
    inference_steps = int(payload.get("inference_steps") or music.get("inference_steps", 8))
    guidance_scale = float(payload.get("guidance_scale") or music.get("guidance_scale", 7.0))
    lm_model = str(payload.get("lm_model") or music.get("lm_model", "acestep-5Hz-lm-1.7B")).strip()

    warnings = []
    lora_path = None
    if lora_name:
        lora_path = resolve_lora_path(lora_name, cfg, target_tier=model_tier)
        if lora_path is None:
            warnings.append(
                f"LoRA '{lora_name}' no encontrada en lora_models. Se genera SIN LoRA. "
                f"Mapea su ruta en config.json -> music.lora_paths."
            )
        else:
            lora_dim, tier_dim = _lora_in_dim(lora_path), _tier_hidden(model_tier, cfg)
            if lora_dim and tier_dim and lora_dim != tier_dim:
                alt = next((t for t in _available_tiers(cfg) if _tier_hidden(t, cfg) == lora_dim), None)
                if alt:
                    warnings.append(
                        f"La LoRA '{lora_name}' se entreno para un DiT de dimension {lora_dim} y '{model_tier}' usa {tier_dim}: "
                        f"no son compatibles. Motor cambiado a '{alt}'."
                    )
                    model_tier = alt
                else:
                    warnings.append(f"La LoRA '{lora_name}' no es compatible con '{model_tier}'. Se genera SIN LoRA.")
                    lora_path = None

    voice_lora_path = None
    if voice_lora_name:
        voice_lora_path = resolve_lora_path(voice_lora_name, cfg, target_tier=model_tier)
        if voice_lora_path is None:
            warnings.append(
                f"Voice LoRA '{voice_lora_name}' no encontrada en lora_models. Se genera sin Voice LoRA."
            )
        else:
            v_lora_dim, tier_dim = _lora_in_dim(voice_lora_path), _tier_hidden(model_tier, cfg)
            if v_lora_dim and tier_dim and v_lora_dim != tier_dim:
                warnings.append(
                    f"La Voice LoRA '{voice_lora_name}' se entreno para dimension {v_lora_dim} y '{model_tier}' usa {tier_dim}. Se genera sin Voice LoRA."
                )
                voice_lora_path = None

    lines = [
        "# Generado por Dany AI Studio",
        'task_type = "text2music"',
        f"caption = {toml_str(caption)}",
        f"lyrics = {toml_multiline(lyrics)}",
        f"duration = {duration}",
        f"instrumental = {'true' if _bool(payload.get('instrumental', False)) else 'false'}",
        "thinking = false",
        f"config_path = {toml_str(model_tier)}",
        f"lm_model_path = {toml_str(lm_model)}",
        f"backend = {toml_str(backend)}",
        f"inference_steps = {inference_steps}",
        f"guidance_scale = {guidance_scale}",
        f"seed = {seed}",
        f"use_random_seed = {'true' if _bool(payload.get('random_seed', False)) else 'false'}",
        f"audio_format = {toml_str(music.get('audio_format', 'flac'))}",
        f"save_dir = {toml_str(out_dir.as_posix())}",
    ]
    if bpm is not None:
        lines.append(f"bpm = {bpm}")
    if key:
        lines.append(f"keyscale = {toml_str(key)}")
    if language:
        lines.append(f"vocal_language = {toml_str(language)}")
    if lora_path is not None:
        lines.append(f"lora_path = {toml_str(Path(lora_path).as_posix())}")
        lines.append(f"lora_scale = {lora_scale}")
    if voice_lora_path is not None:
        lines.append(f"voice_lora_path = {toml_str(Path(voice_lora_path).as_posix())}")
        lines.append(f"voice_lora_scale = {voice_lora_scale}")

    toml_file = out_dir / f"{title}.toml"
    _atomic_write(toml_file, "\n".join(lines) + "\n")

    ace = get_paths(cfg)["acestep"]
    cli_command = None
    if ace:
        py = ace / ".venv" / "Scripts" / "python.exe"
        py_txt = f'"{py}"' if py.exists() else "python"
        cli_command = f'cd /d "{ace}" && {py_txt} cli.py -c "{toml_file}"'

    audit("music_config", {"file": str(toml_file), "lora": lora_name or None, "lora_found": lora_path is not None, "voice_lora": voice_lora_name or None, "voice_lora_found": voice_lora_path is not None})
    return {
        "status": "CONFIG_CREATED",
        "toml_path": str(toml_file),
        "title": title,
        "duration": duration,
        "model_tier": model_tier,
        "lora_path": str(lora_path) if lora_path else None,
        "voice_lora_path": str(voice_lora_path) if voice_lora_path else None,
        "cli_command": cli_command,
        "lyrics_removed": removed,
        "warnings": warnings,
    }


def detect_gpu() -> dict:
    try:
        res = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,memory.free", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, check=True, timeout=8,
        )
        first = res.stdout.strip().splitlines()[0]
        parts = [p.strip() for p in first.split(",")]
        total_mb, free_mb = float(parts[1]), float(parts[2])
        return {
            "detected": True,
            "name": parts[0],
            "total_gb": round(total_mb / 1024, 2),
            "free_gb": round(free_mb / 1024, 2),
            "tier": "Tier 6a (Alta gama >= 16GB)" if total_mb >= 15000 else "Tier Estandar (< 16GB)",
            "can_run_xl_local": total_mb >= 14000,
            "recommend_colab": total_mb < 12000,
        }
    except Exception:
        return {
            "detected": False, "name": "GPU no detectada o integrada", "total_gb": 0, "free_gb": 0,
            "tier": "Modo Nube (Colab Bridge recomendado)", "can_run_xl_local": False, "recommend_colab": True,
        }


def system_info(cfg=None) -> dict:
    cfg = cfg or load_config()
    pp = get_paths(cfg)

    def s(p):
        return str(p) if p else ""

    return {
        "gpu": detect_gpu(),
        "paths": {
            "acestep_local": s(pp["acestep"]),
            "acestep_available": bool(pp["acestep"] and (pp["acestep"] / "cli.py").exists()),
            "comfy_local": s(pp["comfy"]),
            "comfy_available": bool(pp["comfy"] and (pp["comfy"] / "main.py").exists()),
            "colab_bridge": s(pp["bridge"]),
            "bridge_available": bool(pp["bridge"] and pp["bridge"].is_dir()),
            "outputs_dir": s(pp["outputs"]),
        },
        "loras": detect_loras(cfg),
        "llm": llm_public_config(cfg),
        "colab": colab_policy(cfg),
        "server_time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "version": VERSION,
    }


# ---------------------------------------------------------------------------
# Masterizacion EBU R128 (2 pasadas)
# ---------------------------------------------------------------------------

def _run(cmd, timeout):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")


def _last_line(text: str) -> str:
    lines = [ln.strip() for ln in (text or "").strip().splitlines() if ln.strip()]
    return lines[-1][:300] if lines else ""


def _parse_loudnorm(stderr: str) -> dict:
    start, end = stderr.rfind("{"), stderr.rfind("}") + 1
    if start == -1 or end <= start:
        raise RuntimeError("FFmpeg no devolvio mediciones loudnorm")
    stats = json.loads(stderr[start:end])
    out = {}
    for key in ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset"):
        value = float(stats[key])
        if not math.isfinite(value):
            raise ValueError("El audio es silencioso o no se puede medir (loudness infinito)")
        out[key] = value
    return out


def master_audio(input_file, output_file=None, cfg=None) -> dict:
    cfg = cfg or load_config()
    m = cfg["mastering"]
    target_i = _clamp(m["target_lufs"], -24.0, -8.0, -11.0)
    target_tp = _clamp(m["true_peak"], -3.0, 0.0, -0.5)
    target_lra = _clamp(m["lra"], 1.0, 20.0, 7.0)
    bitrate = str(m.get("bitrate", "320k"))
    if not re.match(r"^\d{2,3}k$", bitrate):
        bitrate = "320k"

    raw = str(input_file or "").strip().strip('"')
    if not raw:
        raise ValueError("Indica la ruta del archivo de audio")
    src = Path(raw)
    if not src.is_absolute():
        raise ValueError("Usa una ruta absoluta (ej: C:\\Users\\...\\cancion.mp3)")
    src = src.resolve()
    if not src.is_file():
        raise FileNotFoundError(f"No existe el archivo: {src}")
    if src.suffix.lower() not in AUDIO_EXTS:
        raise ValueError(f"Extension no permitida ({src.suffix}). Validas: {', '.join(sorted(AUDIO_EXTS))}")
    roots = audio_roots(cfg)
    if not is_within(src, roots):
        raise PermissionError(
            "El archivo esta fuera de las carpetas permitidas. Anade su carpeta en "
            "config.json -> paths.extra_audio_roots"
        )

    if output_file:
        dst = Path(str(output_file).strip().strip('"'))
        if not dst.is_absolute():
            raise ValueError("output_file debe ser una ruta absoluta")
        dst = dst.resolve()
        if dst.suffix.lower() != ".mp3":
            raise ValueError("output_file debe terminar en .mp3")
        if not is_within(dst, roots):
            raise PermissionError("output_file esta fuera de las carpetas permitidas")
    else:
        dst = src.with_name(f"{src.stem}_EBUR128_master.mp3")
    if dst == src:
        raise ValueError("La salida no puede ser el mismo archivo que la entrada")

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise FileNotFoundError("FFmpeg no esta en el PATH")

    base = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-i", str(src), "-map", "0:a:0"]
    p1 = _run(
        base + ["-af", f"loudnorm=I={target_i}:TP={target_tp}:LRA={target_lra}:print_format=json", "-f", "null", "-"],
        900,
    )
    if p1.returncode != 0:
        raise RuntimeError(f"FFmpeg fallo en la pasada 1: {_last_line(p1.stderr)}")
    st = _parse_loudnorm(p1.stderr)

    flt = (
        f"loudnorm=I={target_i}:TP={target_tp}:LRA={target_lra}:"
        f"measured_I={st['input_i']:.2f}:measured_TP={st['input_tp']:.2f}:"
        f"measured_LRA={st['input_lra']:.2f}:measured_thresh={st['input_thresh']:.2f}:"
        f"offset={st['target_offset']:.2f}:linear=true"
    )
    tmp = dst.with_name(dst.stem + ".part.mp3")
    # -ar 44100: loudnorm remuestrea internamente a 192 kHz; sin esto el MP3 sale mal.
    p2 = _run(
        base + ["-map_metadata", "0", "-af", flt, "-ar", "44100", "-c:a", "libmp3lame", "-b:a", bitrate, str(tmp)],
        900,
    )
    if p2.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"FFmpeg fallo en la pasada 2: {_last_line(p2.stderr)}")
    os.replace(tmp, dst)

    audit("master_audio", {"input": str(src), "output": str(dst)})
    return {
        "status": "MASTERED",
        "output_file": str(dst),
        "target_lufs": target_i,
        "true_peak": target_tp,
        "bitrate": bitrate,
        "measured": {"input_lufs": st["input_i"], "input_true_peak": st["input_tp"], "input_lra": st["input_lra"]},
    }


# ---------------------------------------------------------------------------
# Colab Bridge - trabajos firmados
# ---------------------------------------------------------------------------

_dispatch_times = deque()


def _glob_match(pattern: str, path: str) -> bool:
    """'*' = cualquier cosa menos '/', '**' = cualquier cosa."""
    rx = re.escape(pattern).replace(r"\*\*", ".*").replace(r"\*", "[^/]*")
    return re.fullmatch(rx, path) is not None


def colab_policy(cfg=None) -> dict:
    col = (cfg or load_config())["colab"]
    return {
        "allowed_executables": list(col["allowed_executables"]),
        "allowed_scripts": list(col["allowed_scripts"]),
        "require_confirm": bool(col["require_confirm"]),
        "max_age_s": int(col["max_age_s"]),
        "allow_mcp_dispatch": bool(col["allow_mcp_dispatch"]),
    }


def validate_colab_command(command, cfg=None) -> list:
    col = (cfg or load_config())["colab"]
    if not isinstance(command, str) or not command.strip():
        raise ValueError("El comando no puede estar vacio")
    command = command.strip()
    if len(command) > 2000:
        raise ValueError("Comando demasiado largo (max. 2000 caracteres)")
    if re.search(r"[\x00-\x1f\x7f]", command):
        raise ValueError("El comando contiene saltos de linea o caracteres de control")
    try:
        argv = shlex.split(command, posix=True)
    except ValueError as exc:
        raise ValueError(f"Comillas sin cerrar: {exc}")
    if len(argv) < 2:
        raise ValueError("Formato: python3 /content/script.py [argumentos]")
    if len(argv) > 60 or any(len(a) > 1000 for a in argv):
        raise ValueError("Demasiados argumentos o argumento demasiado largo")
    if argv[0] not in col["allowed_executables"]:
        raise ValueError(f"Ejecutable no permitido: {argv[0]}. Permitidos: {', '.join(col['allowed_executables'])}")
    script = argv[1]
    if script.startswith("-") or ".." in script.split("/") or not script.endswith(".py"):
        raise ValueError("El segundo elemento debe ser una ruta a un script .py (sin -c, -m ni '..')")
    if not any(_glob_match(p, script) for p in col["allowed_scripts"]):
        raise ValueError(
            f"Script no permitido: {script}. Permitidos: {', '.join(col['allowed_scripts'])} "
            "(editable en config.json -> colab.allowed_scripts)"
        )
    return argv


def _canonical(job: dict) -> bytes:
    fields = {k: job.get(k) for k in ("v", "id", "action", "command", "timestamp", "nonce")}
    return json.dumps(fields, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sign_job(job: dict, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), _canonical(job), hashlib.sha256).hexdigest()


def dispatch_colab_job(command, job_id=None, source="ui", cfg=None) -> dict:
    cfg = cfg or load_config()
    col = cfg["colab"]
    if source == "mcp" and not col["allow_mcp_dispatch"]:
        raise PermissionError(
            "El envio a Colab desde agentes MCP esta desactivado. "
            "Activa colab.allow_mcp_dispatch en config.json si lo quieres (asume el riesgo de prompt injection)."
        )
    bridge = get_paths(cfg)["bridge"]
    if not bridge or not bridge.is_dir():
        raise FileNotFoundError("No se encuentra la carpeta Colab Bridge (paths.colab_bridge_dir en config.json)")

    argv = validate_colab_command(command, cfg)
    normalized = shlex.join(argv)

    if job_id in (None, ""):
        job_id = f"job_{int(time.time())}_{secrets.token_hex(2)}"
    job_id = str(job_id)
    if not JOB_ID_RE.match(job_id) or job_id.lower() in _RESERVED:
        raise ValueError("id no valido (solo letras, numeros, _ y -; max. 64)")

    now = time.time()
    with _lock:
        while _dispatch_times and now - _dispatch_times[0] > 60:
            _dispatch_times.popleft()
        if len(_dispatch_times) >= int(col["max_dispatch_per_min"]):
            raise PermissionError("Limite de envios por minuto alcanzado; espera un momento")
        _dispatch_times.append(now)

    job = {
        "v": 2, "id": job_id, "action": "run_command", "command": normalized,
        "timestamp": int(now), "nonce": secrets.token_hex(16),
    }
    job["sig"] = sign_job(job, load_secrets()["colab_hmac_secret"])

    jobs_dir = bridge / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    target = jobs_dir / f"{job_id}.json"
    if target.exists():
        raise ValueError("Ya existe un trabajo pendiente con ese id")
    tmp = jobs_dir / f"{job_id}.json.tmp"
    tmp.write_text(json.dumps(job, indent=2), encoding="utf-8")
    os.replace(tmp, target)  # atomico: Colab nunca lee un JSON a medias

    audit("colab_dispatch", {"job_id": job_id, "source": source, "command": normalized})
    return {"status": "DISPATCHED", "job_id": job_id, "job_file": str(target), "command": normalized}


def _tail(path: Path, n=3000) -> str:
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        fh.seek(max(0, size - n))
        return fh.read().decode("utf-8", "ignore")


def _bridge_or_raise(cfg):
    bridge = get_paths(cfg)["bridge"]
    if not bridge or not bridge.is_dir():
        raise FileNotFoundError("Carpeta Colab Bridge no encontrada (Google Drive de escritorio)")
    return bridge


def colab_job_status(job_id, cfg=None) -> dict:
    cfg = cfg or load_config()
    job_id = str(job_id or "")
    if not JOB_ID_RE.match(job_id):
        raise ValueError("id no valido")
    bridge = _bridge_or_raise(cfg)
    job_path = bridge / "jobs" / f"{job_id}.json"
    res_path = bridge / "results" / f"{job_id}.json"
    log_path = bridge / "logs" / f"{job_id}_cmd.log"

    status, result = "PENDING", None
    if res_path.exists():
        try:
            result = json.loads(res_path.read_text(encoding="utf-8"))
            status = str(result.get("status", "DONE"))
        except (OSError, ValueError) as exc:
            result, status = {"raw_error": str(exc)}, "ERROR"
    elif not job_path.exists():
        status = "UNKNOWN"
    log = ""
    if log_path.exists():
        try:
            log = _tail(log_path)
        except OSError:
            pass
    return {"id": job_id, "status": status, "result": result, "log": log}


def colab_jobs(cfg=None) -> dict:
    cfg = cfg or load_config()
    bridge = get_paths(cfg)["bridge"]
    if not bridge or not bridge.is_dir():
        return {"jobs": [], "available": False}
    items = {}
    for sub, default in (("results", None), ("jobs", "PENDING")):
        d = bridge / sub
        if not d.is_dir():
            continue
        for p in d.glob("*.json"):
            if not JOB_ID_RE.match(p.stem):
                continue
            status = default
            if sub == "results":
                try:
                    status = str(json.loads(p.read_text(encoding="utf-8")).get("status", "DONE"))
                except (OSError, ValueError):
                    status = "ERROR"
            mtime = p.stat().st_mtime
            if p.stem not in items or sub == "results":
                items[p.stem] = {"id": p.stem, "status": status, "mtime": mtime}
    ordered = sorted(items.values(), key=lambda x: x["mtime"], reverse=True)[:15]
    return {"jobs": [{"id": j["id"], "status": j["status"]} for j in ordered], "available": True}


# ---------------------------------------------------------------------------
# Conector LLM manual (Ollama / compatible OpenAI)
# ---------------------------------------------------------------------------

LLM_PROVIDERS = ("ollama", "openai", "nim")
NIM_DEFAULT_BASE = "https://integrate.api.nvidia.com/v1"
NIM_RECOMMENDED_MODELS = [
    "meta/llama-3.2-11b-vision-instruct",
    "nvidia/nemotron-3-ultra-550b-a55b",
    "meta/llama-3.2-90b-vision-instruct",
    "google/gemma-2b",
    "nvidia/nemotron-3-super-120b-a12b",
]
_NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_DEFAULT_OPENER = urllib.request.build_opener()



def _is_local_host(host: str) -> bool:
    if host in ("localhost",):
        return True
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_loopback or ip.is_private
    except ValueError:
        return False


def validate_llm_url(url) -> str:
    u = urllib.parse.urlparse(str(url or "").strip())
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ValueError("base_url debe empezar por http:// o https://")
    if u.username or u.password:
        raise ValueError("No incluyas usuario/clave en la URL; usa el campo API key")
    host = u.hostname.lower()
    if host == "metadata.google.internal" or host.startswith("169.254."):
        raise ValueError("Host no permitido")
    netloc = u.netloc.split("@")[-1]
    return f"{u.scheme}://{netloc}{u.path}".rstrip("/")


def _http_json(url, payload=None, headers=None, timeout=30):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data is not None else "GET")
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    opener = _NO_PROXY if _is_local_host(host) else _DEFAULT_OPENER
    try:
        with opener.open(req, timeout=timeout) as resp:
            raw = resp.read(4_000_000)
    except urllib.error.HTTPError as exc:
        detail = exc.read(400).decode("utf-8", "ignore").strip()
        raise LLMError(f"HTTP {exc.code} de {host}: {detail[:300]}", status=exc.code)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise LLMError(f"No se pudo conectar con {host}: {getattr(exc, 'reason', exc)}")
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError:
        raise LLMError("La respuesta del servidor no es JSON valido (revisa provider y base_url)")


def _nim_api_key() -> str:
    """API key de NVIDIA NIM: env NVIDIA_API_KEY / NIM_API_KEY, o secrets.nim_api_key."""
    return (
        os.environ.get("NVIDIA_API_KEY")
        or os.environ.get("NIM_API_KEY")
        or load_secrets().get("nim_api_key")
        or ""
    )


def nim_test(cfg=None) -> dict:
    """Verifica la conexion con NVIDIA NIM (integrate.api.nvidia.com) y hace una
    inferencia real de prueba con un modelo Nemotron."""
    key = _nim_api_key()
    if not key:
        return {
            "connected": False,
            "error": "No hay API key de NVIDIA NIM (env NVIDIA_API_KEY o secrets.nim_api_key)",
        }
    base = NIM_DEFAULT_BASE
    headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
    t0 = time.time()
    try:
        res = _http_json(base + "/models", headers=headers, timeout=20)
    except LLMError as exc:
        return {"connected": False, "error": str(exc)}
    models = sorted({m.get("id") for m in res.get("data", []) if m.get("id")})
    # Inferencia real de prueba: preferir Llama 3.2 11B o Nemotron 3 Ultra 550B
    prefer = [m for m in models if "llama-3.2-11b" in m.lower()]
    if not prefer:
        prefer = [m for m in models if "nemotron-3-ultra-550b" in m.lower()]
    if not prefer:
        prefer = [m for m in models if "llama" in m.lower() or "nemotron" in m.lower()]
    model = prefer[0] if prefer else (models[0] if models else None)
    inference_ok, inference_reply, inference_error = False, "", ""
    if model:
        try:
            reply = llm_chat(
                [{"role": "system", "content": "Responde unicamente con JSON valido."},
                 {"role": "user", "content": 'Devuelve exactamente {"ok": true}'}],
                {"provider": "nim", "base_url": base, "model": model,
                 "temperature": 0.2, "timeout_s": 90, "api_key": key},
                json_mode=True,
            )
            inference_ok, inference_reply = True, reply.strip()[:200]
        except (LLMError, ValueError) as exc:
            inference_error = str(exc)[:300]
    result = {
        "connected": bool(models),
        "base_url": base,
        "models_count": len(models),
        "models": models[:40],
        "test_model": model,
        "inference_ok": inference_ok,
        "inference_reply": inference_reply,
        "inference_error": inference_error,
        "latency_ms": int((time.time() - t0) * 1000),
    }
    audit("nim_test", {"connected": result["connected"], "inference_ok": inference_ok, "model": model})
    return result


def llm_public_config(cfg=None) -> dict:
    cfg = cfg or load_config()
    llm = dict(cfg["llm"])
    llm["has_api_key"] = bool(os.environ.get("DANY_LLM_API_KEY") or load_secrets().get("llm_api_key"))
    return llm


def _resolve_llm(overrides=None, cfg=None) -> dict:
    cfg = cfg or load_config()
    s = dict(cfg["llm"])
    o = overrides or {}
    if "provider" in o and o["provider"] not in (None, ""):
        if o["provider"] not in LLM_PROVIDERS:
            raise ValueError(f"provider debe ser uno de: {', '.join(LLM_PROVIDERS)}")
        s["provider"] = o["provider"]
    if o.get("base_url"):
        s["base_url"] = validate_llm_url(o["base_url"])
    else:
        s["base_url"] = validate_llm_url(s["base_url"])
    if "model" in o and o["model"] is not None:
        model = str(o["model"]).strip()
        if model and not LLM_MODEL_RE.match(model):
            raise ValueError("Nombre de modelo no valido")
        s["model"] = model
    if "temperature" in o and o["temperature"] not in (None, ""):
        s["temperature"] = _num(o, "temperature", 0.8, 0.0, 2.0)
    if "timeout_s" in o and o["timeout_s"] not in (None, ""):
        s["timeout_s"] = _num(o, "timeout_s", 120, 5, 600, cast=int)
    if "enabled" in o:
        s["enabled"] = _bool(o["enabled"])
    key = o.get("api_key")
    s["api_key"] = key.strip() if isinstance(key, str) and key.strip() else (
        os.environ.get("DANY_LLM_API_KEY") or load_secrets().get("llm_api_key") or ""
    )
    if s["provider"] == "nim":
        # NIM: base oficial si la actual apunta a localhost o está vacía
        curr_base = str(s.get("base_url") or "").strip()
        if not curr_base or "127.0.0.1" in curr_base or "localhost" in curr_base:
            s["base_url"] = NIM_DEFAULT_BASE
        else:
            s["base_url"] = validate_llm_url(curr_base)
        s["api_key"] = s.get("api_key") or _nim_api_key()
        # Model already set by user or from config; keep it as-is (no forced "vision" default)
    elif s["provider"] == "ollama":
        curr_base = str(s.get("base_url") or "").strip()
        if not curr_base or "api.nvidia.com" in curr_base:
            s["base_url"] = "http://127.0.0.1:11434"
        else:
            s["base_url"] = validate_llm_url(curr_base)
    return s


def save_llm_config(payload: dict) -> dict:
    with _lock:
        cfg = load_config()
        s = _resolve_llm(payload, cfg)
        cfg["llm"] = {
            "enabled": bool(s["enabled"]), "provider": s["provider"], "base_url": s["base_url"],
            "model": s["model"], "temperature": float(s["temperature"]), "timeout_s": int(s["timeout_s"]),
        }
        if cfg["llm"]["enabled"] and not cfg["llm"]["model"]:
            raise ValueError("Elige un modelo antes de activar el conector")
        save_config(cfg)
        sec = load_secrets()
        if _bool(payload.get("clear_api_key")):
            sec.pop("llm_api_key", None)
            save_secrets(sec)
        elif isinstance(payload.get("api_key"), str) and payload["api_key"].strip():
            sec["llm_api_key"] = payload["api_key"].strip()
            save_secrets(sec)
    audit("llm_config", {"provider": cfg["llm"]["provider"], "base_url": cfg["llm"]["base_url"], "model": cfg["llm"]["model"]})
    return llm_public_config(cfg)


def _llm_url(s: dict, kind: str) -> str:
    base = s["base_url"]
    if s["provider"] == "ollama":
        if base.endswith("/api"):
            base = base[:-4]
        return base + ("/api/tags" if kind == "models" else "/api/chat")
    root = base if base.endswith("/v1") else base + "/v1"
    return root + ("/models" if kind == "models" else "/chat/completions")


def _llm_headers(s: dict) -> dict:
    if s.get("api_key") and s["provider"] in ("openai", "nim"):
        return {"Authorization": f"Bearer {s['api_key']}"}
    return {}


def llm_models(payload=None) -> dict:
    s = _resolve_llm(payload)
    if s["provider"] == "nim":
        names = list(NIM_RECOMMENDED_MODELS)
        try:
            res = _http_json(s["base_url"] + "/models", headers=_llm_headers(s), timeout=min(int(s["timeout_s"]), 15))
            api_names = [m.get("id") for m in res.get("data", []) if m.get("id")]
            for m in api_names:
                if m not in names:
                    names.append(m)
        except Exception:
            pass
        return {"provider": s["provider"], "base_url": s["base_url"], "models": names}
    res = _http_json(_llm_url(s, "models"), headers=_llm_headers(s), timeout=min(int(s["timeout_s"]), 30))
    if s["provider"] == "ollama":
        names = [m.get("name") for m in res.get("models", []) if m.get("name")]
    else:
        names = [m.get("id") for m in res.get("data", []) if m.get("id")]
    return {"provider": s["provider"], "base_url": s["base_url"], "models": sorted(set(names))}


def llm_chat(messages, s: dict, json_mode=True) -> str:
    if not s.get("model"):
        raise LLMError("No hay modelo seleccionado")
    timeout = int(s["timeout_s"])
    if s["provider"] == "ollama":
        # num_ctx: 4096 evita que Ollama asigne ventanas nativas de 262k que saturan 33GB de RAM
        body = {
            "model": s["model"],
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": float(s["temperature"]),
                "num_ctx": 4096,
            },
        }
        if json_mode:
            body["format"] = "json"
        res = _http_json(_llm_url(s, "chat"), body, timeout=timeout)
        content = (res.get("message") or {}).get("content")
    else:
        body = {
            "model": s["model"],
            "messages": messages,
            "temperature": float(s["temperature"]),
            "max_tokens": 2048,
            "stream": False,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        try:
            res = _http_json(_llm_url(s, "chat"), body, _llm_headers(s), timeout)
        except LLMError as exc:
            if json_mode and exc.status in (400, 422):  # el servidor no soporta response_format
                body.pop("response_format", None)
                res = _http_json(_llm_url(s, "chat"), body, _llm_headers(s), timeout)
            else:
                raise
        try:
            content = res["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            content = None
    if not isinstance(content, str) or not content.strip():
        raise LLMError("El modelo devolvio una respuesta vacia")
    return content


def llm_test(payload=None) -> dict:
    s = _resolve_llm(payload)
    t0 = time.time()
    reply = llm_chat(
        [{"role": "system", "content": "Responde unicamente con JSON valido."},
         {"role": "user", "content": 'Devuelve exactamente {"ok": true}'}],
        s, json_mode=True,
    )
    return {"ok": True, "latency_ms": int((time.time() - t0) * 1000), "model": s["model"], "reply": reply.strip()[:200]}


def _extract_json(text: str) -> dict:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE).strip()
    # 1. Intentar capturar bloque markdown ```json ... ```
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    if m:
        try:
            obj = json.loads(m.group(1).strip())
            if isinstance(obj, dict):
                return obj
        except ValueError:
            pass
    # 2. Intentar extremos externos de llaves
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidate = text[start:end + 1].strip()
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                return obj
        except ValueError:
            pass
    # 3. Búsqueda de objeto JSON válido en subcadenas si hay razonamiento conversacional previo
    for i in range(len(text)):
        if text[i] == "{":
            for j in range(len(text) - 1, i, -1):
                if text[j] == "}":
                    try:
                        cand = json.loads(text[i:j+1])
                        if isinstance(cand, dict):
                            return cand
                    except ValueError:
                        continue
    raise LLMError("El modelo no devolvió un objeto JSON válido")



# ---------------------------------------------------------------------------
# Compositor (LLM manual o plantillas de respaldo)
# ---------------------------------------------------------------------------

TEMPLATES = {
    "deep horizon": {
        "keywords": ["house", "dance", "electronic", "club", "slap", "edm", "techno", "drop", "deep house"],
        "title": "shadows_in_the_neon_pulse", "genre": "Melodic Deep House",
        "lora_name": "deep horizon", "lora_scale": 0.90, "bpm": 124, "key": "D minor",
        "caption": "deep horizon, melodic deep house, slap house, punchy sub-bass at 50Hz, hypnotic filtered vocal chops, wide analog pads, tight 909 kick and crisp claps, atmospheric sidechain groove, clean club master, 124 BPM, D minor",
        "lyrics": """[Intro]

[Verse 1]
Midnight shadows moving on the neon floor
I don't look behind me anymore
Every rhythm starts to pull me in
Where the light and darkness both begin

[Pre-Chorus]
Can you feel the baseline shaking the stone
Never felt so alive while being alone

[Chorus]
Hold the rhythm, take the night
Fading in the neon light
Deep inside the pulse we stay
Till the shadows fade away

[Verse 2]
City mirrors flashing in the electric rain
Wash away the silence, wash away the pain
Lost inside the frequency, floating high
Drifting underneath the midnight sky

[Guitar Solo]

[Bridge]
No more questions, let the speaker breathe
Everything is fading when you leave

[Chorus]
Hold the rhythm, take the night
Fading in the neon light
Deep inside the pulse we stay
Till the shadows fade away

[Outro]
Fading in the neon light
Just the pulse into the night""",
        "video_prompt": "Cinematic 9:16 vertical video, dark futuristic nightclub with glowing cyan and magenta laser beams cutting through haze, camera smoothly tracking forward through dancing crowd silhouettes, ultra-crisp DLSS 5 texture, beat sync cuts every 2.0 seconds, photorealistic, no text.",
    },
    "roti-r0kkk": {
        "keywords": ["rock", "metal", "grunge", "gothic", "distort", "punk", "heavy", "fuzz"],
        "title": "burn_the_iron_altar", "genre": "Dark Alternative Rock",
        "lora_name": "roti-r0kkk", "lora_scale": 0.95, "bpm": 128, "key": "E minor",
        "caption": "roti-r0kkk, dark alternative rock, gothic grunge, raw distorted drop-D guitars, thick deep fuzz bass, punchy aggressive acoustic drums in a stone chamber, raspy emotional male vocals, intense pre-chorus build, explosive anthemic chorus, 128 BPM, E minor",
        "lyrics": """[Intro]

[Verse 1]
Cold echoes crawling on the concrete wall
Waiting for the heavy sky to finally fall
I walked the wire till the spark went dead
Listening to the voices that I never said

[Pre-Chorus]
The chains are loose, the fire is lit
I ain't bowing down to none of it

[Chorus]
Burn the altar, break the cage
Write the ending on the page
Through the storm and through the black
There is no turning back

[Verse 2]
Rusted nails driven in the crossway pine
Every single scar upon this skin is mine
They sold the promise for a cup of dust
Now there is nothing left in them to trust

[Guitar Solo]

[Bridge]
Let the thunder crash, let the lightning roar
I won't be a prisoner anymore

[Chorus]
Burn the altar, break the cage
Write the ending on the page
Through the storm and through the black
There is no turning back

[Outro]
No turning back
Just the fire in the black""",
        "video_prompt": "Cinematic 9:16 vertical shot, dramatic camera dolly in, vintage dark muscle car accelerating on rain-slicked asphalt at night, amber headlights cutting through mist, neon billboard reflections on wet metal, cinematic film grain, ultra-sharp textures, beat sync cuts every 2 seconds, no text, photorealistic.",
    },
    "michael_bennett": {
        "keywords": ["country", "soul", "blues", "folk", "acoustic", "ballad", "southern", "dobro", "piano", "outlaw", "balada", "carretera"],
        "title": "miles_of_dust_and_bone", "genre": "Acoustic Narrative Soul & Southern Rock",
        "lora_name": "michael_bennett", "lora_scale": 1.00, "bpm": 72, "key": "G major",
        "caption": "michael bennett, acoustic narrative soul, southern rock, outlaw blues ballad, deep weathered raspy male baritone vocal, handcrafted martin acoustic guitar, dirty dobro resonator slide, warm analog upright piano, slow emotional groove, warm vinyl production, 72 BPM, G major",
        "lyrics": """[Intro]

[Verse 1]
There is a road that winds into the dust
Where rusted iron never learned to trust
I count the miles by the scars upon the pine
And every shadow looks an awful lot like mine

[Pre-Chorus]
The evening falls upon the empty plains
A lonely whistle through the southern rains

[Chorus]
When the silence turns to stone
You gotta carry what you own
Through the embers and the pine
Every mile of dust is mine

[Verse 2]
I carried secrets in a wooden box
Buried deep beneath the river rocks
My hands remember what the mouth won't tell
Walked through the valley and I know it well

[Guitar Solo]

[Bridge]
Nobody asks how heavy was the load
They only ask how far you walked the road

[Chorus]
When the silence turns to stone
You gotta carry what you own
Through the embers and the pine
Every mile of dust is mine

[Outro]
Just the wind along the wire
Just the ash beneath the fire
Every mile of dust is mine""",
        "video_prompt": "Cinematic 9:16 vertical slow pan, weathered outlaw silhouette sitting on an old wooden cabin porch holding a vintage acoustic guitar, golden hour dusty sunlight streaming through cracked wood, volumetric dust motes, ultra-high dynamic range, DLSS 5 sharpness, beat sync cuts every 2.5 seconds, photorealistic.",
    },
}
ALLOWED_LORAS = tuple(TEMPLATES.keys())


def get_presets() -> dict:
    return {
        name: {k: v for k, v in t.items() if k != "keywords"}
        for name, t in TEMPLATES.items()
    }


def _compose_template(idea: str, style: str) -> dict:
    text = fold(f"{idea} {style}")
    scores = {}
    for name, tpl in TEMPLATES.items():
        scores[name] = sum(1 for w in tpl["keywords"] if re.search(rf"\b{re.escape(w)}", text))
    best = max(scores.values())
    matched = best > 0
    # Empate o sin coincidencias -> plantilla narrativa/acustica por defecto
    winners = [n for n, sc in scores.items() if sc == best]
    name = winners[0] if len(winners) == 1 else "michael_bennett"
    tpl = TEMPLATES[name]
    out = {k: v for k, v in tpl.items() if k != "keywords"}
    out.update({"status": "COMPOSED", "duration": 200.0, "model_tier": "acestep-v15-turbo",
                "source": "template", "matched": matched, "lyrics_removed": []})
    return out


_COMPOSE_SYSTEM = """Eres el compositor de un canal musical. Devuelve UNICAMENTE un objeto JSON valido (sin texto extra, sin markdown) con estas claves:
- "title": nombre corto en snake_case ASCII, sin nombres de personas.
- "genre": genero en una linea.
- "bpm": entero entre 50 y 200.
- "key": tonalidad, por ejemplo "E minor" o "G major".
- "lora_name": elige la MAS AFIN al estilo entre {loras}; usa "" solo si ninguna encaja.
- "lora_scale": numero entre 0.6 y 1.0.
- "caption": prompt musical EN INGLES para ACE-Step: genero, voz, instrumentos, atmosfera, BPM y tonalidad. Si usas una LoRA, empieza por su nombre.
- "lyrics": letra estructurada con etiquetas [Intro], [Verse 1], [Pre-Chorus], [Chorus], [Verse 2], [Bridge], [Chorus], [Outro]. SOLO letra cantada.
- "video_prompt": prompt EN INGLES para un video vertical 9:16 con movimiento de camara y cortes al ritmo.
Reglas obligatorias:
1. La letra NO lleva BPM, ni acotaciones entre parentesis, ni indicaciones de instrumentos; las etiquetas entre corchetes son solo de estructura.
2. No uses nombres de personas reales en title, caption ni lyrics.
3. Escribe la letra en el mismo idioma de la idea del usuario (salvo que pida otro). caption y video_prompt siempre en ingles.
4. La letra debe tener 2 estrofas, estribillo repetido y puente."""


def _normalize_llm_compose(obj: dict, fallback: dict) -> dict:
    lyrics_raw = obj.get("lyrics")
    if isinstance(lyrics_raw, list):
        lyrics_raw = "\n".join(str(x) for x in lyrics_raw)
    if not isinstance(lyrics_raw, str) or len(lyrics_raw.strip()) < 40:
        raise LLMError("El modelo no devolvio una letra valida")
    lyrics, removed = lint_lyrics(lyrics_raw)
    caption = str(obj.get("caption") or "").strip()[:1500]
    if not caption:
        raise LLMError("El modelo no devolvio caption")
    lora = str(obj.get("lora_name") or "").strip()
    if lora not in ALLOWED_LORAS:
        lora = ""
    try:
        key = normalize_key(obj.get("key"))
    except ValueError:
        key = fallback["key"]
    return {
        "status": "COMPOSED",
        "title": slugify(obj.get("title", ""), default=fallback["title"]),
        "genre": str(obj.get("genre") or fallback["genre"]).strip()[:80],
        "bpm": _clamp(obj.get("bpm"), 40, 240, fallback["bpm"], int),
        "key": key,
        "lora_name": lora,
        "lora_scale": _clamp(obj.get("lora_scale"), 0.5, 1.2, 1.0),
        "duration": 200.0,
        "caption": caption,
        "lyrics": lyrics,
        "video_prompt": str(obj.get("video_prompt") or fallback["video_prompt"]).strip()[:1200],
        "model_tier": "acestep-v15-turbo",
        "lyrics_removed": removed,
    }


def compose(idea, style="auto") -> dict:
    idea = str(idea or "").strip()[:1000]
    style = str(style or "auto").strip()[:60]
    if not idea:
        raise ValueError("Escribe una idea para componer")
    cfg = load_config()
    fallback = _compose_template(idea, style)
    llm = cfg["llm"]
    if not (llm["enabled"] and llm["model"]):
        return fallback
    try:
        s = _resolve_llm(None, cfg)
        messages = [
            {"role": "system", "content": _COMPOSE_SYSTEM.format(loras=", ".join(f'"{n}"' for n in ALLOWED_LORAS))},
            {"role": "user", "content": f"Idea: {idea}\nEstilo preferido: {style}"},
        ]
        result = _normalize_llm_compose(_extract_json(llm_chat(messages, s, json_mode=True)), fallback)
        result.update({"source": "llm", "llm_model": s["model"], "matched": True})
        audit("compose", {"source": "llm", "model": s["model"]})
        return result
    except (LLMError, ValueError) as exc:
        fallback["llm_error"] = str(exc)
        audit("compose", {"source": "template_fallback", "error": str(exc)[:200]})
        return fallback


# ---------------------------------------------------------------------------
# CLI de utilidades
# ---------------------------------------------------------------------------

AUDIO_MIME = {
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".flac": "audio/flac",
    ".m4a": "audio/mp4", ".ogg": "audio/ogg", ".aac": "audio/aac",
    ".wma": "audio/x-ms-wma", ".opus": "audio/opus",
}


def resolve_audio_file(raw_path, cfg=None) -> Path:
    """Valida que la ruta sea un archivo de audio dentro de las carpetas permitidas (para /api/audio/stream)."""
    cfg = cfg or load_config()
    raw = str(raw_path or "").strip().strip('"')
    if not raw:
        raise ValueError("Indica la ruta del archivo de audio")
    src = Path(raw)
    if not src.is_absolute():
        raise ValueError("Usa una ruta absoluta")
    src = src.resolve()
    if not src.is_file():
        raise FileNotFoundError(f"No existe el archivo: {src}")
    if src.suffix.lower() not in AUDIO_EXTS:
        raise ValueError(f"Extension no permitida ({src.suffix})")
    if not is_within(src, audio_roots(cfg)):
        raise PermissionError(
            "El archivo esta fuera de las carpetas permitidas. Anade su carpeta en "
            "config.json -> paths.extra_audio_roots"
        )
    return src


# ---------------------------------------------------------------------------
# v2.2: Biblioteca de video (vistas previas de MiniMax H3 / LTX en la interfaz)
# ---------------------------------------------------------------------------

VIDEO_EXTS = {".mp4", ".webm", ".mov", ".mkv", ".m4v", ".avi", ".gif"}
VIDEO_MIME = {
    ".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime",
    ".mkv": "video/x-matroska", ".m4v": "video/x-m4v", ".avi": "video/x-msvideo", ".gif": "image/gif",
}


def video_roots(cfg=None) -> list:
    return [BASE_DIR / "outputs"]


def list_video_files(cfg=None, limit=40) -> dict:
    items = []
    seen = set()
    for root in video_roots(cfg):
        if not root.is_dir():
            continue
        try:
            for p in root.rglob("*"):
                if not p.is_file() or p.suffix.lower() not in VIDEO_EXTS:
                    continue
                try:
                    rp = p.resolve()
                except OSError:
                    continue
                if rp in seen:
                    continue
                seen.add(rp)
                try:
                    st = rp.stat()
                except OSError:
                    continue
                items.append({
                    "path": str(rp), "name": rp.stem, "ext": rp.suffix.lower(),
                    "size": st.st_size, "mtime": st.st_mtime,
                })
        except OSError:
            continue
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return {"videos": items[:limit]}


def resolve_video_file(raw_path, cfg=None) -> Path:
    """Valida que la ruta sea un archivo de video dentro de outputs/ (para /api/video/stream)."""
    raw = str(raw_path or "").strip().strip('"')
    if not raw:
        raise ValueError("Indica la ruta del video")
    src = Path(raw)
    if not src.is_absolute():
        raise ValueError("Usa una ruta absoluta")
    src = src.resolve()
    if not src.is_file():
        raise FileNotFoundError(f"No existe el archivo: {src}")
    if src.suffix.lower() not in VIDEO_EXTS:
        raise ValueError(f"Extension no permitida ({src.suffix})")
    if not is_within(src, video_roots(cfg)):
        raise PermissionError("El archivo esta fuera de las carpetas permitidas")
    return src


# ---------------------------------------------------------------------------
# v2.2: Generador de prompt de video (IA) segun cancion / genero / BPM
# ---------------------------------------------------------------------------

_VIDEO_PROMPT_SYSTEM = """Eres el director de fotografia de un canal de Shorts musicales verticales (9:16).
Devuelve UNICAMENTE un objeto JSON valido (sin texto extra, sin markdown) con esta clave:
- "video_prompt": prompt EN INGLES, cinematico, para MiniMax H3 (modelo que genera video 9:16 con audio sincronizado). Describe estilo visual, iluminacion y movimientos de camara, estructurado en 2-3 "CUT" (CUT 1, CUT 2...) que casen con el estado de animo, genero y BPM de la cancion. No incluyas texto en pantalla salvo que tenga sentido narrativo. Maximo 900 caracteres.
Usa el genero, BPM, tonalidad y fragmento de letra proporcionados para ambientar el video; no menciones nombres de personas reales."""


def _fallback_video_prompt(genre: str, caption: str, bpm) -> str:
    """Sin modelo IA conectado: reutiliza el video_prompt de la plantilla cuyo genero/caption mas se parezca."""
    text = fold(f"{genre} {caption}")
    scores = {name: sum(1 for w in tpl["keywords"] if re.search(rf"\b{re.escape(w)}", text)) for name, tpl in TEMPLATES.items()}
    best = max(scores.values()) if scores else 0
    winners = [n for n, sc in scores.items() if sc == best]
    name = winners[0] if best > 0 and len(winners) == 1 else "michael_bennett"
    base = TEMPLATES[name]["video_prompt"]
    try:
        bpm_val = float(bpm)
    except (TypeError, ValueError):
        bpm_val = None
    interval = "1.5" if (bpm_val and bpm_val >= 140) else ("2.0" if (bpm_val and bpm_val >= 100) else "2.5")
    base = re.sub(r",?\s*beat sync cuts every [\d.,]+ seconds?", "", base, flags=re.IGNORECASE)
    return base.rstrip(". ") + f", beat sync cuts every {interval} seconds."


def generate_video_prompt(payload: dict) -> dict:
    title = str(payload.get("title") or "").strip()[:120]
    genre = str(payload.get("genre") or "").strip()[:120]
    caption = str(payload.get("caption") or "").strip()[:1500]
    lyrics = str(payload.get("lyrics") or "").strip()[:4000]
    key = str(payload.get("key") or "").strip()
    bpm = payload.get("bpm")
    if not (genre or caption):
        raise ValueError("Indica al menos el genero o el caption musical de la cancion")

    fallback = _fallback_video_prompt(genre, caption, bpm)
    cfg = load_config()
    llm = cfg["llm"]
    if not (llm["enabled"] and llm["model"]):
        return {"video_prompt": fallback, "source": "template"}

    try:
        s = _resolve_llm(None, cfg)
        parts = [
            f"Titulo: {title}" if title else "",
            f"Genero: {genre}" if genre else "",
            f"BPM: {bpm}" if bpm else "",
            f"Tonalidad: {key}" if key else "",
            f"Caption musical: {caption}" if caption else "",
            f"Fragmento de letra:\n{lyrics[:600]}" if lyrics else "",
        ]
        messages = [
            {"role": "system", "content": _VIDEO_PROMPT_SYSTEM},
            {"role": "user", "content": "\n".join(p for p in parts if p)},
        ]
        obj = _extract_json(llm_chat(messages, s, json_mode=True))
        video_prompt = str(obj.get("video_prompt") or "").strip()[:1200]
        if not video_prompt:
            raise LLMError("El modelo no devolvio video_prompt")
        audit("video_prompt", {"source": "llm", "model": s["model"]})
        return {"video_prompt": video_prompt, "source": "llm", "llm_model": s["model"]}
    except (LLMError, ValueError) as exc:
        audit("video_prompt", {"source": "template_fallback", "error": str(exc)[:200]})
        return {"video_prompt": fallback, "source": "template", "llm_error": str(exc)}


# ---------------------------------------------------------------------------
# v2.3: Generador de Portadas IA con ComfyUI img2img
# ---------------------------------------------------------------------------

def _comfy_url(cfg=None) -> str:
    """Construye la URL base de ComfyUI desde la config."""
    cfg = cfg or load_config()
    base = str(cfg.get("comfy", {}).get("api_url", "http://127.0.0.1:8188")).rstrip("/")
    if not base.endswith("/v1"):
        base = base.rstrip("/") + "/v1"
    return base


def _comfy_request(method: str, endpoint: str, payload: dict = None, cfg=None, timeout: int = 60) -> dict:
    """Hace una petición a la API de ComfyUI."""
    import urllib.request
    import urllib.error
    import json

    base = _comfy_url(cfg)
    url = base + endpoint
    data = json.dumps(payload).encode("utf-8") if payload else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(4_000_000)
            return json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read(400).decode("utf-8", "ignore").strip()
        raise LLMError(f"HTTP {exc.code} de ComfyUI: {detail[:300]}", status=exc.code)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise LLMError(f"No se pudo conectar con ComfyUI: {getattr(exc, 'reason', exc)}")


# ---------------------------------------------------------------------------
# v2.5: Generación de Portadas con Qwen Image 2.1 (INT8 ConvRot en ComfyUI)
# ---------------------------------------------------------------------------

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
IMAGE_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


def resolve_image_file(rel_path: str, cfg=None) -> Path:
    cfg = cfg or load_config()
    raw = str(rel_path or "").strip()
    if not raw:
        raise FileNotFoundError("Ruta de imagen vacía")
    p = Path(raw).expanduser()
    if p.is_file() and p.suffix.lower() in IMAGE_EXTS:
        return p.resolve()
    cand = BASE_DIR / "outputs" / "covers" / p.name
    if cand.is_file():
        return cand.resolve()
    cand_vid = BASE_DIR / "outputs" / "videos" / p.name
    if cand_vid.is_file():
        return cand_vid.resolve()
    cand_base = BASE_DIR / "outputs" / p.name
    if cand_base.is_file():
        return cand_base.resolve()
    comfy_dir = _opt_path(cfg.get("paths", {}).get("comfy_dir"))
    if comfy_dir:
        cand_c = comfy_dir / "output" / "covers" / p.name
        if cand_c.is_file():
            return cand_c.resolve()
        cand_co = comfy_dir / "output" / p.name
        if cand_co.is_file():
            return cand_co.resolve()
    raise FileNotFoundError(f"No se encuentra la imagen: {rel_path}")


def list_cover_images(cfg=None, limit=40) -> dict:
    """Lista las portadas e imágenes generadas en outputs/covers y ComfyUI."""
    cfg = cfg or load_config()
    items = []
    seen = set()
    roots = [
        BASE_DIR / "outputs" / "covers",
        BASE_DIR / "outputs",
    ]
    comfy_dir = _opt_path(cfg.get("paths", {}).get("comfy_dir"))
    if comfy_dir:
        roots.append(comfy_dir / "output" / "covers")
        roots.append(comfy_dir / "output")

    for root in roots:
        if not root.is_dir():
            continue
        try:
            for p in root.iterdir():
                if not p.is_file() or p.suffix.lower() not in IMAGE_EXTS:
                    continue
                try:
                    rp = p.resolve()
                except OSError:
                    continue
                if rp in seen:
                    continue
                seen.add(rp)
                try:
                    st = rp.stat()
                except OSError:
                    continue
                items.append({
                    "path": str(rp),
                    "name": rp.stem,
                    "ext": rp.suffix.lower(),
                    "size": st.st_size,
                    "mtime": st.st_mtime,
                    "url": f"/api/image/stream?path={urllib.parse.quote(str(rp))}",
                })
        except OSError:
            continue
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return {"covers": items[:limit]}


def build_krea2_image_workflow(
    prompt: str,
    aspect_ratio: str = "square",
    steps: int = 8,
    guidance: float = 1.0,
    seed: int = None,
    use_llm: bool = True,
    filename_prefix: str = "covers/dany_krea_cover",
) -> dict:
    """Construye el workflow ComfyUI oficial para Krea-2 Turbo con upscaling ESRGAN 4x y Qwen3-VL."""
    aspect = str(aspect_ratio or "square").strip().lower()
    if aspect in ("square", "1:1"):
        w, h = 1024, 1024
    elif aspect in ("horizontal", "16:9"):
        w, h = 1376, 768
    else:  # vertical / 9:16
        w, h = 768, 1376

    if seed is None or seed == "":
        seed = secrets.randbelow(2**31 - 1)
    else:
        seed = int(seed)

    wf_template_path = BASE_DIR / "workflows" / "krea2_cover_workflow.json"
    wf = None
    if wf_template_path.is_file():
        try:
            with open(wf_template_path, "r", encoding="utf-8") as f:
                wf = json.load(f)
        except Exception:
            wf = None

    if not wf:
        wf = {
            "8": {"class_type": "VAEDecode", "inputs": {"samples": ["31", 0], "vae": ["39", 0]}},
            "31": {
                "class_type": "KSampler",
                "inputs": {
                    "seed": seed, "steps": ["368", 0], "cfg": ["397", 0],
                    "sampler_name": "ddim", "scheduler": "beta57", "denoise": 1.0,
                    "model": ["360", 0], "positive": ["387", 0], "negative": ["387", 0],
                    "latent_image": ["263", 0]
                }
            },
            "39": {"class_type": "VAELoader", "inputs": {"vae_name": "krea2RealVae_v10.safetensors"}},
            "212": {"class_type": "TextEncodeQwenImageEditPlus", "inputs": {"prompt": ["410", 0], "clip": ["360", 1], "vae": ["39", 0]}},
            "213": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen3vl_4b_fp8_scaled.safetensors", "type": "krea2", "device": "default"}},
            "233": {"class_type": "ImageUpscaleWithModel", "inputs": {"upscale_model": ["235", 0], "image": ["8", 0]}},
            "235": {"class_type": "UpscaleModelLoader", "inputs": {"model_name": "ESRGAN_4x.pth"}},
            "238": {"class_type": "INTConstant", "inputs": {"value": ["367", 0]}},
            "239": {"class_type": "INTConstant", "inputs": {"value": ["367", 1]}},
            "263": {"class_type": "EmptyLatentImage", "inputs": {"width": ["238", 0], "height": ["239", 0], "batch_size": 1}},
            "316": {"class_type": "Image Comparer (rgthree)", "inputs": {"rgthree_comparer": {"images": []}, "image_a": ["8", 0], "image_b": ["8", 0]}},
            "359": {"class_type": "UNETLoader", "inputs": {"unet_name": "krea2_raw_bf16.safetensors", "weight_dtype": "fp8_e4m3fn"}},
            "360": {
                "class_type": "AcademiaSD_MultiLora",
                "inputs": {
                    "injection_method": "Standard (Native)",
                    "lora_data": "[{\"enabled\":true,\"name\":\"0739 St. Louis Luxurious Wheels dress_v1_pony.safetensors\",\"strength\":0.5}]",
                    "UI": "", "model": ["382", 0], "clip": ["213", 0]
                }
            },
            "361": {"class_type": "AcademiaSD_Downloader", "inputs": {"UI": ""}},
            "363": {"class_type": "AcademiaSD_SaveAndSend", "inputs": {"filename_prefix": filename_prefix, "target_node_title": "Load Image", "images": ["8", 0]}},
            "364": {"class_type": "AcademiaSD_SaveAndSend", "inputs": {"filename_prefix": f"{filename_prefix}_upscaled", "target_node_title": "Load Image", "images": ["233", 0]}},
            "367": {"class_type": "AcademiaSD_Resolution", "inputs": {"preset": f"{w}x{h}", "width": w, "height": h}},
            "368": {"class_type": "INTConstant", "inputs": {"value": ["395", 0]}},
            "369": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["212", 0]}},
            "377": {"class_type": "PrimitiveInt", "inputs": {"value": 2}},
            "382": {"class_type": "ImpactSwitch", "inputs": {"select": ["377", 0], "sel_mode": False, "input1": ["359", 0], "input2": ["385", 0]}},
            "385": {"class_type": "LoaderGGUFAdvanced", "inputs": {"gguf_name": "Krea-2-Turbo-Q4_K_M.gguf", "dequant_dtype": "default", "patch_dtype": "default", "patch_on_device": True}},
            "387": {"class_type": "ConditioningKrea2Rebalance", "inputs": {"multiplier": 4, "per_layer_weights": "1.0,1.0,1.0,1.0,1.0,1.0,1.0,2.5,5.0,1.1,4.0,1.0", "conditioning": ["212", 0]}},
            "390": {"class_type": "Float", "inputs": {"Number": str(guidance)}},
            "392": {"class_type": "INTConstant", "inputs": {"value": int(steps)}},
            "393": {"class_type": "Float", "inputs": {"Number": "4"}},
            "394": {"class_type": "INTConstant", "inputs": {"value": 52}},
            "395": {"class_type": "ImpactSwitch", "inputs": {"select": 1, "sel_mode": False, "input1": ["392", 0], "input2": ["394", 0]}},
            "397": {"class_type": "ImpactSwitch", "inputs": {"select": 1, "sel_mode": False, "input1": ["390", 0], "input2": ["393", 0]}},
            "401": {"class_type": "PrimitiveStringMultiline", "inputs": {"value": "You are an expert prompt engineer for text-to-image models. Expand the user's prompt into a highly effective image-generation prompt paragraph. User's Input:\n\n"}},
            "402": {"class_type": "StringConcatenate", "inputs": {"string_a": ["401", 0], "string_b": ["408", 0], "delimiter": ""}},
            "403": {"class_type": "TextGenerate", "inputs": {"prompt": ["402", 0], "max_length": 512, "sampling_mode": "on", "sampling_mode.temperature": 0.7, "sampling_mode.top_k": 64, "sampling_mode.top_p": 0.95, "sampling_mode.min_p": 0.05, "sampling_mode.repetition_penalty": 1.05, "sampling_mode.seed": 0, "sampling_mode.presence_penalty": 0, "thinking": True, "use_default_template": True, "mtp": "auto", "clip": ["213", 0]}},
            "405": {"class_type": "PreviewAny", "inputs": {"source": ["403", 0]}},
            "408": {"class_type": "PrimitiveStringMultiline", "inputs": {"value": prompt}},
            "410": {"class_type": "ImpactSwitch", "inputs": {"select": ["412", 0], "sel_mode": False, "input1": ["408", 0], "input2": ["405", 0]}},
            "412": {"class_type": "INTConstant", "inputs": {"value": 2 if use_llm else 1}},
        }

    # Inyectar parámetros dinámicos sobre la plantilla
    if "408" in wf and "inputs" in wf["408"]:
        wf["408"]["inputs"]["value"] = str(prompt)
    if "412" in wf and "inputs" in wf["412"]:
        wf["412"]["inputs"]["value"] = 2 if use_llm else 1
    if "367" in wf and "inputs" in wf["367"]:
        wf["367"]["inputs"]["preset"] = f"{w}x{h}"
        wf["367"]["inputs"]["width"] = w
        wf["367"]["inputs"]["height"] = h
    if "238" in wf and "inputs" in wf["238"]:
        wf["238"]["inputs"]["value"] = ["367", 0]
    if "239" in wf and "inputs" in wf["239"]:
        wf["239"]["inputs"]["value"] = ["367", 1]
    if "263" in wf and "inputs" in wf["263"]:
        wf["263"]["inputs"]["width"] = ["238", 0]
        wf["263"]["inputs"]["height"] = ["239", 0]
    if "31" in wf and "inputs" in wf["31"]:
        wf["31"]["inputs"]["seed"] = seed
    if "392" in wf and "inputs" in wf["392"]:
        wf["392"]["inputs"]["value"] = int(steps)
    if "390" in wf and "inputs" in wf["390"]:
        wf["390"]["inputs"]["Number"] = str(guidance)
    if "363" in wf and "inputs" in wf["363"]:
        wf["363"]["inputs"]["filename_prefix"] = filename_prefix
    if "364" in wf and "inputs" in wf["364"]:
        wf["364"]["inputs"]["filename_prefix"] = f"{filename_prefix}_upscaled"

    # Salida estándar adicional para compatibilidad con cualquier visor de ComfyUI
    wf["save_std"] = {
        "class_type": "SaveImage",
        "inputs": {"filename_prefix": filename_prefix, "images": ["8", 0]}
    }
    wf["save_upscaled"] = {
        "class_type": "SaveImage",
        "inputs": {"filename_prefix": f"{filename_prefix}_upscaled", "images": ["233", 0]}
    }

    return wf


def build_qwen_image_workflow(
    prompt: str,
    aspect_ratio: str = "square",
    steps: int = 12,
    guidance: float = 2.5,
    seed: int = None,
    filename_prefix: str = "covers/cover",
) -> dict:
    """Compatibilidad: Genera workflow ComfyUI con Qwen Image 2.1."""
    aspect = str(aspect_ratio or "square").strip().lower()
    if aspect in ("square", "1:1"):
        w, h = 1024, 1024
    elif aspect in ("horizontal", "16:9"):
        w, h = 1344, 768
    else:  # vertical / 9:16
        w, h = 768, 1344

    if seed is None or seed == "":
        seed = secrets.randbelow(2**31 - 1)
    else:
        seed = int(seed)

    return {
        "unet": {
            "class_type": "UNETLoader",
            "inputs": {
                "unet_name": "qwen_image_2.1_int8_convrot.safetensors",
                "weight_dtype": "default"
            }
        },
        "clip": {
            "class_type": "CLIPLoader",
            "inputs": {
                "clip_name": "qwen3vl_8b_int8_convrot.safetensors",
                "type": "qwen_image",
                "device": "default"
            }
        },
        "vae": {
            "class_type": "VAELoader",
            "inputs": {
                "vae_name": "qwen_image_2.1_vae_bf16.safetensors"
            }
        },
        "shift": {
            "class_type": "ModelSamplingAuraFlow",
            "inputs": {
                "shift": 2.0,
                "model": ["unet", 0]
            }
        },
        "positive": {
            "class_type": "CLIPTextEncode",
            "inputs": {
                "text": prompt,
                "clip": ["clip", 0]
            }
        },
        "negative": {
            "class_type": "ConditioningZeroOut",
            "inputs": {
                "conditioning": ["positive", 0]
            }
        },
        "latent": {
            "class_type": "EmptySD3LatentImage",
            "inputs": {
                "width": w,
                "height": h,
                "batch_size": 1
            }
        },
        "sampler": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["shift", 0],
                "positive": ["positive", 0],
                "negative": ["negative", 0],
                "latent_image": ["latent", 0],
                "seed": seed,
                "steps": int(steps),
                "cfg": float(guidance),
                "sampler_name": "euler",
                "scheduler": "simple",
                "denoise": 1.0
            }
        },
        "decode": {
            "class_type": "VAEDecode",
            "inputs": {
                "samples": ["sampler", 0],
                "vae": ["vae", 0]
            }
        },
        "save": {
            "class_type": "SaveImage",
            "inputs": {
                "filename_prefix": filename_prefix,
                "images": ["decode", 0]
            }
        }
    }


def generate_cover_image(payload: dict, cfg=None) -> dict:
    """Genera una portada usando ComfyUI con Krea-2 Turbo (GGUF/FP8, Qwen3-VL y ESRGAN 4x)."""
    cfg = cfg or load_config()

    idea = str(payload.get("idea") or "").strip()[:800]
    prompt_in = str(payload.get("prompt") or "").strip()
    genre = str(payload.get("genre") or "auto").strip()
    style = str(payload.get("style") or "").strip()[:500]
    aspect = str(payload.get("aspect_ratio") or "square").strip().lower()
    guidance = _num(payload, "guidance_scale", 1.0, 1.0, 15.0, cast=float)
    steps = _num(payload, "steps", 8, 4, 50, cast=int)
    seed_raw = payload.get("seed")
    seed = int(seed_raw) if seed_raw not in (None, "") else secrets.randbelow(2**31 - 1)
    use_llm = bool(payload.get("use_llm", True))

    final_prompt = prompt_in
    if not final_prompt:
        prompt_parts = [
            f"Album cover artwork of {idea}" if idea else "Cinematic album cover",
            f"Genre: {genre}" if genre and genre != "auto" else "",
            f"Visual style: {style}" if style else "",
            "high quality, detailed, vibrant lighting, professional music single cover, masterpiece",
        ]
        final_prompt = ", ".join(p for p in prompt_parts if p)

    if use_llm and idea:
        try:
            s = _resolve_llm(None, cfg)
            messages = [
                {"role": "system", "content": "You are an expert album art visual director for ComfyUI. Generate a single highly descriptive English visual prompt for cover art. Be concise (max 250 characters). Output only the prompt."},
                {"role": "user", "content": f"Create cover art prompt for: {idea}. Genre: {genre}. Style: {style}"}
            ]
            raw = llm_chat(messages, s, json_mode=False)
            if raw and len(raw.strip()) > 15:
                final_prompt = raw.strip()[:350]
        except Exception:
            pass

    prefix = f"covers/dany_krea_{slugify(genre, default='cover')}"
    wf = build_krea2_image_workflow(
        prompt=final_prompt,
        aspect_ratio=aspect,
        steps=steps,
        guidance=guidance,
        seed=seed,
        use_llm=use_llm,
        filename_prefix=prefix
    )

    res = _comfy_http("POST", "/prompt", cfg, {"prompt": wf, "client_id": secrets.token_hex(8)}, timeout=30)
    prompt_id = res.get("prompt_id") if isinstance(res, dict) else None
    if not prompt_id:
        raise RuntimeError(f"ComfyUI no devolvió prompt_id: {json.dumps(res, ensure_ascii=False)[:400]}")

    audit("cover_image_dispatch", {
        "engine": "krea2_turbo",
        "prompt_id": prompt_id, "prompt": final_prompt[:150], "aspect": aspect,
        "seed": seed, "steps": steps, "guidance": guidance, "use_llm": use_llm
    })

    return {
        "status": "QUEUED",
        "prompt_id": prompt_id,
        "queue_number": res.get("number"),
        "aspect_ratio": aspect,
        "seed": seed,
        "prompt": final_prompt,
        "engine": "Krea-2 Turbo"
    }


def cover_image_status(prompt_id: str, cfg=None) -> dict:
    """Consulta estado de la generación de portada Krea-2 Turbo en ComfyUI."""
    cfg = cfg or load_config()
    prompt_id = str(prompt_id or "").strip()
    if not _PROMPT_ID_RE.match(prompt_id):
        raise ValueError("prompt_id no válido")

    hist = _comfy_http("GET", f"/history/{urllib.parse.quote(prompt_id)}", cfg, timeout=15)
    entry = hist.get(prompt_id) if isinstance(hist, dict) else None

    if entry is None:
        q = _comfy_http("GET", "/queue", cfg, timeout=15)
        pending = set()
        if isinstance(q, dict):
            for item in (q.get("queue_running") or []) + (q.get("queue_pending") or []):
                if isinstance(item, (list, tuple)) and len(item) > 1:
                    pending.add(item[1])
        if prompt_id in pending:
            return {"status": "RUNNING", "prompt_id": prompt_id, "message": "En cola de generación..."}
        return {"status": "UNKNOWN", "prompt_id": prompt_id, "message": "ComfyUI no tiene registro de este trabajo."}

    status_info = entry.get("status") or {}
    completed = bool(status_info.get("completed"))
    status_str = str(status_info.get("status_str") or "").lower()
    if status_str == "error":
        msgs = str(status_info.get("messages") or "Error en nodo de ComfyUI")
        return {"status": "ERROR", "prompt_id": prompt_id, "message": msgs}

    outputs = entry.get("outputs") or {}
    found_images = []
    for out in outputs.values():
        if isinstance(out, dict):
            found_images.extend(out.get("images") or [])

    if not completed and not found_images:
        return {"status": "RUNNING", "prompt_id": prompt_id, "message": status_str or "Generando portada en GPU con Krea-2 Turbo..."}

    dest_dir = BASE_DIR / "outputs" / "covers"
    dest_dir.mkdir(parents=True, exist_ok=True)
    saved_images = []
    for item in found_images:
        fname = str((item or {}).get("filename") or "").strip()
        if not fname or "/" in fname or "\\" in fname or ".." in fname:
            continue
        try:
            data = _comfy_fetch_view(item, cfg)
            dest = dest_dir / fname
            dest.write_bytes(data)
            saved_images.append(str(dest))
        except Exception:
            comfy_dir = _opt_path(cfg.get("paths", {}).get("comfy_dir"))
            if comfy_dir:
                sub = str((item or {}).get("subfolder") or "")
                local_f = comfy_dir / "output" / sub / fname if sub else comfy_dir / "output" / fname
                if local_f.is_file():
                    dest = dest_dir / fname
                    import shutil
                    shutil.copyfile(local_f, dest)
                    saved_images.append(str(dest))

    if saved_images:
        # Si hay versión ampliada (upscaled), presentarla preferentemente
        upscaled = [img for img in saved_images if "_upscaled" in img.lower()]
        first = upscaled[0] if upscaled else saved_images[0]
        url = f"/api/image/stream?path={urllib.parse.quote(first)}"
        audit("cover_image_result", {"prompt_id": prompt_id, "saved": first, "total": len(saved_images), "engine": "krea2_turbo"})
        return {
            "status": "DONE",
            "prompt_id": prompt_id,
            "image_path": first,
            "image_url": url,
            "images": saved_images,
            "message": "Portada Krea-2 Turbo generada y ampliada con éxito"
        }

    return {"status": "RUNNING", "prompt_id": prompt_id, "message": "Procesando imagen con Krea-2 Turbo..."}


def serve_comfy_image(seed: str) -> dict:
    """Compatibilidad: URL para servir imagen ComfyUI."""
    cfg = load_config()
    base = str(cfg.get("comfy", {}).get("api_url", "http://127.0.0.1:8188")).rstrip("/")
    return {
        "image_path": f"{base}/view?seed={seed}" if seed else "",
        "seed": seed,
        "status": "ready_for_frontend",
    }


# ---------------------------------------------------------------------------
# v2.2: Video con ComfyUI - MiniMax H3 (Reference-to-Video con audio nativo)
#
# Grafo API-format equivalente (aplanado) al workflow oficial
# "H3 Lite - Multi-Reference.json" (subgrafo hub-h3-key-inputs-base), sin
# imagenes/videos/audios de referencia: solo texto -> video+audio 9:16.
# Nombres de nodo/entrada verificados contra la documentacion oficial de
# MiniMaxH3ReferenceToVideo (docs.comfy.org) y el propio workflow exportado.
# ---------------------------------------------------------------------------

H3_UNET_MODEL = "minimax_h3_ref2va_pruned_int8_convrot.safetensors"
H3_CLIP_MODEL = "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
H3_VAE_VIDEO = "minimax_h3_video_vae_fp16.safetensors"
H3_VAE_AUDIO = "minimax_h3_audio_vae_fp32.safetensors"
# Dimensiones 9:16 (alineadas a 32 px) tomadas de "H3 Lite - Multi-Reference.json"
H3_ASPECT_9_16 = {"native": (768, 1344), "preview": (480, 864)}
_PROMPT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _comfy_url(cfg=None) -> str:
    cfg = cfg or load_config()
    comfy_cfg = cfg.get("comfy") if isinstance(cfg.get("comfy"), dict) else {}
    return str(comfy_cfg.get("api_url") or "http://127.0.0.1:8188").rstrip("/")


def _comfy_http(method, path, cfg=None, payload=None, timeout=30):
    url = _comfy_url(cfg) + path
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with _NO_PROXY.open(req, timeout=timeout) as resp:
            raw = resp.read(80_000_000)
            ctype = resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        detail = exc.read(600).decode("utf-8", "ignore").strip()
        raise RuntimeError(f"ComfyUI HTTP {exc.code}: {detail[:500]}")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(
            f"No se pudo conectar con ComfyUI en {_comfy_url(cfg)}. Abre ComfyUI y vuelve a intentarlo. "
            f"Detalle: {getattr(exc, 'reason', exc)}"
        )
    if "application/json" in ctype:
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError:
            raise RuntimeError("ComfyUI devolvio una respuesta JSON no valida")
    return raw


def _h3_length_from_seconds(seconds) -> int:
    """Replica la formula del workflow oficial: frames a 24fps alineados a la rejilla 17k+5, entre 124 y 362."""
    frames = round(float(seconds) * 24)
    aligned = frames + (5 - (frames % 17)) % 17
    return int(min(362, max(124, aligned)))


def _comfy_fetch_view(item: dict, cfg=None) -> bytes:
    params = urllib.parse.urlencode({
        "filename": item.get("filename", ""),
        "subfolder": item.get("subfolder", ""),
        "type": item.get("type", "output"),
    })
    data = _comfy_http("GET", f"/view?{params}", cfg, timeout=180)
    if not isinstance(data, (bytes, bytearray)):
        raise RuntimeError("ComfyUI no devolvio el archivo de video esperado")
    return data


def video_status(prompt_id: str, cfg=None) -> dict:
    """Consulta /history en ComfyUI; si terminó, descarga el .mp4 a outputs/videos/ y outputs/minimax_videos/.
    Detecta errores de ejecución en ComfyUI y no se queda bloqueado en RUNNING."""
    cfg = cfg or load_config()
    prompt_id = str(prompt_id or "").strip()
    if not _PROMPT_ID_RE.match(prompt_id):
        raise ValueError("prompt_id no válido")

    hist = _comfy_http("GET", f"/history/{urllib.parse.quote(prompt_id)}", cfg, timeout=15)
    entry = hist.get(prompt_id) if isinstance(hist, dict) else None

    if entry is None:
        q = _comfy_http("GET", "/queue", cfg, timeout=15)
        pending = set()
        if isinstance(q, dict):
            for item in (q.get("queue_running") or []) + (q.get("queue_pending") or []):
                if isinstance(item, (list, tuple)) and len(item) > 1:
                    pending.add(item[1])
        if prompt_id in pending:
            return {"status": "RUNNING", "prompt_id": prompt_id, "message": "En cola de ComfyUI..."}
        return {"status": "UNKNOWN", "prompt_id": prompt_id, "message": "ComfyUI no tiene registro de este trabajo."}

    status_info = entry.get("status") or {}
    completed = bool(status_info.get("completed"))
    status_str = str(status_info.get("status_str") or "").lower()

    if status_str == "error":
        msgs = str(status_info.get("messages") or "Error en nodo de ComfyUI")
        return {"status": "ERROR", "prompt_id": prompt_id, "message": f"Error en ComfyUI: {msgs}"}

    outputs = entry.get("outputs") or {}
    found = []
    for out in outputs.values():
        if isinstance(out, dict):
            found.extend(out.get("videos") or [])
            found.extend(out.get("gifs") or [])

    if not completed and not found:
        return {"status": "RUNNING", "prompt_id": prompt_id, "message": status_str or "Generando vídeo en GPU..."}

    dest_dir = BASE_DIR / "outputs" / "videos"
    dest_dir.mkdir(parents=True, exist_ok=True)
    minimax_dir = BASE_DIR / "outputs" / "minimax_videos"
    minimax_dir.mkdir(parents=True, exist_ok=True)

    saved = []
    for item in found:
        fname = str((item or {}).get("filename") or "").strip()
        if not fname or "/" in fname or "\\" in fname or ".." in fname:
            continue
        try:
            data = _comfy_fetch_view(item, cfg)
            dest = dest_dir / fname
            dest.write_bytes(data)
            saved.append(str(dest))
            # Copia espejo a minimax_videos si corresponde
            try:
                (minimax_dir / fname).write_bytes(data)
            except Exception:
                pass
        except Exception:
            comfy_dir = _opt_path(cfg.get("paths", {}).get("comfy_dir"))
            if comfy_dir:
                sub = str((item or {}).get("subfolder") or "")
                local_f = comfy_dir / "output" / sub / fname if sub else comfy_dir / "output" / fname
                if local_f.is_file():
                    dest = dest_dir / fname
                    shutil.copyfile(local_f, dest)
                    saved.append(str(dest))

    if saved:
        first = saved[0]
        url = f"/api/video/stream?path={urllib.parse.quote(first)}"
        audit("video_result", {"prompt_id": prompt_id, "saved": first, "total": len(saved)})
        return {
            "status": "DONE",
            "prompt_id": prompt_id,
            "video_path": first,
            "video_url": url,
            "videos": saved,
            "message": "Vídeo generado con éxito"
        }

    if completed:
        return {"status": "ERROR", "prompt_id": prompt_id, "message": "ComfyUI finalizó pero no se encontró vídeo en la salida."}

    return {"status": "RUNNING", "prompt_id": prompt_id, "message": "Procesando vídeo..."}


def minimax_video_status(prompt_id, cfg=None) -> dict:
    """Alias de compatibilidad para minimax_video_status."""
    return video_status(prompt_id, cfg)



# ---------------------------------------------------------------------------
# v2.3 - Mejora #1: audio de referencia REAL en MiniMax H3
#
# Copia el audio ya generado al input/ de ComfyUI y lo conecta como referencia
# independiente (ref_audios.ref_audio_0) para que el video se ambiente con el
# timbre/energia reales de la cancion. No sustituye el audio del clip (eso lo
# hace /api/video/concat superponiendo el audio real al final). Redefine
# build_minimax_h3_workflow y generate_minimax_video de v2.2 con un parametro
# opcional nuevo (compatibles con las llamadas anteriores sin referencia).
# ---------------------------------------------------------------------------

def _stage_comfy_reference_audio(raw_path, cfg=None) -> str:
    """Copia un audio ya generado al input/ de ComfyUI; devuelve el nombre de archivo
    (relativo a input/) que espera el nodo LoadAudio."""
    cfg = cfg or load_config()
    src = resolve_audio_file(raw_path, cfg)
    comfy_dir = get_paths(cfg)["comfy"]
    if not comfy_dir or not comfy_dir.is_dir():
        raise FileNotFoundError(
            "No se encuentra ComfyUI (paths.comfy_dir en config.json); hace falta para copiar "
            "el audio de referencia a su carpeta input/"
        )
    input_dir = comfy_dir / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    safe_name = f"dany_ref_{secrets.token_hex(6)}{src.suffix.lower()}"
    shutil.copyfile(src, input_dir / safe_name)
    return safe_name


def build_minimax_h3_workflow(
    prompt, width, height, length, seed,
    filename_prefix="video/MiniMax_H3_DanyStudio", ref_audio_filename=None,
) -> dict:
    """Grafo API-format (aplanado) para generar video 9:16 con MiniMax H3. Si se indica
    ref_audio_filename (ya copiado al input/ de ComfyUI via _stage_comfy_reference_audio),
    se conecta como referencia de audio independiente (ref_audios.ref_audio_0)."""
    wf = {
        "h3_vae_video": {"class_type": "VAELoader", "inputs": {"vae_name": H3_VAE_VIDEO}},
        "h3_vae_audio": {"class_type": "VAELoader", "inputs": {"vae_name": H3_VAE_AUDIO}},
        "h3_unet": {"class_type": "UNETLoader", "inputs": {"unet_name": H3_UNET_MODEL, "weight_dtype": "default"}},
        "h3_clip": {"class_type": "CLIPLoader", "inputs": {"clip_name": H3_CLIP_MODEL, "type": "minimax", "device": "default"}},
        "h3_ref2video": {
            "class_type": "MiniMaxH3ReferenceToVideo",
            "inputs": {
                "clip": ["h3_clip", 0],
                "vae": ["h3_vae_video", 0],
                "audio_vae": ["h3_vae_audio", 0],
                "prompt": prompt,
                "width": int(width),
                "height": int(height),
                "length": int(length),
                "ref_image_size": "match",
            },
        },
        "h3_noise": {"class_type": "RandomNoise", "inputs": {"noise_seed": int(seed)}},
        "h3_sampler_sel": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "res_multistep"}},
        "h3_scheduler": {
            "class_type": "BasicScheduler",
            "inputs": {"model": ["h3_unet", 0], "scheduler": "simple", "steps": 20, "denoise": 1},
        },
        "h3_guider": {"class_type": "BasicGuider", "inputs": {"model": ["h3_unet", 0], "conditioning": ["h3_ref2video", 0]}},
        "h3_sample": {
            "class_type": "SamplerCustomAdvanced",
            "inputs": {
                "noise": ["h3_noise", 0], "guider": ["h3_guider", 0], "sampler": ["h3_sampler_sel", 0],
                "sigmas": ["h3_scheduler", 0], "latent_image": ["h3_ref2video", 1],
            },
        },
        "h3_decode_video": {"class_type": "VAEDecode", "inputs": {"samples": ["h3_sample", 0], "vae": ["h3_vae_video", 0]}},
        "h3_decode_audio": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["h3_sample", 0], "vae": ["h3_vae_audio", 0]}},
        "h3_create_video": {
            "class_type": "CreateVideo",
            "inputs": {"images": ["h3_decode_video", 0], "audio": ["h3_decode_audio", 0], "fps": 24},
        },
        "h3_save": {
            "class_type": "SaveVideo",
            "inputs": {"video": ["h3_create_video", 0], "filename_prefix": filename_prefix, "format": "auto", "codec": "auto"},
        },
    }
    if ref_audio_filename:
        wf["h3_ref_audio_load"] = {"class_type": "LoadAudio", "inputs": {"audio": ref_audio_filename}}
        wf["h3_ref2video"]["inputs"]["ref_audios.ref_audio_0"] = ["h3_ref_audio_load", 0]
    return wf


def generate_minimax_video(payload: dict, cfg=None) -> dict:
    """(v2.3) Igual que la version anterior, mas soporte opcional de audio de referencia
    real (payload.reference_audio_path, una ruta de la Biblioteca de Audio)."""
    cfg = cfg or load_config()
    prompt = str(payload.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("El prompt de video no puede estar vacio")
    if len(prompt) > 4000:
        raise ValueError("El prompt de video es demasiado largo (max. 4000 caracteres)")

    duration = _num(payload, "duration_seconds", 8.0, 5.0, 15.0)
    quality = str(payload.get("quality") or "native").strip().lower()
    if quality not in H3_ASPECT_9_16:
        raise ValueError("quality debe ser 'native' o 'preview'")
    width, height = H3_ASPECT_9_16[quality]
    length = _h3_length_from_seconds(duration)

    seed_raw = payload.get("seed")
    seed = int(seed_raw) if seed_raw not in (None, "") else secrets.randbelow(2**31 - 1)

    title = slugify(payload.get("title", ""), default="video")
    prefix = f"video/MiniMax_H3_{title}"

    ref_audio_filename = None
    ref_audio_raw = payload.get("reference_audio_path")
    if ref_audio_raw:
        ref_audio_filename = _stage_comfy_reference_audio(ref_audio_raw, cfg)

    workflow = build_minimax_h3_workflow(
        prompt, width, height, length, seed, filename_prefix=prefix, ref_audio_filename=ref_audio_filename,
    )
    res = _comfy_http("POST", "/prompt", cfg, {"prompt": workflow, "client_id": secrets.token_hex(8)}, timeout=30)
    prompt_id = res.get("prompt_id") if isinstance(res, dict) else None
    if not prompt_id:
        raise RuntimeError(f"ComfyUI no devolvio prompt_id: {json.dumps(res, ensure_ascii=False)[:500]}")

    audit("minimax_h3_dispatch", {
        "prompt_id": prompt_id, "title": title, "duration_s": duration,
        "quality": quality, "width": width, "height": height, "length_frames": length,
        "reference_audio": bool(ref_audio_filename),
    })
    return {
        "status": "QUEUED", "prompt_id": prompt_id, "queue_number": res.get("number"),
        "width": width, "height": height, "length_frames": length,
        "duration_seconds": round(length / 24, 2), "seed": seed, "filename_prefix": prefix,
        "reference_audio_used": bool(ref_audio_filename),
    }


def _stage_comfy_image(raw_path: str, cfg=None) -> str:
    """Copia una imagen (portada o similar) al input/ de ComfyUI; devuelve el nombre del archivo."""
    cfg = cfg or load_config()
    src = resolve_image_file(raw_path, cfg)
    comfy_dir = get_paths(cfg)["comfy"]
    if not comfy_dir or not comfy_dir.is_dir():
        raise FileNotFoundError("paths.comfy_dir no configurado en config.json")
    input_dir = comfy_dir / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    safe_name = f"dany_img_{secrets.token_hex(6)}{src.suffix.lower()}"
    shutil.copyfile(src, input_dir / safe_name)
    return safe_name


def build_ltx_video_workflow(
    prompt: str,
    duration_seconds: float = 2.0,
    aspect_ratio: str = "9:16",
    steps: int = 10,
    guidance: float = 3.0,
    seed: int = None,
    filename_prefix: str = "video/LTX_2.5_DanyStudio",
    initial_image_filename: str = None,
) -> dict:
    aspect = str(aspect_ratio or "9:16").strip().lower()
    if aspect in ("16:9", "horizontal", "landscape"):
        width, height = 768, 512
    elif aspect in ("1:1", "square"):
        width, height = 512, 512
    else:  # "9:16", "vertical", "portrait"
        width, height = 512, 768

    # LTX-2.5 prefiere (N - 1) % 8 == 0 (ej: 49 frames para 2.0s a 24fps)
    raw_frames = round(float(duration_seconds) * 24)
    k = max(1, round((raw_frames - 1) / 8))
    length = k * 8 + 1

    if seed is None or seed == "":
        seed = secrets.randbelow(2**31 - 1)
    else:
        seed = int(seed)

    wf = {
        "unet": {
            "inputs": {
                "unet_name": "ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors",
                "weight_dtype": "default",
            },
            "class_type": "UNETLoader",
        },
        "clip": {
            "inputs": {
                "clip_name": "gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors",
                "type": "ltxv",
                "device": "default",
            },
            "class_type": "CLIPLoader",
        },
        "vae": {
            "inputs": {
                "vae_name": "ltx-2.5-video-vae-conv-bf16.safetensors",
            },
            "class_type": "VAELoader",
        },
        "latent": {
            "inputs": {
                "width": width,
                "height": height,
                "length": length,
                "batch_size": 1,
            },
            "class_type": "EmptyLTXVLatentVideo",
        },
        "pos_text": {
            "inputs": {
                "text": prompt,
                "clip": ["clip", 0],
            },
            "class_type": "CLIPTextEncode",
        },
        "neg_text": {
            "inputs": {
                "text": "blurry, low quality, jitter, flicker, distorted faces, extra limbs, bad anatomy, static picture",
                "clip": ["clip", 0],
            },
            "class_type": "CLIPTextEncode",
        },
        "conditioning": {
            "inputs": {
                "frame_rate": 24.0,
                "positive": ["pos_text", 0],
                "negative": ["neg_text", 0],
            },
            "class_type": "LTXVConditioning",
        },
    }

    if initial_image_filename:
        wf["load_img"] = {
            "inputs": {
                "image": initial_image_filename,
            },
            "class_type": "LoadImage",
        }
        wf["guide"] = {
            "inputs": {
                "positive": ["conditioning", 0],
                "negative": ["conditioning", 1],
                "vae": ["vae", 0],
                "latent": ["latent", 0],
                "image": ["load_img", 0],
                "frame_idx": 0,
                "strength": 1.0,
            },
            "class_type": "LTXVAddGuide",
        }
        sampler_pos = ["guide", 0]
        sampler_neg = ["guide", 1]
        sampler_latent = ["guide", 2]
    else:
        sampler_pos = ["conditioning", 0]
        sampler_neg = ["conditioning", 1]
        sampler_latent = ["latent", 0]

    wf["sampler"] = {
        "inputs": {
            "seed": seed,
            "steps": int(steps),
            "cfg": float(guidance),
            "sampler_name": "euler",
            "scheduler": "normal",
            "denoise": 1.0,
            "model": ["unet", 0],
            "positive": sampler_pos,
            "negative": sampler_neg,
            "latent_image": sampler_latent,
        },
        "class_type": "KSampler",
    }

    wf["decode"] = {
        "inputs": {
            "samples": ["sampler", 0],
            "vae": ["vae", 0],
        },
        "class_type": "VAEDecode",
    }

    wf["save_video"] = {
        "inputs": {
            "frame_rate": 24,
            "loop_count": 0,
            "filename_prefix": filename_prefix,
            "format": "video/h264-mp4",
            "pix_fmt": "yuv420p",
            "crf": 19,
            "save_metadata": True,
            "trim_to_audio": False,
            "pingpong": False,
            "save_output": True,
            "images": ["decode", 0],
        },
        "class_type": "VHS_VideoCombine",
    }

    return wf


def generate_video(payload: dict, cfg=None) -> dict:
    """Generador unificado de vídeo con ComfyUI (LTX-2.5 22B INT8 ConvRot por defecto o MiniMax H3)."""
    cfg = cfg or load_config()
    engine = str(payload.get("engine") or "ltx-2.5").strip().lower()

    if "minimax" in engine:
        return generate_minimax_video(payload, cfg)

    prompt = str(payload.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("El prompt de vídeo no puede estar vacío")
    if len(prompt) > 4000:
        raise ValueError("El prompt de vídeo es demasiado largo")

    duration = _num(payload, "duration_seconds", 2.0, 1.0, 30.0)
    aspect = str(payload.get("aspect_ratio") or payload.get("aspect") or "9:16").strip()
    steps = _num(payload, "steps", 10, 4, 30, cast=int)
    guidance = _num(payload, "guidance_scale", 3.0, 1.0, 15.0, cast=float)
    seed_raw = payload.get("seed")
    seed = int(seed_raw) if seed_raw not in (None, "") else secrets.randbelow(2**31 - 1)

    title = slugify(payload.get("title", ""), default="video")
    prefix = f"video/LTX25_{title}"

    init_img_filename = None
    init_img_raw = payload.get("initial_image") or payload.get("image_path")
    if init_img_raw:
        init_img_filename = _stage_comfy_image(init_img_raw, cfg)

    workflow = build_ltx_video_workflow(
        prompt=prompt,
        duration_seconds=duration,
        aspect_ratio=aspect,
        steps=steps,
        guidance=guidance,
        seed=seed,
        filename_prefix=prefix,
        initial_image_filename=init_img_filename,
    )

    res = _comfy_http("POST", "/prompt", cfg, {"prompt": workflow, "client_id": secrets.token_hex(8)}, timeout=30)
    prompt_id = res.get("prompt_id") if isinstance(res, dict) else None
    if not prompt_id:
        raise RuntimeError(f"ComfyUI no devolvió prompt_id: {json.dumps(res, ensure_ascii=False)[:500]}")

    audit("video_dispatch", {
        "engine": "ltx-2.5", "prompt_id": prompt_id, "title": title,
        "duration_s": duration, "aspect": aspect, "has_image": bool(init_img_filename),
    })

    return {
        "status": "QUEUED",
        "prompt_id": prompt_id,
        "engine": "ltx-2.5",
        "queue_number": res.get("number"),
        "duration_seconds": duration,
        "aspect_ratio": aspect,
        "seed": seed,
        "initial_image_used": bool(init_img_filename),
        "message": "Vídeo encolado en ComfyUI con LTX-2.5 22B (Ultra rápido en RTX 5080)",
    }



# ---------------------------------------------------------------------------
# v2.3 - Mejora #2: planificador de escenas -> Short completo (varios clips H3
# concatenados con FFmpeg, con opcion de superponer el audio real de la cancion)
# ---------------------------------------------------------------------------

_VIDEO_SCENES_SYSTEM = """Eres el director de fotografia de un canal de Shorts musicales verticales (9:16).
Devuelve UNICAMENTE un objeto JSON valido (sin texto extra, sin markdown) con esta clave:
- "scenes": una lista de EXACTAMENTE {n} objetos, cada uno con:
  - "prompt": prompt EN INGLES, cinematico, para UN SOLO plano de MiniMax H3 (video 9:16). Estilo visual, iluminacion y UN movimiento de camara claro. Maximo 350 caracteres.
  - "duration_seconds": numero entre 5 y 15.
Las {n} escenas deben formar un arco narrativo coherente para todo el Short (hook inicial, desarrollo, climax, cierre segun corresponda) y variar el encuadre/movimiento de camara entre si para que no se vean repetitivas. No incluyas texto en pantalla salvo que tenga sentido narrativo. No menciones nombres de personas reales."""

_SCENE_CAMERA_VARIANTS = [
    "wide establishing shot, slow dolly in",
    "close-up tracking shot, shallow depth of field",
    "low-angle handheld shot, dynamic energetic movement",
    "slow motion detail shot, dramatic side lighting",
    "high-angle crane shot, sweeping camera move",
    "static tripod shot, subtle parallax, moody atmosphere",
]


def _fallback_video_scenes(genre, caption, bpm, num_scenes, scene_seconds) -> list:
    """Sin modelo IA conectado: variaciones de camara sobre la plantilla mas afin al genero/caption."""
    base = _fallback_video_prompt(genre, caption, bpm)
    base = re.sub(r",?\s*beat sync cuts every [\d.,]+ seconds?\.?\s*$", "", base, flags=re.IGNORECASE).rstrip(". ")
    scenes = []
    for i in range(num_scenes):
        variant = _SCENE_CAMERA_VARIANTS[i % len(_SCENE_CAMERA_VARIANTS)]
        scenes.append({"prompt": (f"{base}, {variant}.")[:900], "duration_seconds": scene_seconds})
    return scenes


def _normalize_scenes(raw, num_scenes, default_seconds) -> list:
    if not isinstance(raw, list) or not raw:
        raise LLMError("El modelo no devolvio una lista de escenas")
    scenes = []
    for item in raw[:6]:
        if not isinstance(item, dict):
            continue
        prompt = str(item.get("prompt") or "").strip()[:900]
        if not prompt:
            continue
        seconds = _clamp(item.get("duration_seconds"), 5.0, 15.0, default_seconds)
        scenes.append({"prompt": prompt, "duration_seconds": seconds})
    if len(scenes) < 2:
        raise LLMError("El modelo no devolvio suficientes escenas validas")
    return scenes[:num_scenes]


def generate_video_scenes(payload: dict) -> dict:
    title = str(payload.get("title") or "").strip()[:120]
    genre = str(payload.get("genre") or "").strip()[:120]
    caption = str(payload.get("caption") or "").strip()[:1500]
    lyrics = str(payload.get("lyrics") or "").strip()[:4000]
    key = str(payload.get("key") or "").strip()
    bpm = payload.get("bpm")
    num_scenes = int(_num(payload, "num_scenes", 4, 2, 6))
    scene_seconds = _num(payload, "scene_seconds", 8.0, 5.0, 15.0)
    if not (genre or caption):
        raise ValueError("Indica al menos el genero o el caption musical de la cancion")

    fallback = _fallback_video_scenes(genre, caption, bpm, num_scenes, scene_seconds)
    cfg = load_config()
    llm = cfg["llm"]
    if not (llm["enabled"] and llm["model"]):
        return {"scenes": fallback, "source": "template"}

    try:
        s = _resolve_llm(None, cfg)
        parts = [
            f"Titulo: {title}" if title else "",
            f"Genero: {genre}" if genre else "",
            f"BPM: {bpm}" if bpm else "",
            f"Tonalidad: {key}" if key else "",
            f"Caption musical: {caption}" if caption else "",
            f"Fragmento de letra:\n{lyrics[:600]}" if lyrics else "",
            f"Numero de escenas pedidas: {num_scenes}",
        ]
        messages = [
            {"role": "system", "content": _VIDEO_SCENES_SYSTEM.format(n=num_scenes)},
            {"role": "user", "content": "\n".join(p for p in parts if p)},
        ]
        obj = _extract_json(llm_chat(messages, s, json_mode=True))
        scenes = _normalize_scenes(obj.get("scenes"), num_scenes, scene_seconds)
        audit("video_scenes", {"source": "llm", "model": s["model"], "n": len(scenes)})
        return {"scenes": scenes, "source": "llm", "llm_model": s["model"]}
    except (LLMError, ValueError) as exc:
        audit("video_scenes", {"source": "template_fallback", "error": str(exc)[:200]})
        return {"scenes": fallback, "source": "template", "llm_error": str(exc)}


def _ffmpeg_concat_quote(path: Path) -> str:
    # El demuxer concat de FFmpeg usa un mini-parser: las comillas simples se escapan duplicandolas.
    s = str(path).replace("'", "'\\''")
    return f"'{s}'"


def concat_video_clips(payload: dict, cfg=None) -> dict:
    """Une varios clips H3 (mismo codec/resolucion -> -c copy, sin recodificar) en un Short.
    Si se indica audio_path, sustituye el audio de los clips por ese audio real (recortado a
    la duracion del video con -shortest)."""
    cfg = cfg or load_config()
    raw_clips = payload.get("clips")
    if not isinstance(raw_clips, list) or len(raw_clips) < 2:
        raise ValueError("Se necesitan al menos 2 clips para concatenar")
    if len(raw_clips) > 8:
        raise ValueError("Demasiados clips (max. 8)")
    clips = [resolve_video_file(c, cfg) for c in raw_clips]

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise FileNotFoundError("FFmpeg no esta en el PATH")

    title = slugify(payload.get("title", ""), default="short")
    dest_dir = BASE_DIR / "outputs" / "minimax_videos"
    dest_dir.mkdir(parents=True, exist_ok=True)
    out_path = dest_dir / f"Short_{title}_{secrets.token_hex(3)}.mp4"
    list_file = dest_dir / f"_concat_{secrets.token_hex(4)}.txt"
    list_file.write_text("\n".join(f"file {_ffmpeg_concat_quote(c)}" for c in clips), encoding="utf-8")

    audio_path = payload.get("audio_path")
    used_audio = None
    try:
        if audio_path:
            song = resolve_audio_file(audio_path, cfg)
            cmd = [
                ffmpeg, "-hide_banner", "-nostdin", "-y",
                "-f", "concat", "-safe", "0", "-i", str(list_file),
                "-i", str(song),
                "-map", "0:v:0", "-map", "1:a:0",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                "-shortest", str(out_path),
            ]
            used_audio = str(song)
        else:
            cmd = [
                ffmpeg, "-hide_banner", "-nostdin", "-y",
                "-f", "concat", "-safe", "0", "-i", str(list_file),
                "-c", "copy", str(out_path),
            ]
        res = _run(cmd, 300)
        if res.returncode != 0:
            raise RuntimeError(
                f"FFmpeg fallo al concatenar (¿todos los clips usan la misma calidad/resolucion?): {_last_line(res.stderr)}"
            )
    finally:
        list_file.unlink(missing_ok=True)

    audit("video_concat", {"clips": len(clips), "audio": used_audio, "output": str(out_path)})
    return {"status": "DONE", "output_file": str(out_path), "clips_used": len(clips), "audio_used": used_audio}


# ---------------------------------------------------------------------------
# v2.3 - Mejora #3: ejecutar ACE-Step localmente desde la app (proceso en
# segundo plano, con sondeo de estado) + Mejora #5: sidecar de metadata junto
# al audio resultante (genero/bpm/key/caption), para poder reusarlo despues
# desde la Biblioteca de Audio sin volver a escribir nada.
# ---------------------------------------------------------------------------

_LOCAL_JOBS = {}
_LOCAL_JOBS_LOCK = threading.RLock()
_LOCAL_JOB_ID_RE = re.compile(r"^[A-Za-z0-9]{1,32}$")


def run_acestep_local(payload: dict, cfg=None) -> dict:
    cfg = cfg or load_config()
    with _LOCAL_JOBS_LOCK:
        for j in _LOCAL_JOBS.values():
            if j["proc"].poll() is None:
                raise PermissionError(
                    "Ya hay una generacion de ACE-Step en curso; espera a que termine antes de lanzar otra."
                )

    ace = get_paths(cfg)["acestep"]
    if not ace or not (ace / "cli.py").exists():
        raise FileNotFoundError("No se encuentra ACE-Step (paths.acestep_dir en config.json, o falta cli.py)")

    raw = str(payload.get("toml_path") or "").strip().strip('"')
    if not raw:
        raise ValueError("Indica toml_path (la ruta que devolvio /api/generate_music_config)")
    toml_path = Path(raw)
    if not toml_path.is_absolute():
        raise ValueError("toml_path debe ser una ruta absoluta")
    toml_path = toml_path.resolve()
    if not toml_path.is_file() or toml_path.suffix.lower() != ".toml":
        raise FileNotFoundError(f"No existe el archivo de configuracion: {toml_path}")
    out_dir = get_paths(cfg)["outputs"]
    if not is_within(toml_path, [out_dir]):
        raise PermissionError("toml_path esta fuera de la carpeta de salidas configurada")

    py = ace / ".venv" / "Scripts" / "python.exe"
    python_exe = str(py) if py.exists() else sys.executable

    job_id = secrets.token_hex(8)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"acestep_{job_id}.log"
    log_fh = open(log_path, "w", encoding="utf-8", errors="replace")
    try:
        proc = subprocess.Popen(
            [python_exe, "cli.py", "-c", str(toml_path)],
            cwd=str(ace), stdout=log_fh, stderr=subprocess.STDOUT,
        )
    except OSError as exc:
        log_fh.close()
        raise RuntimeError(f"No se pudo lanzar ACE-Step: {exc}")

    meta = {
        "title": str(payload.get("title") or "").strip()[:120],
        "genre": str(payload.get("genre") or "").strip()[:120],
        "bpm": payload.get("bpm"),
        "key": str(payload.get("key") or "").strip()[:40],
        "caption": str(payload.get("caption") or "").strip()[:1500],
    }
    with _LOCAL_JOBS_LOCK:
        _LOCAL_JOBS[job_id] = {
            "proc": proc, "log_fh": log_fh, "log_path": log_path,
            "started": time.time(), "out_dir": out_dir, "meta": meta, "resolved": False,
        }
    audit("acestep_run", {"job_id": job_id, "toml": str(toml_path)})
    return {"job_id": job_id, "status": "RUNNING"}


def _read_audio_sidecar(audio_path: Path):
    sidecar = audio_path.parent / (audio_path.name + ".json")
    if not sidecar.is_file():
        return None
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return {
        "title": str(data.get("title") or "")[:120],
        "genre": str(data.get("genre") or "")[:120],
        "bpm": data.get("bpm"),
        "key": str(data.get("key") or "")[:40],
        "caption": str(data.get("caption") or "")[:1500],
    }


def acestep_job_status(job_id, cfg=None) -> dict:
    job_id = str(job_id or "")
    if not _LOCAL_JOB_ID_RE.match(job_id):
        raise ValueError("job_id no valido")
    with _LOCAL_JOBS_LOCK:
        job = _LOCAL_JOBS.get(job_id)
    if not job:
        raise ValueError("job_id desconocido (¿se reinicio el servidor despues de lanzarlo?)")

    proc = job["proc"]
    ret = proc.poll()
    log_tail = ""
    try:
        log_tail = job["log_path"].read_text(encoding="utf-8", errors="replace")[-4000:]
    except OSError:
        pass

    if ret is None:
        return {"status": "RUNNING", "job_id": job_id, "log": log_tail}

    if not job["resolved"]:
        try:
            job["log_fh"].close()
        except OSError:
            pass
        job["resolved"] = True

    if ret != 0:
        return {"status": "ERROR", "job_id": job_id, "returncode": ret, "log": log_tail}

    output_file = job.get("output_file")
    if output_file is None:
        newest, newest_mtime = None, job["started"]
        try:
            for p in job["out_dir"].rglob("*"):
                if p.is_file() and p.suffix.lower() in AUDIO_EXTS:
                    try:
                        mt = p.stat().st_mtime
                    except OSError:
                        continue
                    if mt > newest_mtime:
                        newest, newest_mtime = p, mt
        except OSError:
            pass
        if newest is not None:
            sidecar = newest.parent / (newest.name + ".json")
            try:
                sidecar.write_text(json.dumps(job["meta"], ensure_ascii=False, indent=2), encoding="utf-8")
            except OSError:
                pass
            output_file = str(newest)
        else:
            output_file = ""
        job["output_file"] = output_file

    audit("acestep_result", {"job_id": job_id, "output": output_file or None})
    return {"status": "DONE", "job_id": job_id, "output_file": output_file or None, "log": log_tail}


def list_audio_tracks(cfg=None, limit=60) -> dict:
    """(v2.3) Igual que la version de v2.2, mas metadata guardada (genero/bpm/key/caption)
    si existe un sidecar .json junto al audio (lo escribe run_acestep_local al terminar)."""
    cfg = cfg or load_config()
    items = []
    seen = set()
    for root in audio_roots(cfg):
        root = Path(root)
        if not root.is_dir():
            continue
        try:
            for p in root.rglob("*"):
                if not p.is_file() or p.suffix.lower() not in AUDIO_EXTS:
                    continue
                try:
                    rp = p.resolve()
                except OSError:
                    continue
                # Ignorar carpetas virtuales, node_modules y caches
                parts_lower = {part.lower() for part in rp.parts}
                if any(bad in parts_lower for bad in (".venv", "node_modules", "__pycache__", ".git", "site-packages")):
                    continue
                if rp in seen:
                    continue
                seen.add(rp)
                try:
                    st = rp.stat()
                except OSError:
                    continue
                display_name = rp.stem
                if display_name.lower() in ("audio", "output") and rp.parent.name:
                    display_name = f"{rp.parent.name} ({rp.name})"
                items.append({
                    "path": str(rp), "name": display_name, "ext": rp.suffix.lower(),
                    "size": st.st_size, "mtime": st.st_mtime, "meta": _read_audio_sidecar(rp),
                })
        except OSError:
            continue
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return {"tracks": items[:limit]}




# ---------------------------------------------------------------------------
# v2.6 - YuE2 (yue2-infer): pipeline lyrics-to-song integrado en el Studio
# ---------------------------------------------------------------------------

_YUE2_JOBS = {}
_YUE2_JOBS_LOCK = threading.RLock()


def _yue2_paths(cfg=None) -> dict:
    cfg = cfg or load_config()
    y = cfg.get("yue2") if isinstance(cfg.get("yue2"), dict) else {}
    d = _opt_path(y.get("dir")) or (_HOME / "Music" / "YuE2")
    return {
        "dir": d,
        "python": d / ".venv" / "Scripts" / "python.exe",
        "script": d / "generate_16gb.py",
        "output_dir": _opt_path(y.get("output_dir")) or (d / "outputs"),
        "cot": str(y.get("cot") or "full"),
    }


def yue2_status(cfg=None) -> dict:
    """Disponibilidad de YuE2: rutas, python, script de generacion y trabajos en curso."""
    cfg = cfg or load_config()
    p = _yue2_paths(cfg)
    with _YUE2_JOBS_LOCK:
        running = [jid for jid, j in _YUE2_JOBS.items() if j["proc"].poll() is None]
    return {
        "dir": str(p["dir"]),
        "available": bool(p["dir"].is_dir() and p["script"].is_file()),
        "python_available": p["python"].is_file(),
        "script": str(p["script"]),
        "output_dir": str(p["output_dir"]),
        "cot": p["cot"],
        "running_jobs": running,
    }


def list_yue2_loras(cfg=None) -> list:
    """Devuelve la lista curada y descubierta de LoRAs para YuE 2."""
    cfg = cfg or load_config()
    loras = []
    seen_paths = set()

    dirs_to_scan = []
    hf_hub = Path.home() / ".cache" / "huggingface" / "hub"
    if hf_hub.is_dir():
        dirs_to_scan.append(hf_hub)
    p = get_paths(cfg)
    yue2_cfg_dir = _opt_path(cfg.get("yue2", {}).get("dir"))
    if yue2_cfg_dir and (yue2_cfg_dir / "models" / "loras").is_dir():
        dirs_to_scan.append(yue2_cfg_dir / "models" / "loras")
    lora_models_dir = BASE_DIR.parent / "lora_models"
    if lora_models_dir.is_dir():
        dirs_to_scan.append(lora_models_dir)

    curated = {
        "nar_lora_joint_v5.safetensors": ("Mothersuperior NAR v5 (Realaudio)", "Acoustic / Vocal"),
        "nar_lora_joint_v4.safetensors": ("Mothersuperior NAR v4 (Realaudio)", "Acoustic / Vocal"),
        "nar_lora_joint_v8.safetensors": ("Mothersuperior NAR v8 (Realaudio)", "Acoustic / Vocal"),
        "ar_lora_inst_v3abc.safetensors": ("Mothersuperior Instrumental CoT v3", "Instrumental"),
        "deathmetalv1_step-000600.safetensors": ("PDuncan Death Metal v1", "Metal"),
        "blgr_rhodope.safetensors": ("Bulgarian Voices - Rhodope", "Ethnic / Balkan"),
        "blgr_kargyraa.safetensors": ("Bulgarian Voices - Kargyraa Throat", "Ethnic / Balkan"),
        "blgr_steppe.safetensors": ("Bulgarian Voices - Steppe", "Ethnic / Balkan"),
        "mltnt_soundclash.safetensors": ("Militant Reggae - Soundclash", "Reggae / Dub"),
        "mltnt_roots.safetensors": ("Militant Reggae - Roots", "Reggae / Dub"),
        "trbdr_broadside.safetensors": ("Folk Troubadour - Broadside", "Folk / Acoustic"),
        "trbdr_ferryman.safetensors": ("Folk Troubadour - Ferryman", "Folk / Acoustic"),
    }

    for root_dir in dirs_to_scan:
        for f in root_dir.rglob("*.safetensors"):
            if not f.is_file():
                continue
            if "model.safetensors" in f.name or "temp" in f.name:
                continue
            f_str = str(f.resolve())
            if f_str in seen_paths:
                continue
            if "yue" not in f_str.lower() and "lora" not in f_str.lower():
                continue

            seen_paths.add(f_str)
            base_dir_name = f.parent.name
            if f.name == "lora.safetensors":
                grandpa = f.parent.parent.name if "models--" in f.parent.parent.name else f.parent.name
                clean_name = grandpa.replace("models--", "").replace("monsterovich--", "")
                name_str = f"Monsterovich - {clean_name} ({base_dir_name})"
                cat_str = "Rock & Epic"
            elif f.name in curated:
                name_str, cat_str = curated[f.name]
            else:
                name_str = f"{f.stem.replace('_', ' ').title()}"
                cat_str = "General"

            loras.append({
                "id": f.stem,
                "name": name_str,
                "category": cat_str,
                "filename": f.name,
                "path": f_str,
            })

    loras.sort(key=lambda x: (x["category"], x["name"]))
    return loras


def resolve_yue2_lora_path(ident: str, cfg=None) -> str:
    """Busca y resuelve la ruta absoluta de una LoRA para YuE 2."""
    if not ident:
        return ""
    cand = Path(ident).expanduser()
    if cand.is_file() and cand.suffix.lower() == ".safetensors":
        return str(cand.resolve())
    for item in list_yue2_loras(cfg):
        if ident == item["id"] or ident == item["path"] or ident == item["filename"] or ident.lower() in item["name"].lower():
            return item["path"]
    return ""


def yue2_generate(payload: dict, cfg=None) -> dict:
    """Lanza generate_16gb.py en segundo plano (perfil 16GB: FP8 + offload AR).
    Payload: style, lyrics (texto), output (nombre), cot ('full'|'melody'|'off'),
    reference_audio_path, lora_name, lora_scale."""
    cfg = cfg or load_config()
    with _YUE2_JOBS_LOCK:
        for j in _YUE2_JOBS.values():
            if j["proc"].poll() is None:
                raise PermissionError("Ya hay una generacion de YuE2 en curso; espera a que termine.")
    p = _yue2_paths(cfg)
    if not p["dir"].is_dir() or not p["script"].is_file():
        raise FileNotFoundError("No se encuentra YuE2 (paths.yue2.dir en config.json, o falta generate_16gb.py)")
    if not p["python"].is_file():
        raise FileNotFoundError(f"No existe el python de YuE2: {p['python']}")

    style = str(payload.get("style") or "").strip()[:600]
    lyrics = str(payload.get("lyrics") or "").strip()
    cot = str(payload.get("cot") or p["cot"]).strip().lower()
    if cot not in ("full", "melody", "off"):
        cot = "full"
    if not style and not lyrics:
        raise ValueError("Indica al menos un estilo o una letra para YuE2")

    out_dir = Path(str(payload.get("output_dir") or p["output_dir"]))
    out_dir.mkdir(parents=True, exist_ok=True)
    output_name = slugify(payload.get("output", "yue2_song"), default="yue2_song")

    # Referencia de audio (copia a input/ temporal de YuE2)
    ref_audio_file = None
    ref_audio_path = str(payload.get("reference_audio_path") or "").strip()
    if ref_audio_path:
        from studio_core import resolve_audio_file
        src = resolve_audio_file(ref_audio_path, cfg)
        yue2_input_dir = p["dir"] / "input"
        yue2_input_dir.mkdir(parents=True, exist_ok=True)
        safe_name = f"dany_ref_yue2_{secrets.token_hex(6)}{src.suffix.lower()}"
        ref_audio_file = yue2_input_dir / safe_name
        import shutil
        shutil.copyfile(src, ref_audio_file)

    # LoRA music (resolución automática en Hugging Face y carpetas locales)
    lora_raw = str(payload.get("lora_name") or payload.get("lora") or payload.get("lora_path") or "").strip()
    lora_scale = _num(payload, "lora_scale", 1.0, 0.0, 2.0)
    lora_path = resolve_yue2_lora_path(lora_raw, cfg) if lora_raw else None


    # Letras a archivo temporal (el CLI las acepta por --lyrics-file)
    lyrics_file = None
    if lyrics:
        lyrics_file = LOG_DIR / f"yue2_lyrics_{secrets.token_hex(4)}.txt"
        _atomic_write(lyrics_file, lyrics + "\n")

    cmd = [str(p["python"]), str(p["script"]), "--cot", cot, "--output", str(out_dir / output_name)]
    if style:
        cmd += ["--style", style]
    if lyrics_file:
        cmd += ["--lyrics-file", str(lyrics_file)]
    if ref_audio_file:
        cmd += ["--reference-audio", str(ref_audio_file)]
    if lora_path:
        cmd += [f"--lora", lora_path]
        if lora_scale and lora_scale != 1.0:
            cmd += [f"--lora-scale", str(lora_scale)]

    job_id = secrets.token_hex(8)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"yue2_{job_id}.log"
    log_fh = open(log_path, "w", encoding="utf-8", errors="replace")
    try:
        proc = subprocess.Popen(cmd, cwd=str(p["dir"]), stdout=log_fh, stderr=subprocess.STDOUT)
    except OSError as exc:
        log_fh.close()
        raise RuntimeError(f"No se pudo lanzar YuE2: {exc}")

    with _YUE2_JOBS_LOCK:
        _YUE2_JOBS[job_id] = {
            "proc": proc, "log_fh": log_fh, "log_path": log_path,
            "started": time.time(), "out_dir": out_dir, "output_name": output_name, "resolved": False,
        }
    audit("yue2_run", {"job_id": job_id, "style": style[:200], "cot": cot, "has_lyrics": bool(lyrics)})
    return {"job_id": job_id, "status": "RUNNING", "command": " ".join(cmd[:8]) + " ...", "output_dir": str(out_dir)}


def yue2_job_status(job_id, cfg=None) -> dict:
    job_id = str(job_id or "")
    if not re.match(r"^[A-Za-z0-9]{1,32}$", job_id):
        raise ValueError("job_id no valido")
    with _YUE2_JOBS_LOCK:
        job = _YUE2_JOBS.get(job_id)
    if not job:
        raise ValueError("job_id desconocido (¿se reinicio el servidor despues de lanzarlo?)")

    proc = job["proc"]
    ret = proc.poll()
    log_tail = ""
    try:
        log_tail = job["log_path"].read_text(encoding="utf-8", errors="replace")[-4000:]
    except OSError:
        pass

    if ret is None:
        elapsed = int(time.time() - job["started"])
        return {"status": "RUNNING", "job_id": job_id, "elapsed_s": elapsed, "log": log_tail}

    if not job["resolved"]:
        try:
            job["log_fh"].close()
        except OSError:
            pass
        job["resolved"] = True

    if ret != 0:
        return {"status": "ERROR", "job_id": job_id, "returncode": ret, "log": log_tail}

    # Localizar el audio generado (mas reciente en el out_dir desde el arranque)
    output_file = None
    try:
        newest, newest_mtime = None, job["started"]
        for f in job["out_dir"].rglob("*"):
            if f.is_file() and f.suffix.lower() in AUDIO_EXTS:
                try:
                    mt = f.stat().st_mtime
                except OSError:
                    continue
                if mt > newest_mtime:
                    newest, newest_mtime = f, mt
        output_file = str(newest) if newest else ""
    except OSError:
        pass

    audit("yue2_result", {"job_id": job_id, "output": output_file or None})
    return {"status": "DONE", "job_id": job_id, "output_file": output_file or None, "log": log_tail}


_YUE2_COMPOSE_SYSTEM = """Eres el productor musical y letrista experto para YuE 2 (modelo de generacion musical full-song lyrics-to-song a 48kHz).
Tu labor es componer el prompt de estilo musical exacto y la letra cantada completa para la idea del usuario.
Devuelve UNICAMENTE un objeto JSON valido (sin markdown, sin texto extra) con estas claves:
- "title": nombre corto en snake_case ASCII sin espacios ni nombres de personas (ej: "sombras_en_la_carretera").
- "genre": nombre del genero en 2 o 3 palabras (ej: "Dark Synthwave", "Southern Rock", "Flamenco Chill").
- "bpm": entero adecuado para el tempo del estilo (ej: 84, 120, 138).
- "key": tonalidad musical (ej: "D minor", "G major", "E minor").
- "style": prompt de estilo para YuE 2 EN INGLES siguiendo estrictamente el estandar oficial de YuE:
  "[Language], [detailed subgenre], [vocal timbre, gender and emotion], [instruments: lead and rhythm], [production/atmosphere], [BPM] BPM, [Key]"
  Ejemplo: "Spanish, emotional acoustic southern rock ballad, gritty raspy male vocal, martin acoustic guitar, vintage dobro slide, warm upright piano, stomp kick, intimate production, 78 BPM, D minor"
- "lyrics": letra cantada completa y estructurada con etiquetas en minusculas [verse], [chorus], [bridge], [outro].
  Cada estrofa debe tener 4-6 versos con rima y ritmo.
  El estribillo [chorus] debe repetirse 2 o 3 veces para dar estructura natural a la cancion.
  Escribe la letra en el idioma solicitado ({lang}). Si es instrumental, devuelve "".
  REGLAS: Cero nombres de artistas reales. No incluyas acordes ni BPM dentro de la letra.
- "cot": modo simbolico recomendado ("full" para canciones melodicas y armonicas ricas, "melody" para solo melodia, "off" para beats electronicos rapidos).
"""


def _yue2_template_fallback(idea: str, genre_hint: str, lang: str) -> dict:
    text = fold(f"{idea} {genre_hint}")
    is_es = lang == "es"
    if any(k in text for k in ["house", "dance", "electronic", "club", "techno", "synth", "edm"]):
        return {
            "title": "neon_night_pulse",
            "genre": "Melodic Deep House",
            "bpm": 124,
            "key": "D minor",
            "style": f"{'Spanish' if is_es else 'English'}, dark melodic deep house, filtered hypnotic vocal chops, punchy 50Hz sub-bass, atmospheric analog pads, driving 909 groove, crisp club master, 124 BPM, D minor",
            "lyrics": """[verse]
Sombras que corren bajo el cristal
Luces de noche que no tienen final
Siento el latido bajo mi piel
Nada en el mundo nos puede detener

[chorus]
Sigue el pulso en la oscuridad
Quema la noche y déjate llevar
Hasta que el sol vuelva a brillar
En este ritmo nos vamos a quedar

[verse]
Reflejos de lluvia sobre el motor
Buscando el eco de una vieja canción
No mires atrás que el tiempo se va
Solo la música nos salvará

[chorus]
Sigue el pulso en la oscuridad
Quema la noche y déjate llevar
Hasta que el sol vuelva a brillar
En este ritmo nos vamos a quedar

[bridge]
Baja el silencio, sube el calor
Todo se funde alrededor

[chorus]
Sigue el pulso en la oscuridad
Quema la noche y déjate llevar
Hasta que el sol vuelva a brillar
En este ritmo nos vamos a quedar

[outro]
En la oscuridad
Déjate llevar""" if lang != "instrumental" else "",
            "cot": "full",
            "source": "template",
        }
    elif any(k in text for k in ["country", "blues", "folk", "acoustic", "balada", "ballad", "soul"]):
        return {
            "title": "huellas_en_el_polvo",
            "genre": "Southern Folk Rock Ballad",
            "bpm": 76,
            "key": "G major",
            "style": f"{'Spanish' if is_es else 'English'}, acoustic narrative southern ballad, deep weathered raspy male baritone vocal, handcrafted martin acoustic guitar, dobro resonator slide, warm upright piano, slow emotional groove, 76 BPM, G major",
            "lyrics": """[verse]
Hay un camino que se pierde en el pinar
Donde los años no supieron perdonar
Llevo en las manos el frío del metal
Y en la mirada la huella del vendaval

[chorus]
Cuando la noche se vuelve de sal
Hay que aprender a no mirar atrás
Entre las brasas que deja el ayer
Solo la tierra te enseña a volver

[verse]
Dejé recuerdos guardados en un cajón
Junto a las cartas que nunca tuvieron voz
Nadie pregunta lo que pesó el dolor
Solo si el viento borró tu dirección

[chorus]
Cuando la noche se vuelve de sal
Hay que aprender a no mirar atrás
Entre las brasas que deja el ayer
Solo la tierra te enseña a volver

[bridge]
El río canta lo que calla el honor
Y cada herida reclama su perdón

[chorus]
Cuando la noche se vuelve de sal
Hay que aprender a no mirar atrás
Entre las brasas que deja el ayer
Solo la tierra te enseña a volver

[outro]
Solo el viento en el pinar
Polvo y sal""" if lang != "instrumental" else "",
            "cot": "full",
            "source": "template",
        }
    else:
        return {
            "title": "fuego_en_la_carretera",
            "genre": "Hard Alternative Rock",
            "bpm": 134,
            "key": "E minor",
            "style": f"{'Spanish' if is_es else 'English'}, epic alternative rock, powerful gritty male vocal, distorted drop-D heavy guitars, thunderous acoustic drums, driving bassline, anthemic chorus build, 134 BPM, E minor",
            "lyrics": """[verse]
Ruedas calientes sobre el asfalto gris
Rompiendo el cerco que dejé detrás de mí
No quedan deudas que tenga que pagar
Solo horizontes abiertos de par en par

[chorus]
Fuego en la carretera, quema el motor
Rompe las cadenas, vence al temor
Por la tormenta y la tempestad
Nadie nos quita la libertad

[verse]
Luces que pasan como un relámpago fiel
Marcando el mapa dibujado en la piel
El eco ruge con fuerza en el metal
Y la garganta despierta el temporal

[chorus]
Fuego en la carretera, quema el motor
Rompe las cadenas, vence al temor
Por la tormenta y la tempestad
Nadie nos quita la libertad

[bridge]
Que rujan los cielos, que caiga la ley
En esta autopista no manda ningún rey

[chorus]
Fuego en la carretera, quema el motor
Rompe las cadenas, vence al temor
Por la tormenta y la tempestad
Nadie nos quita la libertad

[outro]
Quema el motor
Libertad""" if lang != "instrumental" else "",
            "cot": "full",
            "source": "template",
        }


def yue2_compose(payload: dict, cfg=None) -> dict:
    """Genera estilo y letra optimizados para YuE 2 con el LLM activo (NIM u Ollama)."""
    cfg = cfg or load_config()
    idea = str(payload.get("idea") or "").strip()[:1000]
    genre = str(payload.get("genre") or "auto").strip()
    lang = str(payload.get("lang") or "es").strip().lower()
    if not idea and genre in ("auto", ""):
        raise ValueError("Indica una idea o temática para generar con IA.")

    fallback = _yue2_template_fallback(idea, genre, lang)
    fallback["prompt"] = fallback["style"]

    overrides = {}
    if payload.get("provider"):
        overrides["provider"] = payload["provider"]
    if payload.get("model"):
        overrides["model"] = payload["model"]
    if payload.get("base_url"):
        overrides["base_url"] = payload["base_url"]
    s = _resolve_llm(overrides if overrides else None, cfg)

    if not (s.get("enabled") and s.get("model")):
        return fallback

    try:
        lang_label = "Español" if lang == "es" else ("English" if lang == "en" else "Instrumental")
        messages = [
            {"role": "system", "content": _YUE2_COMPOSE_SYSTEM.format(lang=lang_label)},
            {"role": "user", "content": f"Idea del usuario: {idea}\nGenero preferido: {genre}\nIdioma: {lang_label}"},
        ]
        raw_reply = llm_chat(messages, s, json_mode=True)
        obj = _extract_json(raw_reply)
        style = str(obj.get("style") or fallback["style"]).strip()[:600]
        lyrics = str(obj.get("lyrics") if "lyrics" in obj else fallback["lyrics"]).strip()
        cot = str(obj.get("cot") or fallback["cot"]).strip().lower()
        if cot not in ("full", "melody", "off"):
            cot = "full"
        title = slugify(obj.get("title", ""), default=fallback["title"])
        genre_out = str(obj.get("genre") or fallback["genre"]).strip()[:60]
        bpm = _clamp(obj.get("bpm"), 50, 220, fallback["bpm"], int)
        key = str(obj.get("key") or fallback["key"]).strip()[:30]

        res = {
            "status": "COMPOSED",
            "title": title,
            "genre": genre_out,
            "bpm": bpm,
            "key": key,
            "style": style,
            "prompt": style,
            "lyrics": lyrics,
            "cot": cot,
            "output": title,
            "source": "llm",
            "llm_model": s["model"],
            "provider": s["provider"],
        }
        audit("yue2_compose", {"source": "llm", "model": s["model"], "title": title})
        return res
    except (LLMError, ValueError) as exc:
        fallback["llm_error"] = str(exc)
        audit("yue2_compose", {"source": "template_fallback", "error": str(exc)[:200]})
        return fallback


def yue2_compose_and_run(payload: dict, cfg=None) -> dict:
    """Pipeline integral YuE 2: compone con IA y lanza inmediatamente la generación en GPU."""
    comp = yue2_compose(payload, cfg)
    gen_payload = {
        "style": comp["style"],
        "lyrics": comp["lyrics"],
        "cot": comp.get("cot", "full"),
        "output": comp["output"],
    }
    run_res = yue2_generate(gen_payload, cfg)
    run_res.update({
        "composed": comp,
        "style": comp["style"],
        "output_name": comp["output"],
    })
    return run_res


# ---------------------------------------------------------------------------
# CLI de utilidades
# ---------------------------------------------------------------------------

def _cli():
    import argparse

    ap = argparse.ArgumentParser(description="Utilidades de Dany AI Studio")
    ap.add_argument("--init", action="store_true", help="Crea config.json y secrets.json si no existen")
    ap.add_argument("--show-colab-secret", action="store_true", help="Muestra el secreto HMAC para pegarlo en Colab Secrets")
    ap.add_argument("--rotate-colab-secret", action="store_true", help="Genera un secreto HMAC nuevo")
    ap.add_argument("--llm-test", action="store_true", help="Prueba la conexion con el modelo configurado")
    args = ap.parse_args()

    if args.init:
        if not CONFIG_FILE.exists():
            save_config(load_config())
            print(f"Creado {CONFIG_FILE}")
        load_secrets()
        print(f"Secretos en {SECRETS_FILE}")
    if args.rotate_colab_secret:
        sec = load_secrets()
        sec["colab_hmac_secret"] = secrets.token_urlsafe(32)
        save_secrets(sec)
        print("Secreto rotado. Actualiza DANY_COLAB_HMAC en Colab Secrets.")
    if args.show_colab_secret or args.rotate_colab_secret:
        print("DANY_COLAB_HMAC =", load_secrets()["colab_hmac_secret"])
    if args.llm_test:
        try:
            print(json.dumps(llm_test(), ensure_ascii=False, indent=2))
        except (LLMError, ValueError) as exc:
            print("ERROR:", exc)
    if not any(vars(args).values()):
        ap.print_help()


if __name__ == "__main__":
    _cli()
