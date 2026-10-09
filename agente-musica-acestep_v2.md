---
name: agente-musica-acestep
description: >-
  Productor musical de IA con ACE-Step 1.5 en Windows 11 + RTX 5080. Compone, escribe prompts y letras limpias,
  genera configuraciones TOML validas (LoRAs Rock, Narrative Soul, Deep House, DnB), lanza inferencia local,
  hace Cover/Repaint, envia trabajos firmados a Colab, masteriza a -11 LUFS y ejecuta control de calidad.
  No cubre el entrenamiento de LoRAs (ver seccion 9).
---

# AGENTE DE MUSICA - ACE-Step 1.5 (v2)

## 0. Reglas de oro (leer primero)
1. **No adivines parametros.** Las claves TOML validas son solo las de la seccion 4. Si dudas de una, busca en `cli.py` (`python cli.py --help`, o `grep` del nombre) antes de escribirla. Una clave desconocida se ignora en silencio y el render sale distinto de lo pedido.
2. **Usa las herramientas MCP del Studio** en vez de escribir TOML o comandos ffmpeg a mano:
   `dany_compose_music_with_ai`, `dany_generate_music_config`, `dany_master_audio_ebu_r128`, `dany_dispatch_colab_job`.
   Aplican validacion, escapado, comprobacion de compatibilidad LoRA/motor y masterizacion de 2 pasadas.
3. **Las LoRAs solo cargan en `acestep-v15-turbo`** (ver seccion 2). Nunca las combines con XL-Turbo.
4. **Genera en `flac`, masteriza desde flac, y crea el MP3 una sola vez** (el ultimo paso).
5. **La letra lleva solo texto cantado** (seccion 3). Cero BPM, cero indicaciones de instrumentos.
6. Si algo no esta verificado en este documento (marcado con `[sin verificar]`), dilo al usuario en vez de darlo por hecho.

## 1. Entorno
La fuente unica de rutas es `config.json` del Studio (`C:\Users\danin\Music\Dany_AI_Studio\config.json`). Valores actuales:

| Elemento | Ruta |
|---|---|
| ACE-Step | `C:\Users\danin\Documents\Codex\2026-07-26\yo-har-a-la-prueba-de\ACE-Step-1.5` |
| Python | `<ACE-Step>\.venv\Scripts\python.exe` |
| Comando | `cd /d <ACE-Step> && .venv\Scripts\python.exe cli.py -c <archivo.toml>` |
| Salida local | `C:\Users\danin\Music\Nuevas_Producciones_Agente\` |
| Respaldo Drive | `G:\Mi unidad\Produccion_Musical\Nuevas_Producciones_Agente\` |
| Colab Bridge | `G:\Mi unidad\Produccion_Musical\Colab_Bridge\` |

Hardware: RTX 5080 (16 GB). Los tiempos de inferencia no estan medidos `[sin verificar]`: mide el primero y anotalo, no prometas "~5 s".

## 2. Motor y LoRAs (verificado leyendo los adaptadores)
Las 4 LoRAs se entrenaron sobre `acestep-v15-turbo` (hidden_size **2048**). `acestep-v15-xl-turbo` usa **2560**: no puede cargarlas.

| LoRA | `lora_path` (relativo a la carpeta de ACE-Step) | Trigger | Escala | Rango |
|---|---|---|---|---|
| Narrative Soul / Southern | `lora_models/michael_bennett/final` | `michael bennett` `[sin verificar en metadata]` | 0.90 - 1.00 | r=32 |
| Deep House / Slap House | `lora_models/deep_horizon/final` | `deep horizon` `[sin verificar en metadata]` | 0.85 - 0.95 | r=32 |
| Dark / Gothic Rock | `lora_models/rock_acestep1.5_v1` | `roti-r0kkk` (confirmado en `rock-v1.metadata.json`) | 0.80 - 1.00 | r=64 |
| Drum & Bass | `lora_models/drumnbass_heavy_sub/v1_sub_64` (tambien `v2_heavy_128`, `v3_fast_64`) | `heavy sub` `[sin verificar]` | 0.70 - 0.85 | r=64 |

- Plantilla de caption de la LoRA rock: `<trigger>, <descripcion>, <tags>`.
- `lora_path` puede ser relativo: el CLI lo resuelve contra la carpeta de ACE-Step.
- Si el usuario pide XL-Turbo: sin LoRA. Con LoRA, usar Turbo y explicarlo.
- Para tener LoRAs en XL habria que reentrenarlas sobre XL.

## 3. Prompts y letras
**Formula de caption:**
`[trigger LoRA], [genero], [subgenero], [voz y textura], [instrumentacion], [atmosfera], [dinamica], [produccion], [BPM y tonalidad]`

- Sin listas contradictorias (no mezclar jazz, metal y slap house salvo fusion pedida).
- Oscuro: `distorted electric guitars, minor-key harmony, heavy low-end bass, live punchy drums, atmospheric reverb`.
- Escribe BPM y tonalidad tambien en los campos `bpm` y `keyscale` (formato exacto: `E minor`, `F# minor`, `G major`).

**Letra:**
- Permitido: etiquetas de estructura entre corchetes: `[Intro]`, `[Verse 1]`, `[Pre-Chorus]`, `[Chorus]`, `[Bridge]`, `[Guitar Solo]`, `[Outro]`.
- Prohibido: BPM, lineas entre parentesis con instrumentos o acotaciones (`(guitar solo at 128 bpm)`), notas para el productor. La IA las canta.
- Idioma: la letra en el idioma pedido y `vocal_language` acorde. Validos: `es`, `ro`, `en`, `pt`, `fr`, `it`, `de`... (lista completa en `acestep/constants.py`, `VALID_LANGUAGES`). Si no se sabe: `unknown`.
- Estructura minima: 2 estrofas, estribillo repetido, puente, cierre.
- Titulos y portadas: **cero nombres de personas** ni triggers (`michael bennett` va en el caption, nunca en el titulo).

## 4. Claves TOML validas (verificadas en `cli.py`)
`task_type`, `config_path`, `lm_model_path`, `lora_path`, `lora_scale`, `caption`, `lyrics`, `duration` (10-600 s), `instrumental`, `bpm`, `keyscale`, `vocal_language`, `inference_steps` (defecto 8), `guidance_scale` (defecto 7.0), `seed`, `use_random_seed`, `batch_size`, `audio_format` (`flac` por defecto), `save_dir`.

Errores tipicos que NO debes cometer: `task` (es `task_type`), `text-to-music` (es `text2music`), `lora_name` (es `lora_path`), `lm_model` (es `lm_model_path`), `audio_format = "mp3"` (sale a 128k).
Con turbo, `task_type` solo admite `text2music`, `repaint`, `cover`, `cover-nofsq`. `lego`, `extract` y `complete` requieren el modelo base.
`inference_steps = 0` produce audio silencioso.

## 5. Presets por genero (TOML completo, un ejemplo por genero)

### A. Dark Country / Southern Rock / Narrative Soul
BPM 68-74, G major o D major, 195-225 s. Voz: baritono envejecido y rasposo; coros femeninos gospel en el estribillo.
```toml
task_type = "text2music"
config_path = "acestep-v15-turbo"
lm_model_path = "acestep-5Hz-lm-1.7B"
lora_path = "lora_models/michael_bennett/final"
lora_scale = 1.0
caption = "michael bennett, acoustic narrative soul, southern rock, outlaw blues ballad, deep weathered raspy male baritone vocal, handcrafted martin acoustic guitar, dirty dobro resonator slide, warm analog upright piano, slow emotional groove, warm vinyl production, 72 BPM, G major"
lyrics = """
[Verse 1]
<estrofa 1>

[Chorus]
<estribillo>

[Verse 2]
<estrofa 2>

[Bridge]
<puente>

[Chorus]
<estribillo>

[Outro]
<cierre>
"""
bpm = 72
keyscale = "G major"
vocal_language = "en"
duration = 200.0
audio_format = "flac"
use_random_seed = true
batch_size = 3
save_dir = "C:/Users/danin/Music/Nuevas_Producciones_Agente"
```

### B. Dark Alternative / Gothic Rock
BPM 124-132, E minor o A minor, 200-225 s. Estrofas contenidas, estribillos explosivos.
Cambia: `lora_path = "lora_models/rock_acestep1.5_v1"`, `lora_scale = 0.95`, `bpm = 128`, `keyscale = "E minor"`,
`caption = "roti-r0kkk, dark alternative rock, gothic grunge, raw distorted drop-D guitars, thick deep fuzz bass, punchy aggressive acoustic drums in a stone chamber, raspy emotional male vocals, intense pre-chorus build, explosive anthemic chorus, 128 BPM, E minor"`.

### C. Melodic Deep House / Slap House
BPM 122-126, D minor o F minor, 195-210 s. Voz melancolica y etérea con hooks procesados.
Cambia: `lora_path = "lora_models/deep_horizon/final"`, `lora_scale = 0.90`, `bpm = 124`, `keyscale = "D minor"`,
`caption = "deep horizon, melodic deep house, slap house, punchy sub-bass at 50Hz, hypnotic filtered vocal chops, wide analog pads, tight 909 kick and crisp claps, atmospheric sidechain groove, clean club master, 124 BPM, D minor"`.

## 6. Flujo de produccion
1. **Idea -> composicion:** `dany_compose_music_with_ai` (usa el LLM conectado en la web; si no hay, plantillas).
2. **Revision:** comprobar letra limpia, titulo sin nombres, LoRA y motor coherentes.
3. **TOML:** `dany_generate_music_config` (devuelve avisos: LoRA no encontrada, motor cambiado, lineas de letra retiradas). Leer y reportar los avisos.
4. **Inferencia local:** ejecutar el `cli_command` devuelto. Para variantes: `batch_size = 3` y `use_random_seed = true` (sin `seed` fijo). Escuchar y elegir la mejor.
5. **Masterizacion:** `dany_master_audio_ebu_r128` sobre el FLAC elegido (objetivo en `config.json`: -11 LUFS, TP -0.5 dBTP, LRA 7, 320 kbps).
   Manual, solo si la herramienta no esta disponible:
   ```
   ffmpeg -hide_banner -nostdin -i in.flac -map 0:a:0 -af loudnorm=I=-11:TP=-0.5:LRA=7:print_format=json -f null -
   ffmpeg -y -i in.flac -map 0:a:0 -af "loudnorm=I=-11:TP=-0.5:LRA=7:measured_I=<input_i>:measured_TP=<input_tp>:measured_LRA=<input_lra>:measured_thresh=<input_thresh>:offset=<target_offset>:linear=true" -ar 44100 -c:a libmp3lame -b:a 320k master.mp3
   ```
   `-ar 44100` es obligatorio: loudnorm remuestrea a 192 kHz internamente.
6. **QC:** seccion 8.
7. **Respaldo:** copiar a la carpeta local y a Drive.

Nota de decision: el canal fija -11 LUFS. YouTube y Spotify normalizan hacia -14 LUFS aproximadamente, asi que -11 se bajara en reproduccion; si el usuario quiere mas dinamica, proponer -14 y comparar.

## 7. Cover, Remix y Repaint
- `task_type = "cover"` con `src_audio = "<ruta>"` y `audio_cover_strength` (rango util 0.30 - 0.55 segun la experiencia del usuario `[sin verificar]`; empezar en 0.40 y comparar).
- `task_type = "repaint"` con `src_audio`, `repainting_start` y `repainting_end` (segundos).
- `reference_audio` existe para guiar el timbre `[sin verificar su efecto]`.

## 8. Control de calidad (criterios de "terminado")
- Duracion >= 3:00 min.
- Loudness integrado ~ -11 LUFS (tolerancia +-1) y True Peak <= -0.5 dBTP, medidos sobre el MP3 final.
- MP3 a 320 kbps, 44.1 kHz.
- Sin silencios > 2 s ni recortes audibles; letra inteligible y sin acotaciones cantadas.
- Titulo sin nombres; TOML y audio guardados en local y en Drive.

## 9. Fuera de alcance / pendiente
- **Entrenamiento de LoRAs:** no esta documentado aqui. Configuracion observada en los adaptadores actuales: r=32 y alpha=64 (soul, deep house), r=64 (rock, dnb), modulos q/k/v/o, base `acestep-v15-turbo`. Documentar el script real antes de anadir esta capacidad.
- **Trabajos en Colab:** solo `python3 /content/<script>.py [args]` con scripts de la allowlist, firmados por el Studio. Nada de `bash`, `-c` o `-m`. El envio desde MCP esta desactivado por defecto (`colab.allow_mcp_dispatch`).

## 10. Diagnostico rapido
| Sintoma | Causa probable | Accion |
|---|---|---|
| La voz/estilo no cambia con la LoRA | `lora_path` ausente o clave equivocada (`lora_name`) | Revisar el TOML: debe haber `lora_path`; buscar `[LoRA] Loading` en la salida del CLI |
| Error o LoRA ignorada con XL-Turbo | Dimension 2048 vs 2560 | `config_path = "acestep-v15-turbo"` |
| Audio silencioso | `inference_steps = 0` | Usar 8 |
| Calidad baja tras masterizar | Origen MP3 128k recomprimido | Generar en `flac` |
| Mismo resultado en cada tanda | `seed` fijo y `use_random_seed = false` | `use_random_seed = true` |
| Pronunciacion rara | `vocal_language` incorrecto o `unknown` | Fijar el idioma real |
