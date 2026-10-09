#!/usr/bin/env python3
"""
DANY AI STUDIO - Servidor MCP (JSON-RPC 2.0 sobre stdio) v2.1

Permite que agentes IA (Claude Desktop, Cursor, ...) usen la suite. Toda la
logica esta en studio_core.py, igual que en la interfaz web, asi que las reglas
(validacion, masterizacion 2 pasadas, compositor con LLM manual) son identicas.

Seguridad: el envio a Colab desde MCP esta DESACTIVADO por defecto
(config.json -> colab.allow_mcp_dispatch). Un agente puede ser victima de
prompt injection; actívalo solo si confias en los agentes que conectas.
"""

import json
import sys
import traceback

import studio_core as core

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

TOOLS_DEFINITIONS = [
    {
        "name": "dany_get_system_info",
        "description": "Hardware (GPU NVIDIA, VRAM), estado de ACE-Step/ComfyUI/Colab Bridge, LoRAs detectadas y estado del modelo LLM conectado.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "dany_compose_music_with_ai",
        "description": "Compositor: a partir de una idea devuelve titulo, caption, letra limpia, BPM, tonalidad, LoRA y prompt de video LTX. Usa el LLM conectado en la web (config.json -> llm); si no hay ninguno, usa plantillas de respaldo.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "idea": {"type": "string", "description": "Idea o concepto de la cancion"},
                "style_pref": {"type": "string", "description": "Estilo opcional: rock, country, house, soul", "default": "auto"},
            },
            "required": ["idea"],
        },
    },
    {
        "name": "dany_generate_music_config",
        "description": "Genera un TOML valido para ACE-Step 1.5 (con escapado correcto, lora_path real y validacion).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Titulo de la pista"},
                "caption": {"type": "string", "description": "Prompt maestro"},
                "lyrics": {"type": "string", "description": "Letra limpia estructurada"},
                "duration": {"type": "number", "default": 200.0},
                "model_tier": {"type": "string", "default": "acestep-v15-xl-turbo"},
                "lora_name": {"type": "string", "description": "Nombre de la LoRA (carpeta en lora_models)"},
                "lora_scale": {"type": "number", "default": 1.0},
                "bpm": {"type": "integer"},
                "key": {"type": "string", "description": "Tonalidad, ej: 'E minor'"},
            },
            "required": ["title", "caption", "lyrics"],
        },
    },
    {
        "name": "dany_master_audio_ebu_r128",
        "description": "Masteriza un audio con FFmpeg de 2 pasadas al objetivo de config.json (por defecto -11 LUFS, TP -0.5 dBTP, 320 kbps). Solo archivos dentro de las carpetas permitidas.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "input_file": {"type": "string", "description": "Ruta absoluta del audio"},
                "output_file": {"type": "string", "description": "Ruta .mp3 de salida (opcional)"},
            },
            "required": ["input_file"],
        },
    },
    {
        "name": "dany_dispatch_colab_job",
        "description": "Envia un trabajo FIRMADO a Colab (solo 'python3 <script.py> args' con scripts de la allowlist). Desactivado por defecto para MCP.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "python3 /content/script.py --arg valor"},
                "job_id": {"type": "string", "description": "Identificador opcional (letras, numeros, _ y -)"},
            },
            "required": ["command"],
        },
    },
]


def handle_call_tool(name, args):
    args = args or {}
    if name == "dany_get_system_info":
        return core.system_info()
    if name == "dany_compose_music_with_ai":
        return core.compose(args.get("idea", ""), args.get("style_pref", "auto"))
    if name == "dany_generate_music_config":
        payload = dict(args)
        return core.create_music_config(payload)
    if name == "dany_master_audio_ebu_r128":
        return core.master_audio(args.get("input_file", ""), args.get("output_file"))
    if name == "dany_dispatch_colab_job":
        return core.dispatch_colab_job(args.get("command", ""), args.get("job_id"), source="mcp")
    raise ValueError(f"Herramienta desconocida: {name}")


def _send(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def run_mcp():
    """Bucle principal MCP (JSON-RPC 2.0 sobre stdio). Nunca muere por un error de herramienta."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            continue
        if not isinstance(req, dict):
            continue

        req_id = req.get("id")
        method = req.get("method")
        params = req.get("params") or {}

        if method == "initialize":
            _send({
                "jsonrpc": "2.0", "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "dany-ai-studio-mcp", "version": core.VERSION},
                },
            })
        elif method == "tools/list":
            _send({"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS_DEFINITIONS}})
        elif method == "tools/call":
            is_error = False
            try:
                text = json.dumps(handle_call_tool(params.get("name"), params.get("arguments")), ensure_ascii=False, indent=2)
            except (ValueError, PermissionError, FileNotFoundError, core.LLMError, RuntimeError) as exc:
                text, is_error = json.dumps({"error": str(exc)}, ensure_ascii=False), True
            except Exception:
                traceback.print_exc(file=sys.stderr)
                text, is_error = json.dumps({"error": "Error interno (ver stderr del servidor MCP)"}), True
            _send({"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": text}], "isError": is_error}})
        elif method == "ping":
            _send({"jsonrpc": "2.0", "id": req_id, "result": {}})
        elif req_id is not None and not str(method).startswith("notifications/"):
            _send({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Metodo no soportado: {method}"}})


if __name__ == "__main__":
    run_mcp()
