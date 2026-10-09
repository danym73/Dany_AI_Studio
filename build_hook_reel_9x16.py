import os
import sys
import subprocess
import time
import shutil

REEL_DURATION = 58.0
REEL_SEGMENTS_DIR = r"C:\Users\danin\Music\Dany_AI_Studio\logs\reel_segments"
os.makedirs(REEL_SEGMENTS_DIR, exist_ok=True)

AUDIO_FILE = r"C:\Users\danin\Music\Dany_AI_Studio\logs\reel_audio_mix_58s.wav"
OUT_SHORTS_CANAL = r"C:\Users\danin\Music\canal you.m.d\Shorts_9x16\Dany_AI_Studio_Reel_Hook_9x16.mp4"
OUT_SHORTS_VIDEOS = r"C:\Users\danin\Music\Videos\Shorts\Dany_AI_Studio_Reel_Hook_9x16.mp4"
OUT_DESKTOP = r"C:\Users\danin\Desktop\Dany_AI_Studio_Reel_Hook_9x16.mp4"

# Source assets (100% pristine cinema without burned-in text)
SHOT_BURNOUT = r"C:\Users\danin\Music\Videos\_tmp_16x9\shot_001.mp4"
SHOT_SUBWOOFER = r"C:\Users\danin\Music\Videos\_tmp_16x9\shot_020.mp4"
SHOT_COCKPIT = r"C:\Users\danin\Music\Videos\_tmp_16x9\shot_012.mp4"
SHOT_HYPERCAR = r"C:\Users\danin\Music\Videos\_tmp_16x9\shot_015.mp4"

TOKYO = r"C:\Users\danin\Music\Videos\Tokyo_Rain_Kenzy_Night_Drive_Official_16x9.mp4"
OVERDRIVE = r"C:\Users\danin\Music\Videos\Overdrive_Horizon_YuE2_NAR_Official_16x9.mp4"
DOODLE = r"C:\Users\danin\Downloads\Dany_AI_Studio.mp4"
INFO_VRAM = r"C:\Users\danin\Music\Dany_AI_Studio\branding\infografias\01_vram_bottleneck_infographic.mp4"
INFO_HYBRID = r"C:\Users\danin\Music\Dany_AI_Studio\branding\infografias\02_hybrid_architecture_infographic.mp4"
INFO_MASTER = r"C:\Users\danin\Music\Dany_AI_Studio\branding\infografias\03_ebu_r128_mastering_infographic.mp4"
INFO_DLSS = r"C:\Users\danin\Music\Dany_AI_Studio\branding\infografias\04_dlss5_video_pipeline_infographic.mp4"
TAB_MUSIC = r"C:\Users\danin\Music\Dany_AI_Studio\branding\dany_studio_tab_music.png"
TAB_VIDEO = r"C:\Users\danin\Music\Dany_AI_Studio\branding\dany_studio_tab_video.png"
TAB_COLAB = r"C:\Users\danin\Music\Dany_AI_Studio\branding\dany_studio_tab_colab.png"
TAB_MASTER = r"C:\Users\danin\Music\Dany_AI_Studio\branding\dany_studio_tab_master.png"
TAB_MODELS = r"C:\Users\danin\Music\Dany_AI_Studio\branding\dany_studio_tab_models.png"

# Timeline definition (22 cuts = 58.0 seconds total)
timeline = [
    # 0.0 - 2.5s: Hook Alert (Lamborghini Burnout & Smoke)
    {
        "name": "reel_01_hook_alert",
        "style": "action_bleed",
        "dur": 2.5,
        "source": SHOT_BURNOUT,
        "src_start": 0.5,
        "top": "ALERTA PRODUCTORES IA",
        "center": "TE QUEDAS SIN VRAM"
    },
    # 2.5 - 5.0s: Unlimited AI Production (Tokyo Drift)
    {
        "name": "reel_02_unlimited",
        "style": "action_bleed",
        "dur": 2.5,
        "source": TOKYO,
        "src_start": 5.0,
        "top": "DANY AI STUDIO v2.6",
        "center": "PRODUCCION IA ILIMITADA"
    },
    # 5.0 - 7.5s: VRAM Bottleneck Infographic
    {
        "name": "reel_03_vram_limit",
        "style": "ui_backdrop",
        "dur": 2.5,
        "source": INFO_VRAM,
        "src_start": 0.0,
        "top": "EL GRAN PROBLEMA",
        "center": "16GB VRAM NO ALCANZAN"
    },
    # 7.5 - 10.0s: Music Tab UI
    {
        "name": "reel_04_tab_music",
        "style": "image_backdrop",
        "dur": 2.5,
        "source": TAB_MUSIC,
        "top": "MOTOR DE AUDIO LOCAL",
        "center": "ACE-STEP 1.5 + DUAL LoRA"
    },
    # 10.0 - 12.5s: Speed (Subwoofer membrane vibrating with water)
    {
        "name": "reel_05_speed",
        "style": "action_bleed",
        "dur": 2.5,
        "source": SHOT_SUBWOOFER,
        "src_start": 0.5,
        "top": "VELOCIDAD BRUTAL",
        "center": "CANCION COMPLETA EN 10s"
    },
    # 12.5 - 15.0s: Hybrid Architecture concept (Doodle)
    {
        "name": "reel_06_hybrid_concept",
        "style": "ui_backdrop",
        "dur": 2.5,
        "source": DOODLE,
        "src_start": 30.0,
        "top": "LA SOLUCION DEFINITIVA",
        "center": "ARQUITECTURA HIBRIDA"
    },
    # 15.0 - 17.5s: Infographic Hybrid
    {
        "name": "reel_07_info_hybrid",
        "style": "ui_backdrop",
        "dur": 2.5,
        "source": INFO_HYBRID,
        "src_start": 0.0,
        "top": "LOCAL + CLOUD UNIDOS",
        "center": "RTX 5080 + COLAB A100"
    },
    # 17.5 - 20.0s: Tab Colab UI
    {
        "name": "reel_08_tab_colab",
        "style": "image_backdrop",
        "dur": 2.5,
        "source": TAB_COLAB,
        "top": "BRIDGE AUTOMATICO DRIVE",
        "center": "GOOGLE COLAB A100 GRATIS"
    },
    # 20.0 - 22.5s: Cinematic Video (Tokyo Rain)
    {
        "name": "reel_09_video_gen",
        "style": "action_bleed",
        "dur": 2.5,
        "source": TOKYO,
        "src_start": 18.0,
        "top": "GENERACION DE VIDEO IA",
        "center": "CALIDAD CINEMATOGRAFICA REAL"
    },
    # 22.5 - 25.0s: DLSS 5 Infographic
    {
        "name": "reel_10_dlss5",
        "style": "ui_backdrop",
        "dur": 2.5,
        "source": INFO_DLSS,
        "src_start": 0.0,
        "top": "ESCALADO NEURONAL",
        "center": "LTX-VIDEO 2.5 + DLSS 5"
    },
    # 25.0 - 27.5s: Overdrive YuE2
    {
        "name": "reel_11_yue2",
        "style": "action_bleed",
        "dur": 2.5,
        "source": OVERDRIVE,
        "src_start": 10.0,
        "top": "VOCES HUMANAS REALES",
        "center": "YuE 2 MULTILINGUE + ESPANOL"
    },
    # 27.5 - 30.0s: Video Tab ComfyUI
    {
        "name": "reel_12_tab_video",
        "style": "image_backdrop",
        "dur": 2.5,
        "source": TAB_VIDEO,
        "top": "COMFYUI CONFIGURADO",
        "center": "WORKFLOWS LISTOS EN 1-CLIC"
    },
    # 30.0 - 32.5s: Cockpit night speed
    {
        "name": "reel_13_sound_quality",
        "style": "action_bleed",
        "dur": 2.5,
        "source": SHOT_COCKPIT,
        "src_start": 0.5,
        "top": "FIDELIDAD ACUSTICA",
        "center": "CERO RUIDO - CERO LATENCIA"
    },
    # 32.5 - 35.0s: Mastering Infographic
    {
        "name": "reel_14_mastering",
        "style": "ui_backdrop",
        "dur": 2.5,
        "source": INFO_MASTER,
        "src_start": 0.0,
        "top": "ESTANDAR BROADCAST",
        "center": "MASTERING EBU R128 (-11 LUFS)"
    },
    # 35.0 - 37.5s: Tab Master UI
    {
        "name": "reel_15_tab_master",
        "style": "image_backdrop",
        "dur": 2.5,
        "source": TAB_MASTER,
        "top": "VOLUMEN DE STREAMING",
        "center": "LISTO PARA SPOTIFY Y YOUTUBE"
    },
    # 37.5 - 40.0s: Doodle automation
    {
        "name": "reel_16_doodle_auto",
        "style": "ui_backdrop",
        "dur": 2.5,
        "source": DOODLE,
        "src_start": 155.0,
        "top": "AUTOMATIZACION TOTAL",
        "center": "JOBS DESATENDIDOS EN 1 CLIC"
    },
    # 40.0 - 43.0s: No Subscriptions (Tokyo high speed)
    {
        "name": "reel_17_no_subs",
        "style": "action_bleed",
        "dur": 3.0,
        "source": TOKYO,
        "src_start": 35.0,
        "top": "AHORRA CIENTOS DE DOLARES",
        "center": "ADIOS A LAS SUSCRIPCIONES"
    },
    # 43.0 - 46.0s: Tab Models UI
    {
        "name": "reel_18_tab_models",
        "style": "image_backdrop",
        "dur": 3.0,
        "source": TAB_MODELS,
        "top": "TODO INCLUIDO Y GRATIS",
        "center": "DESCARGA LA SUITE EN DRIVE"
    },
    # 46.0 - 49.0s: Masterclass Hook (Overdrive hypercar)
    {
        "name": "reel_19_masterclass",
        "style": "action_bleed",
        "dur": 3.0,
        "source": OVERDRIVE,
        "src_start": 45.0,
        "top": "TUTORIAL YA EN EL CANAL",
        "center": "CURSO MAESTRO PASO A PASO"
    },
    # 49.0 - 52.0s: Final Doodle
    {
        "name": "reel_20_final_doodle",
        "style": "ui_backdrop",
        "dur": 3.0,
        "source": DOODLE,
        "src_start": 410.0,
        "top": "MIRA EL VIDEO LARGO 16x9",
        "center": "EN EL CANAL DE YOUTUBE"
    },
    # 52.0 - 55.0s: Tokyo finish
    {
        "name": "reel_21_tokyo_finish",
        "style": "action_bleed",
        "dur": 3.0,
        "source": TOKYO,
        "src_start": 55.0,
        "top": "ENLACE EN COMENTARIOS",
        "center": "PACK DE INSTALACION LISTO"
    },
    # 55.0 - 58.0s: Final CTA (Hypercar Rain Headlights)
    {
        "name": "reel_22_final_cta",
        "style": "action_bleed",
        "dur": 3.0,
        "source": SHOT_HYPERCAR,
        "src_start": 0.5,
        "top": "CANAL DANIEL MARIUS",
        "center": "SUSCRIBETE Y COMIENZA HOY"
    }
]

print(f">>> STARTING 9:16 REEL BUILD ({len(timeline)} segments, 58.0s total) <<<", flush=True)
start_time = time.time()

# Fixed bottom CTA overlay for all segments
CTA_LINE_1 = "VIDEO COMPLETO 16x9 EN EL CANAL"
CTA_LINE_2 = "SUSCRIBETE | LINK EN LA DESCRIPCION"

COMMON_CTA_FILTER = (
    f"drawtext=fontfile='C\\:/Windows/Fonts/arialbd.ttf':text='{CTA_LINE_1}':"
    f"fontcolor=yellow:fontsize=36:box=1:boxcolor=black@0.85:boxborderw=14:x=(w-text_w)/2:y=1560,"
    f"drawtext=fontfile='C\\:/Windows/Fonts/arialbd.ttf':text='{CTA_LINE_2}':"
    f"fontcolor=white:fontsize=30:box=1:boxcolor=red@0.85:boxborderw=10:x=(w-text_w)/2:y=1640"
)

rendered_segments = []

for idx, seg in enumerate(timeline, 1):
    out_file = os.path.join(REEL_SEGMENTS_DIR, f"{seg['name']}.mp4")
    rendered_segments.append(out_file)
    dur = seg["dur"]
    style = seg["style"]
    source = seg["source"]
    top_txt = seg["top"]
    center_txt = seg["center"]

    # Text overlays for this specific cut
    cut_text_filter = (
        f"drawtext=fontfile='C\\:/Windows/Fonts/arialbd.ttf':text='{top_txt}':"
        f"fontcolor=white:fontsize=38:box=1:boxcolor=black@0.80:boxborderw=12:x=(w-text_w)/2:y=220,"
        f"drawtext=fontfile='C\\:/Windows/Fonts/arialbd.ttf':text='{center_txt}':"
        f"fontcolor=yellow:fontsize=46:box=1:boxcolor=black@0.88:boxborderw=16:x=(w-text_w)/2:y=1380"
    )

    print(f"[{idx:02d}/{len(timeline)}] Rendering {seg['name']} ({style}, {dur}s)...", flush=True)

    if style == "action_bleed":
        base_vf = "scale=-1:1920:force_original_aspect_ratio=increase,crop=1080:1920,fps=60"
        vf = f"{base_vf},{cut_text_filter},{COMMON_CTA_FILTER}"
        cmd = [
            "ffmpeg", "-y", "-ss", str(seg["src_start"]), "-t", str(dur), "-i", source,
            "-vf", vf, "-c:v", "h264_nvenc", "-preset", "p4", "-b:v", "14M", "-pix_fmt", "yuv420p", "-an", out_file
        ]
    elif style == "ui_backdrop":
        complex_filter = (
            f"[0:v]split[main][bg];"
            f"[bg]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=25:5[bg_b];"
            f"[main]scale=1040:-1[main_sc];"
            f"[bg_b][main_sc]overlay=(W-w)/2:(H-h)/2,fps=60,{cut_text_filter},{COMMON_CTA_FILTER}"
        )
        cmd = [
            "ffmpeg", "-y", "-ss", str(seg["src_start"]), "-t", str(dur), "-i", source,
            "-filter_complex", complex_filter, "-c:v", "h264_nvenc", "-preset", "p4", "-b:v", "14M", "-pix_fmt", "yuv420p", "-an", out_file
        ]
    elif style == "image_backdrop":
        complex_filter = (
            f"[0:v]split[main][bg];"
            f"[bg]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=25:5[bg_b];"
            f"[main]scale=1040:-1[main_sc];"
            f"[bg_b][main_sc]overlay=(W-w)/2:(H-h)/2,fps=60,{cut_text_filter},{COMMON_CTA_FILTER}"
        )
        cmd = [
            "ffmpeg", "-y", "-loop", "1", "-t", str(dur), "-i", source,
            "-filter_complex", complex_filter, "-c:v", "h264_nvenc", "-preset", "p4", "-b:v", "14M", "-pix_fmt", "yuv420p", "-an", out_file
        ]
    else:
        raise ValueError(f"Unknown style {style}")

    res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    if res.returncode != 0:
        print(f"Error rendering {seg['name']}: {res.stderr}", flush=True)
        sys.exit(1)

print("\n>>> ALL 22 VERTICAL 9:16 SEGMENTS RENDERED IN 60FPS! <<<", flush=True)

# Create concat list
concat_list_path = os.path.join(REEL_SEGMENTS_DIR, "reel_concat_list.txt")
with open(concat_list_path, "w", encoding="utf-8") as f:
    for seg_file in rendered_segments:
        safe_path = seg_file.replace("\\", "/")
        f.write(f"file '{safe_path}'\n")

print("Concatenating segments and muxing with 58s master audio...", flush=True)

temp_concat = os.path.join(REEL_SEGMENTS_DIR, "reel_concat_raw.mp4")

mux_cmd = [
    "ffmpeg", "-y",
    "-f", "concat", "-safe", "0", "-i", concat_list_path,
    "-i", AUDIO_FILE,
    "-c:v", "copy",
    "-c:a", "aac", "-b:a", "320k",
    "-t", "58.0",
    "-movflags", "+faststart",
    temp_concat
]

res = subprocess.run(mux_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
if res.returncode != 0:
    print(f"Error muxing reel: {res.stderr}", flush=True)
    sys.exit(1)

# Ensure target directories exist
os.makedirs(os.path.dirname(OUT_SHORTS_CANAL), exist_ok=True)
os.makedirs(os.path.dirname(OUT_SHORTS_VIDEOS), exist_ok=True)

# Copy to final destinations
print(f"Synchronizing to Canal Shorts: {OUT_SHORTS_CANAL}", flush=True)
shutil.copy2(temp_concat, OUT_SHORTS_CANAL)

print(f"Synchronizing to Videos Shorts: {OUT_SHORTS_VIDEOS}", flush=True)
shutil.copy2(temp_concat, OUT_SHORTS_VIDEOS)

print(f"Synchronizing to Desktop: {OUT_DESKTOP}", flush=True)
shutil.copy2(temp_concat, OUT_DESKTOP)

elapsed = time.time() - start_time
print("\n=======================================================", flush=True)
print(f"SUCCESS: REEL HOOK 9:16 (58 SEGUNDOS) COMPLETADO EN {elapsed:.1f}s!", flush=True)
print(f"Destino 1 (Canal): {OUT_SHORTS_CANAL}", flush=True)
print(f"Destino 2 (Videos): {OUT_SHORTS_VIDEOS}", flush=True)
print(f"Destino 3 (Escritorio): {OUT_DESKTOP}", flush=True)
print("=======================================================", flush=True)
