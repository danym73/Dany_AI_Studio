"""
Cancion real: Rumba Flamenca "Luna de Fuego".
Motor: acestep-v15-xl-sft (50 pasos) + LM 1.7B (el maximo real disponible; 4B no
esta descargado y tu tier de 16GB no lo soporta). Sin LoRA: ninguna de las 4
coincide con flamenco.
"""
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
out_dir = core.get_paths(cfg)["outputs"]
out_dir.mkdir(parents=True, exist_ok=True)

TITLE = "luna_de_fuego"
CAPTION = (
    "vibrant rumba flamenca, passionate spanish acoustic guitar rasgueado, "
    "rhythmic cajon percussion driving the groove, sharp layered palmas hand claps, "
    "fiery raspy cante flamenco male vocal delivery, warm tumbao bass, "
    "joyful festive live atmosphere, natural room reverb, acoustic ensemble production, "
    "132 BPM"
)
LYRICS = """[Intro]

[Verse 1]
Bajo la luna que enciende el mar
las palmas suenan sin descansar
el compas corre por la arena fina
y el corazon ya no se resigna

[Chorus]
Baila, baila, luna de fuego
enciende el alma, no tengo miedo
que suene fuerte la guitarra vieja
y esta noche nadie se queja

[Verse 2]
El cajon marca mi caminar
entre palmeras junto al lugar
donde el cante se vuelve oracion
y cada golpe es una razon

[Chorus]
Baila, baila, luna de fuego
enciende el alma, no tengo miedo
que suene fuerte la guitarra vieja
y esta noche nadie se queja

[Outro]
Luna de fuego, no te vayas ya"""

lines = [
    "# Rumba Flamenca - Luna de Fuego",
    'task_type = "text2music"',
    f"caption = {core.toml_str(CAPTION)}",
    f"lyrics = {core.toml_multiline(LYRICS)}",
    "duration = 200.0",
    "instrumental = false",
    'config_path = "acestep-v15-xl-sft"',
    f"lm_model_path = {core.toml_str(cfg['music']['lm_model'])}",
    "inference_steps = 50",
    f"guidance_scale = {float(cfg['music']['guidance_scale'])}",
    "seed = 20260923",
    "use_random_seed = false",
    'audio_format = "flac"',
    f"save_dir = {core.toml_str(str(out_dir))}",
    "bpm = 132",
    'vocal_language = "es"',
    "thinking = false",
]
toml_path = out_dir / f"{TITLE}.toml"
toml_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
print("TOML:", toml_path)

print("\n=== Generando (XL-SFT, 50 pasos, 200s) - puede tardar unos minutos ===")
t0 = time.time()
proc = subprocess.run(
    [str(py), "cli.py", "-c", str(toml_path)],
    cwd=str(ace), capture_output=True, text=True, timeout=1800,
    encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL,
)
dt = time.time() - t0
print(proc.stdout[-3000:])
if proc.returncode != 0:
    print("STDERR:", proc.stderr[-3000:])
    raise SystemExit(f"cli.py fallo (returncode={proc.returncode})")

paths = re.findall(r"Path:\s*(.+\.flac)", proc.stdout)
if not paths:
    raise SystemExit("No se encontro 'Path: ...' en la salida de cli.py")
raw_audio = Path(paths[0].strip())
print(f"\n[OK] Generado en {dt:.1f}s -> {raw_audio}")
if len(paths) > 1:
    print(f"     (variante adicional: {paths[1].strip()})")

# --- Masterizacion (2 pasadas EBU R128, igual que el resto del catalogo) ---
print("\n=== Masterizando ===")
m = core.master_audio(str(raw_audio), str(out_dir / f"{TITLE.title().replace('_', '_')}_Master.mp3"), cfg)
print(f"[MASTER OK] {m['output_file']}")
print(f"  Objetivo: {m['target_lufs']} LUFS | TP <= {m['true_peak']} dBTP | {m['bitrate']}")
print(f"  Medido (original): {m['measured']['input_lufs']} LUFS | TP {m['measured']['input_true_peak']} dBTP")
print("\nFIN")
