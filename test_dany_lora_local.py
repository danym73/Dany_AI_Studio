import json
import urllib.request
import urllib.parse
import os
import sys
import time
import shutil

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import studio_core as _core
    _PATHS = _core.get_paths()
except Exception:
    _PATHS = None
COMFY_OUTPUT_DIR = os.environ.get("COMFY_OUTPUT_DIR") or (
    str(_PATHS["comfy"] / "output") if _PATHS and _PATHS["comfy"] else ""
)
BRIDGE_DIR = str(_PATHS["bridge"]) if _PATHS and _PATHS["bridge"] else ""
COMFY_URL = os.environ.get("COMFY_URL", "http://127.0.0.1:8188")
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outputs", "lora_tests")
os.makedirs(OUTPUT_DIR, exist_ok=True)

test_prompts = [
    {
        "id": "dany_rock_stage",
        "title": "Dany en Escenario de Rock",
        "prompt": "photo of dany_man, a handsome 40-year-old man, singing passionately on a rock concert stage, wearing distressed black leather jacket, gripping vintage chrome microphone, dramatic red and violet neon stage lights cutting through thick haze, hyper-realistic, 8k resolution, crisp detail",
        "width": 512,
        "height": 768,
        "seed": 42
    },
    {
        "id": "dany_outlaw_car",
        "title": "Dany con Coche Clásico Outlaw",
        "prompt": "photo of dany_man, a handsome 40-year-old man, standing in the rain next to a black vintage muscle car, dark highway at night, wet asphalt with neon reflections, light stubble, intense gaze, cinematic film still, masterpiece",
        "width": 768,
        "height": 512,
        "seed": 105
    },
    {
        "id": "dany_acoustic_studio",
        "title": "Dany en Estudio Acústico",
        "prompt": "photo of dany_man, a handsome 40-year-old man, sitting with an acoustic wood guitar in a cozy music recording studio, warm golden lighting, sound acoustic panels and vintage audio mixer in background, calm charismatic smile, portrait photography",
        "width": 512,
        "height": 768,
        "seed": 777
    }
]

def build_workflow(prompt_text, width, height, seed):
    workflow = {
        "3": {
            "inputs": {
                "seed": seed,
                "steps": 25,
                "cfg": 7.0,
                "sampler_name": "dpmpp_2m",
                "scheduler": "karras",
                "denoise": 1.0,
                "model": ["10", 0],
                "positive": ["6", 0],
                "negative": ["7", 0],
                "latent_image": ["5", 0]
            },
            "class_type": "KSampler"
        },
        "4": {
            "inputs": {
                "ckpt_name": "realisticComicBook_v10.safetensors"
            },
            "class_type": "CheckpointLoaderSimple"
        },
        "10": {
            "inputs": {
                "lora_name": "dany_man_lora.safetensors",
                "strength_model": 0.85,
                "strength_clip": 0.85,
                "model": ["4", 0],
                "clip": ["4", 1]
            },
            "class_type": "LoraLoader"
        },
        "5": {
            "inputs": {
                "width": width,
                "height": height,
                "batch_size": 1
            },
            "class_type": "EmptyLatentImage"
        },
        "6": {
            "inputs": {
                "text": prompt_text,
                "clip": ["10", 1]
            },
            "class_type": "CLIPTextEncode"
        },
        "7": {
            "inputs": {
                "text": "blurry, low quality, deformed, bad anatomy, bad hands, extra limbs, cartoon, illustration, distorted face, oversaturated, watermark",
                "clip": ["10", 1]
            },
            "class_type": "CLIPTextEncode"
        },
        "8": {
            "inputs": {
                "samples": ["3", 0],
                "vae": ["4", 2]
            },
            "class_type": "VAEDecode"
        },
        "9": {
            "inputs": {
                "filename_prefix": "dany_lora_test",
                "images": ["8", 0]
            },
            "class_type": "SaveImage"
        }
    }
    return workflow

def queue_and_wait(workflow):
    payload = json.dumps({"prompt": workflow}).encode("utf-8")
    req = urllib.request.Request(f"{COMFY_URL}/prompt", data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            prompt_id = data["prompt_id"]
    except Exception as e:
        print(f"[ERROR] No se pudo enviar el prompt a ComfyUI: {e}")
        return None

    print(f"  [+] Tarea enviada a ComfyUI (ID: {prompt_id}). Generando...")
    start_time = time.time()
    while time.time() - start_time < 120:
        try:
            req_h = urllib.request.Request(f"{COMFY_URL}/history/{prompt_id}")
            with urllib.request.urlopen(req_h, timeout=5) as resp:
                hist = json.loads(resp.read().decode("utf-8"))
                if prompt_id in hist:
                    outputs = hist[prompt_id].get("outputs", {})
                    for node_id, node_out in outputs.items():
                        if "images" in node_out and len(node_out["images"]) > 0:
                            img_info = node_out["images"][0]
                            fname = img_info["filename"]
                            subfolder = img_info.get("subfolder", "")
                            
                            # Obtener imagen via API o buscar en output
                            comfy_output_dir = COMFY_OUTPUT_DIR
                            src_path = os.path.join(comfy_output_dir, subfolder, fname) if subfolder else os.path.join(comfy_output_dir, fname)
                            return src_path
        except Exception:
            pass
        time.sleep(1.5)
    return None

def main():
    print("=========================================================")
    print("🚀 TEST DE LORA 'dany_man_lora' EN LOCAL (RTX 5080)")
    print("=========================================================")
    generated_files = []
    
    for i, item in enumerate(test_prompts, 1):
        print(f"\n[{i}/{len(test_prompts)}] Generando: {item['title']}...")
        wf = build_workflow(item["prompt"], item["width"], item["height"], item["seed"])
        src_path = queue_and_wait(wf)
        
        if src_path and os.path.exists(src_path):
            dst_name = f"{item['id']}.png"
            dst_path = os.path.join(OUTPUT_DIR, dst_name)
            shutil.copy2(src_path, dst_path)
            print(f"  [OK] Imagen guardada en: {dst_path}")
            generated_files.append(dst_path)
        else:
            print(f"  [!] No se pudo recuperar el archivo para {item['id']}")

    print("\n=========================================================")
    print(f"✅ FINALIZADO: {len(generated_files)}/{len(test_prompts)} imágenes generadas con éxito.")
    print(f"📁 Directorio: {OUTPUT_DIR}")
    print("=========================================================")

if __name__ == "__main__":
    main()
