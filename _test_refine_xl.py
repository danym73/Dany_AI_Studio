"""
Test A/B: LoRA (roti-r0kkk) en Turbo como base, luego 'cover' de refinado en XL-Turbo
sin LoRA (XL no soporta esta LoRA: dim 2048 vs 2560 -- ver conversacion).

Genera DOS audios reales:
  1) turbo_lora  : acestep-v15-turbo + lora roti-r0kkk (voz/estilo entrenado)
  2) xl_refined  : acestep-v15-xl-turbo, task_type=cover, src_audio=el de arriba

No se toca el pipeline normal del Studio (studio_core.py) porque esto es un
experimento exploratorio, no una funcion permanente todavia.
"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

PROJ = Path(r"C:\Users\danin\Music\Dany_AI_Studio")
sys.path.insert(0, str(PROJ))
import studio_core as core

cfg = core.load_config()
ace = core.get_paths(cfg)["acestep"]
py = ace / ".venv" / "Scripts" / "python.exe"
out_dir = ace / "output"
DURATION = 45.0  # corto a proposito: es una prueba de oido, no la version final

CAPTION = (
    "roti-r0kkk, dark alternative rock, gothic grunge, raw distorted drop-D guitars, "
    "thick deep fuzz bass, punchy aggressive acoustic drums in a stone chamber, "
    "raspy emotional male vocals, intense pre-chorus build, explosive anthemic chorus, "
    "128 BPM, E minor"
)
LYRICS = """[Verse 1]
Cold echoes crawling on the concrete wall
Waiting for the heavy sky to finally fall

[Chorus]
Burn the altar, break the cage
Write the ending on the page
Through the storm and through the black
There is no turning back"""


def run_cli(toml_path: Path, label: str) -> Path:
    print(f"\n=== [{label}] Ejecutando cli.py ===\n  {toml_path}")
    t0 = time.time()
    proc = subprocess.run(
        [str(py), "cli.py", "-c", str(toml_path)],
        cwd=str(ace), capture_output=True, text=True, timeout=900, encoding="utf-8", errors="replace",
        stdin=subprocess.DEVNULL,  # si algo llama a input() sin querer, falla rapido en vez de colgarse
    )
    dt = time.time() - t0
    print(proc.stdout[-3000:])
    if proc.returncode != 0:
        print("STDERR:", proc.stderr[-3000:])
        raise SystemExit(f"[{label}] cli.py fallo (returncode={proc.returncode})")
    m = re.search(r"Path:\s*(.+\.(?:flac|mp3|wav))", proc.stdout)
    if not m:
        raise SystemExit(f"[{label}] No se encontro 'Path: ...' en la salida de cli.py")
    audio_path = Path(m.group(1).strip())
    print(f"[{label}] OK en {dt:.1f}s -> {audio_path}")
    return audio_path


def probe(path: Path) -> str:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=sample_rate,codec_name",
         "-of", "default=noprint_wrappers=1", str(path)],
        capture_output=True, text=True,
    )
    return r.stdout.strip().replace("\n", " | ")


def loudness(path: Path) -> str:
    r = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-i", str(path),
         "-af", "loudnorm=I=-11:TP=-0.5:LRA=7:print_format=json", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    s = r.stderr
    a, b = s.rfind("{"), s.rfind("}") + 1
    try:
        j = json.loads(s[a:b])
        return f"LUFS integrado: {j['input_i']} | True Peak: {j['input_tp']} dBTP"
    except Exception:
        return "(no se pudo medir loudness)"


# --- Etapa 1: Turbo + LoRA -------------------------------------------------
r1 = core.create_music_config({
    "title": "test_ab_stage1_turbo_lora",
    "model_tier": "acestep-v15-turbo",
    "lora_name": "roti-r0kkk",
    "lora_scale": 0.95,
    "caption": CAPTION,
    "lyrics": LYRICS,
    "duration": DURATION,
    "bpm": 128,
    "key": "E minor",
    "seed": 777,
}, cfg)
print("Aviso etapa 1:", r1["warnings"], "| lora cargada:", bool(r1["lora_path"]))
stage1_audio = run_cli(Path(r1["toml_path"]), "ETAPA 1 turbo+lora")

# --- Etapa 2: XL-Turbo, cover usando el audio de la etapa 1 como referencia ---
save_dir = core.get_paths(cfg)["outputs"]
toml2 = save_dir / "test_ab_stage2_xl_cover.toml"
lines = [
    "# Prueba A/B - etapa 2: refinado con XL-Turbo (sin LoRA) sobre el audio de la etapa 1",
    'task_type = "cover"',
    f"src_audio = {core.toml_str(str(stage1_audio))}",
    f"caption = {core.toml_str(CAPTION)}",
    f"lyrics = {core.toml_multiline(LYRICS)}",
    f"duration = {DURATION}",
    'config_path = "acestep-v15-xl-turbo"',
    f"lm_model_path = {core.toml_str(cfg['music']['lm_model'])}",
    f"inference_steps = {int(cfg['music']['inference_steps'])}",
    f"guidance_scale = {float(cfg['music']['guidance_scale'])}",
    "seed = 777",
    "use_random_seed = false",
    "audio_cover_strength = 0.8",   # alto = mas influencia del audio/codigos de referencia (preservar la voz)
    "cover_noise_strength = 0.6",   # punto medio: no es 0.0 (que segun el comentario del codigo = "no cover")
    "thinking = false",
    'audio_format = "flac"',
    f"save_dir = {core.toml_str(str(out_dir))}",
    "bpm = 128",
    'keyscale = "E minor"',
]
toml2.write_text("\n".join(lines) + "\n", encoding="utf-8")
stage2_audio = run_cli(toml2, "ETAPA 2 xl+cover")

print("\n" + "=" * 70)
print("RESUMEN")
print("=" * 70)
print(f"[1] Turbo + LoRA (roti-r0kkk):\n     {stage1_audio}\n     {probe(stage1_audio)}\n     {loudness(stage1_audio)}")
print(f"[2] XL-Turbo cover (refinado, sin LoRA):\n     {stage2_audio}\n     {probe(stage2_audio)}\n     {loudness(stage2_audio)}")
print("\nEscucha ambos y compara: si [2] suena mas nitido/detallado PERO conserva el caracter\n"
      "de la voz de [1], la cadena funciona. Si [2] pierde el caracter entrenado, sube\n"
      "audio_cover_strength (hacia 1.0) o baja cover_noise_strength y repite solo la etapa 2.")
