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
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outputs", "ltx_videos")
os.makedirs(OUTPUT_DIR, exist_ok=True)

def build_ltx_fl_workflow():
    workflow = {
        "1": {
            "inputs": {
                "unet_name": "ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors",
                "weight_dtype": "default"
            },
            "class_type": "UNETLoader"
        },
        "2": {
            "inputs": {
                "clip_name": "gemma4-12b-with-proj-ltx-2.5-bf16.safetensors",
                "type": "ltxv",
                "device": "default"
            },
            "class_type": "CLIPLoader"
        },
        "3": {
            "inputs": {
                "vae_name": "ltx-2.5-video-vae-bf16.safetensors"
            },
            "class_type": "VAELoader"
        },
        "4": {
            "inputs": {
                "width": 768,
                "height": 512,
                "length": 33,
                "batch_size": 1
            },
            "class_type": "EmptyLTXVLatentVideo"
        },
        "5": {
            "inputs": {
                "text": "cinematic transition, a handsome man moves from his vintage muscle car in the rainy night into a rock concert stage under vibrant spotlights, smooth fluid camera motion, crisp 8k photorealistic detail",
                "clip": ["2", 0]
            },
            "class_type": "CLIPTextEncode"
        },
        "6": {
            "inputs": {
                "text": "blurry, low quality, jitter, flicker, distorted faces, extra limbs, bad anatomy, static picture",
                "clip": ["2", 0]
            },
            "class_type": "CLIPTextEncode"
        },
        "7": {
            "inputs": {
                "frame_rate": 24.0,
                "positive": ["5", 0],
                "negative": ["6", 0]
            },
            "class_type": "LTXVConditioning"
        },
        "8": {
            "inputs": {
                "image": "dany_outlaw_car.png"
            },
            "class_type": "LoadImage"
        },
        "9": {
            "inputs": {
                "positive": ["7", 0],
                "negative": ["7", 1],
                "vae": ["3", 0],
                "latent": ["4", 0],
                "image": ["8", 0],
                "frame_idx": 0,
                "strength": 1.0
            },
            "class_type": "LTXVAddGuide"
        },
        "10": {
            "inputs": {
                "image": "dany_rock_stage.png"
            },
            "class_type": "LoadImage"
        },
        "11": {
            "inputs": {
                "positive": ["9", 0],
                "negative": ["9", 1],
                "vae": ["3", 0],
                "latent": ["9", 2],
                "image": ["10", 0],
                "frame_idx": -1,
                "strength": 1.0
            },
            "class_type": "LTXVAddGuide"
        },
        "12": {
            "inputs": {
                "seed": 42,
                "steps": 10,
                "cfg": 3.0,
                "sampler_name": "euler",
                "scheduler": "normal",
                "denoise": 1.0,
                "model": ["1", 0],
                "positive": ["11", 0],
                "negative": ["11", 1],
                "latent_image": ["11", 2]
            },
            "class_type": "KSampler"
        },
        "13": {
            "inputs": {
                "samples": ["12", 0],
                "vae": ["3", 0]
            },
            "class_type": "VAEDecode"
        },
        "14": {
            "inputs": {
                "frame_rate": 24,
                "loop_count": 0,
                "filename_prefix": "dany_ltx25_fl",
                "format": "video/h264-mp4",
                "pix_fmt": "yuv420p",
                "crf": 19,
                "save_metadata": True,
                "trim_to_audio": False,
                "pingpong": False,
                "save_output": True,
                "images": ["13", 0]
            },
            "class_type": "VHS_VideoCombine"
        }
    }
    return workflow

def queue_video(workflow):
    payload = json.dumps({"prompt": workflow}).encode("utf-8")
    req = urllib.request.Request(f"{COMFY_URL}/prompt", data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["prompt_id"]
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8")
        print(f"[ERROR HTTP]: {err}")
        return None
    except Exception as e:
        print(f"[ERROR]: {e}")
        return None

def monitor(prompt_id):
    print(f"[*] Render LTX-2.5 encolado con ID: {prompt_id}")
    print("[*] Generando vídeo con primer y último fotograma en RTX 5080...")
    start_time = time.time()
    while time.time() - start_time < 300:
        try:
            req = urllib.request.Request(f"{COMFY_URL}/history/{prompt_id}")
            with urllib.request.urlopen(req, timeout=5) as resp:
                hist = json.loads(resp.read().decode("utf-8"))
                if prompt_id in hist:
                    print("\n[OK] ¡Renderizado completado con éxito!")
                    outputs = hist[prompt_id].get("outputs", {})
                    for node_id, out_data in outputs.items():
                        if "gifs" in out_data:
                            for item in out_data["gifs"]:
                                fname = item.get("filename")
                                sub = item.get("subfolder", "")
                                comfy_out = COMFY_OUTPUT_DIR
                                p = os.path.join(comfy_out, sub, fname) if sub else os.path.join(comfy_out, fname)
                                return p
                        if "videos" in out_data:
                            for item in out_data["videos"]:
                                fname = item.get("filename")
                                sub = item.get("subfolder", "")
                                comfy_out = COMFY_OUTPUT_DIR
                                p = os.path.join(comfy_out, sub, fname) if sub else os.path.join(comfy_out, fname)
                                return p
        except Exception:
            pass
        sys.stdout.write(".")
        sys.stdout.flush()
        time.sleep(3)
    return None

def main():
    print("=========================================================")
    print("🎬 LTX-2.5 22B: VIDEO PRIMER Y ÚLTIMO FOTOGRAMA (LOCAL)")
    print("=========================================================")
    print("Fotograma 1: dany_outlaw_car.png")
    print("Fotograma 2: dany_rock_stage.png")
    print("Resolución: 768x512 | Frames: 33 (~1.4s) | Steps: 10")
    print("=========================================================")
    
    wf = build_ltx_fl_workflow()
    pid = queue_video(wf)
    if not pid:
        print("[!] No se pudo encolar el workflow.")
        return
        
    out_video = monitor(pid)
    if out_video and os.path.exists(out_video):
        dst = os.path.join(OUTPUT_DIR, "dany_ltx25_first_last_preview.mp4")
        shutil.copy2(out_video, dst)
        print(f"\n[OK] Vídeo guardado en: {dst}")
        if BRIDGE_DIR and os.path.isdir(os.path.join(BRIDGE_DIR, "outputs")):
            drive_dst = os.path.join(BRIDGE_DIR, "outputs", "dany_ltx25_first_last_preview.mp4")
            shutil.copy2(out_video, drive_dst)
            print(f"[OK] Sincronizado a Google Drive: {drive_dst}")
        else:
            print("[AVISO] Colab Bridge no disponible: no se copia a Google Drive.")
    else:
        print("\n[!] No se pudo encontrar el archivo de vídeo resultante.")

if __name__ == "__main__":
    main()
