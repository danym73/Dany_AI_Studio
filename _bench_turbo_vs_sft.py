import subprocess
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ACE = Path(r"c:\Users\danin\Documents\Codex\2026-07-26\yo-har-a-la-prueba-de\ACE-Step-1.5")
PY = ACE / ".venv" / "Scripts" / "python.exe"
EXAMPLE = ACE / "examples" / "text2music" / "example_01.json"

COMMON = [
    "--mode", "profile",
    "--lm-model", "acestep-5Hz-lm-1.7B",
    "--quantization", "int8_weight_only",
    "--offload-dit-to-cpu",
    "--duration", "45",
    "--example", str(EXAMPLE),
]

RUNS = [
    ("XL-TURBO (8 pasos)", ["--config-path", "acestep-v15-xl-turbo", "--inference-steps", "8"]),
    ("XL-SFT (50 pasos)", ["--config-path", "acestep-v15-xl-sft", "--inference-steps", "50"]),
]

for label, extra in RUNS:
    print(f"\n{'=' * 70}\n{label}\n{'=' * 70}", flush=True)
    t0 = time.time()
    proc = subprocess.run(
        [str(PY), "profile_inference.py", *COMMON, *extra],
        cwd=str(ACE), capture_output=True, text=True, timeout=1200,
        encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL,
    )
    dt = time.time() - t0
    print(proc.stdout[-4000:])
    if proc.returncode != 0:
        print("STDERR:", proc.stderr[-2000:])
        print(f"[{label}] FALLO (returncode={proc.returncode}) tras {dt:.1f}s")
    else:
        print(f"[{label}] wall time total del subproceso: {dt:.1f}s")

print("\nFIN")
