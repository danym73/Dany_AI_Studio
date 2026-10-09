import os
import sys
import subprocess
import time

# Configurations
TOTAL_DURATION = 427.38
SEGMENTS_DIR = r"C:\Users\danin\Music\Dany_AI_Studio\logs\segments"
os.makedirs(SEGMENTS_DIR, exist_ok=True)

MASTER_AUDIO = r"C:\Users\danin\Music\Dany_AI_Studio\logs\dany_studio_audio_master_ebu128.wav"
OUTPUT_VIDEO_MUSIC = r"C:\Users\danin\Music\Videos\Dany_AI_Studio_Tutorial_Master_1080p.mp4"
OUTPUT_VIDEO_DESKTOP = r"C:\Users\danin\Desktop\Dany_AI_Studio_Tutorial_Master_1080p.mp4"

# Timeline specifications
timeline = [
    # 00:00 - 00:14 (14s): Opening Hook (Cinematic Rock Guitar & Stage)
    {
        "name": "seg_01_hook_rock",
        "type": "video_hook",
        "dur": 14.0,
        "source": r"C:\Users\danin\Music\Videos\Rust_And_Redemption_Bennett_Guitar_Director_16x9.mp4",
        "src_start": 3.0
    },
    # 00:14 - 01:14 (60s): Base NotebookLM Doodle (Concept & Bridge)
    {
        "name": "seg_02_base_intro",
        "type": "base",
        "dur": 60.0,
        "source": r"C:\Users\danin\Downloads\Dany_AI_Studio.mp4",
        "src_start": 14.0
    },
    # 01:14 - 01:22 (8s): Infographic 1 - VRAM Bottleneck
    {
        "name": "seg_03_vram_infographic",
        "type": "video",
        "dur": 8.0,
        "source": r"C:\Users\danin\Music\Dany_AI_Studio\branding\infografias\01_vram_bottleneck_infographic.mp4",
        "src_start": 0.0
    },
    # 01:22 - 02:18 (56s): Base NotebookLM Doodle (VRAM vs RAM table)
    {
        "name": "seg_04_base_vram_table",
        "type": "base",
        "dur": 56.0,
        "source": r"C:\Users\danin\Downloads\Dany_AI_Studio.mp4",
        "src_start": 82.0
    },
    # 02:18 - 02:26 (8s): Infographic 2 - Hybrid Architecture
    {
        "name": "seg_05_hybrid_infographic",
        "type": "video",
        "dur": 8.0,
        "source": r"C:\Users\danin\Music\Dany_AI_Studio\branding\infografias\02_hybrid_architecture_infographic.mp4",
        "src_start": 0.0
    },
    # 02:26 - 03:40 (74s): Base NotebookLM Doodle (Cloud & Drive sync)
    {
        "name": "seg_06_base_cloud",
        "type": "base",
        "dur": 74.0,
        "source": r"C:\Users\danin\Downloads\Dany_AI_Studio.mp4",
        "src_start": 146.0
    },
    # 03:40 - 03:52 (12s): UI Tour - Tab Music (ACE-Step 1.5)
    {
        "name": "seg_07_ui_music",
        "type": "image",
        "dur": 12.0,
        "source": r"C:\Users\danin\Music\Dany_AI_Studio\branding\dany_studio_tab_music.png"
    },
    # 03:52 - 04:04 (12s): UI Tour - Tab Video (ComfyUI / LTX)
    {
        "name": "seg_08_ui_video",
        "type": "image",
        "dur": 12.0,
        "source": r"C:\Users\danin\Music\Dany_AI_Studio\branding\dany_studio_tab_video.png"
    },
    # 04:04 - 04:16 (12s): UI Tour - Tab Colab (Bridge)
    {
        "name": "seg_09_ui_colab",
        "type": "image",
        "dur": 12.0,
        "source": r"C:\Users\danin\Music\Dany_AI_Studio\branding\dany_studio_tab_colab.png"
    },
    # 04:16 - 04:30 (14s): Base NotebookLM Doodle
    {
        "name": "seg_10_base_before_master",
        "type": "base",
        "dur": 14.0,
        "source": r"C:\Users\danin\Downloads\Dany_AI_Studio.mp4",
        "src_start": 256.0
    },
    # 04:30 - 04:38 (8s): Infographic 3 - EBU R128 Mastering
    {
        "name": "seg_11_master_infographic",
        "type": "video",
        "dur": 8.0,
        "source": r"C:\Users\danin\Music\Dany_AI_Studio\branding\infografias\03_ebu_r128_mastering_infographic.mp4",
        "src_start": 0.0
    },
    # 04:38 - 05:12 (34s): Base NotebookLM Doodle (Audio LUFS)
    {
        "name": "seg_12_base_audio_lufs",
        "type": "base",
        "dur": 34.0,
        "source": r"C:\Users\danin\Downloads\Dany_AI_Studio.mp4",
        "src_start": 278.0
    },
    # 05:12 - 05:20 (8s): Infographic 4 - DLSS 5 Video Pipeline
    {
        "name": "seg_13_dlss5_infographic",
        "type": "video",
        "dur": 8.0,
        "source": r"C:\Users\danin\Music\Dany_AI_Studio\branding\infografias\04_dlss5_video_pipeline_infographic.mp4",
        "src_start": 0.0
    },
    # 05:20 - 05:32 (12s): Cinematic Video Proof - Tokyo Rain Night Drive
    {
        "name": "seg_14_cinematic_tokyo",
        "type": "video",
        "dur": 12.0,
        "source": r"C:\Users\danin\Music\Videos\Tokyo_Rain_Kenzy_Night_Drive_Official_16x9.mp4",
        "src_start": 5.0
    },
    # 05:32 - 06:28 (56s): Base NotebookLM Doodle (Workflow Steps)
    {
        "name": "seg_15_base_workflow_steps",
        "type": "base",
        "dur": 56.0,
        "source": r"C:\Users\danin\Downloads\Dany_AI_Studio.mp4",
        "src_start": 332.0
    },
    # 06:28 - 06:45 (17s): UI Models & Quick Guide
    {
        "name": "seg_16_ui_models",
        "type": "image",
        "dur": 17.0,
        "source": r"C:\Users\danin\Music\Dany_AI_Studio\branding\dany_studio_tab_models.png"
    },
    # 06:45 - 427.38 (22.38s): Base Final Doodle + Call to Action
    {
        "name": "seg_17_base_cta_final",
        "type": "base_cta",
        "dur": 22.38,
        "source": r"C:\Users\danin\Downloads\Dany_AI_Studio.mp4",
        "src_start": 405.0
    }
]

print(">>> STARTING OPTION B MASTER VIDEO GENERATION <<<")
start_time = time.time()

rendered_segments = []

for idx, seg in enumerate(timeline, 1):
    out_file = os.path.join(SEGMENTS_DIR, f"{seg['name']}.mp4")
    rendered_segments.append(out_file)
    dur = seg["dur"]
    stype = seg["type"]
    source = seg["source"]

    print(f"[{idx}/{len(timeline)}] Rendering {seg['name']} ({stype}, {dur}s)...")

    if stype == "video_hook":
        # Video with lower third banner
        vf = (
            "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,fps=60,"
            "drawtext=fontfile='C\\:/Windows/Fonts/arial.ttf':text='DANY AI STUDIO | SUITE HIBRIDA IA':fontcolor=white:fontsize=44:box=1:boxcolor=black@0.75:boxborderw=15:x=(w-text_w)/2:y=h-150,"
            "drawtext=fontfile='C\\:/Windows/Fonts/arial.ttf':text='ACE-Step 1.5 + ComfyUI + Google Colab (RTX 5080)':fontcolor=yellow:fontsize=30:box=1:boxcolor=black@0.75:boxborderw=10:x=(w-text_w)/2:y=h-85"
        )
        cmd = [
            "ffmpeg", "-y", "-ss", str(seg["src_start"]), "-t", str(dur), "-i", source,
            "-vf", vf, "-c:v", "h264_nvenc", "-preset", "p4", "-b:v", "14M", "-pix_fmt", "yuv420p", "-an", out_file
        ]
    elif stype == "base_cta":
        # Final base video with Call to Action banner
        vf = (
            "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,fps=60,"
            "drawtext=fontfile='C\\:/Windows/Fonts/arial.ttf':text='PAQUETE GRATIS EN GOOGLE DRIVE  |  SUSCRIBETE AL CANAL':fontcolor=white:fontsize=38:box=1:boxcolor=red@0.85:boxborderw=14:x=(w-text_w)/2:y=h-110"
        )
        cmd = [
            "ffmpeg", "-y", "-ss", str(seg["src_start"]), "-t", str(dur), "-i", source,
            "-vf", vf, "-c:v", "h264_nvenc", "-preset", "p4", "-b:v", "14M", "-pix_fmt", "yuv420p", "-an", out_file
        ]
    elif stype in ["base", "video"]:
        # Standard video / infographic scaled to 1080p 60fps
        vf = "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,fps=60"
        cmd = [
            "ffmpeg", "-y", "-ss", str(seg["src_start"]), "-t", str(dur), "-i", source,
            "-vf", vf, "-c:v", "h264_nvenc", "-preset", "p4", "-b:v", "14M", "-pix_fmt", "yuv420p", "-an", out_file
        ]
    elif stype == "image":
        # Static image looped for dur seconds
        vf = "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,fps=60"
        cmd = [
            "ffmpeg", "-y", "-loop", "1", "-t", str(dur), "-i", source,
            "-vf", vf, "-c:v", "h264_nvenc", "-preset", "p4", "-b:v", "14M", "-pix_fmt", "yuv420p", "-an", out_file
        ]

    res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    if res.returncode != 0:
        print(f"Error rendering {seg['name']}: {res.stderr}")
        sys.exit(1)

print("\n>>> ALL 17 SEGMENTS RENDERED IN 1080p 60fps! <<<")

# Create concat list
concat_list_path = os.path.join(SEGMENTS_DIR, "concat_list.txt")
with open(concat_list_path, "w", encoding="utf-8") as f:
    for seg_file in rendered_segments:
        # Forward slashes for ffmpeg concat
        safe_path = seg_file.replace("\\", "/")
        f.write(f"file '{safe_path}'\n")

print("Concat list written. Concatenating and muxing with EBU R128 master audio...")

# Concatenate and mux
final_cmd = [
    "ffmpeg", "-y",
    "-f", "concat", "-safe", "0", "-i", concat_list_path,
    "-i", MASTER_AUDIO,
    "-c:v", "copy",
    "-c:a", "aac", "-b:a", "320k",
    "-movflags", "+faststart",
    OUTPUT_VIDEO_MUSIC
]

res = subprocess.run(final_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
if res.returncode != 0:
    print(f"Error muxing final video: {res.stderr}")
    sys.exit(1)

# Copy to Desktop
import shutil
shutil.copy2(OUTPUT_VIDEO_MUSIC, OUTPUT_VIDEO_DESKTOP)

elapsed = time.time() - start_time
print(f"\n=======================================================")
print(f"🎉 MASTER TUTORIAL VIDEO (OPCIÓN B) COMPLETADO EN {elapsed:.1f}s!")
print(f"📁 Destino 1: {OUTPUT_VIDEO_MUSIC}")
print(f"📁 Destino 2 (Escritorio): {OUTPUT_VIDEO_DESKTOP}")
print(f"=======================================================")
