# DANY AI STUDIO v2.7 - LEEME PRIMERO

## Enlaces Oficiales de Descarga
- **Archivo ZIP directo:** https://drive.google.com/file/d/1HYgh8zT_oPAcZ1xTuGFkmjYjswdxm7nc/view?usp=sharing
- **Carpeta completa compartida:** https://drive.google.com/drive/folders/122G3iczOwqzD1Lgx8QX-0m18UQUBp6_3?usp=sharing

## Arranque
1. `install.bat` (una vez): comprueba Python, GPU y FFmpeg y crea `config.json` y `secrets.json`.
2. `start_studio.bat`: arranca el servidor y abre http://localhost:7877

## Archivos
| Archivo | Para qué sirve |
|---|---|
| `studio_core.py` | Lógica compartida (config, seguridad, TOML, masterización, Colab firmado, LLM). |
| `studio_server.py` | Servidor web local + API. |
| `mcp_server.py` | Servidor MCP para agentes (usa `studio_core.py`, mismas reglas que la web). |
| `config.json` | Rutas y ajustes. **Edítalo aquí, no en el código.** |
| `secrets.json` | Secreto HMAC de Colab y API key del LLM. No lo subas a Drive ni lo compartas. |
| `logs/audit.log` | Registro de envíos a Colab, masterizaciones, configs y cambios del LLM. |
| `_backup_2026-09-21/` | Copia de todo lo que había antes de esta versión. |

## Conexión manual de modelo (LLM) para el Copiloto IA
Pestaña **⚙️ Modelos & Guía Rápida** -> "Conexión Manual de Modelo":
1. Elige el tipo: **Ollama** (`http://127.0.0.1:11434`) o **compatible OpenAI** (LM Studio, llama.cpp, vLLM, OpenRouter...).
2. Pulsa **Detectar modelos** (o escribe el nombre a mano), **Probar conexión** y **Guardar** con la casilla activada.
3. "Componer con IA" usará ese modelo. Si está desactivado o falla, usa las plantillas de respaldo y lo indica en pantalla.
- El primer mensaje a un modelo grande puede tardar 1 minuto mientras se carga en memoria (`timeout_s` en `config.json`).
- La API key se guarda en `secrets.json`, o usa la variable de entorno `DANY_LLM_API_KEY`.
- El MCP usa el mismo modelo configurado.

## Colab Bridge seguro (configuración única)
Ahora Colab solo ejecuta trabajos **firmados por tu PC**, con caducidad de 10 min, anti-replay y sin shell.
1. En tu PC: `python studio_core.py --show-colab-secret`
2. En Colab: icono de llave (Secrets) -> nuevo secreto `DANY_COLAB_HMAC` -> pega el valor -> activa "Acceso desde el cuaderno".
3. **Vuelve a subir** `Produccion_Audiovisual_Colab_Bridge.ipynb` a Colab y ejecuta las celdas en orden.
- Formato permitido: `python3 /content/script.py --args` con scripts de la allowlist (`config.json` -> `colab.allowed_scripts`; en el cuaderno, `ALLOWED_SCRIPTS`). Debe coincidir o ser más estricta en el cuaderno.
- Ya no se aceptan comandos bash libres (`bash`, `python3 -c`, `-m`, `curl`...).
- Desde MCP el envío a Colab está **desactivado** (`colab.allow_mcp_dispatch`).
- Rotar el secreto: `python studio_core.py --rotate-colab-secret` y actualizarlo en Colab.

## Seguridad de la web local
Escucha solo en `127.0.0.1`, exige token de sesión, valida Host y Origin (anti-CSRF y anti-DNS-rebinding), no tiene CORS y limita el tamaño y el tipo de las peticiones.
La masterización solo acepta audio dentro de `outputs_dir`, `shorts_dirs` y `extra_audio_roots`.

## Novedades en v2.7
- **Nuevo Motor de Portadas Krea-2 Turbo:** sustitución del motor anterior por el pipeline oficial de Krea-2 Turbo con carga híbrida GGUF/FP8 (`Krea-2-Turbo-Q4_K_M.gguf`), VAE dedicado (`krea2RealVae_v10.safetensors`), refinamiento y expansión de prompts con `Qwen3-VL Thinking` (`TextGenerate`), balanceo de acondicionamiento (`ConditioningKrea2Rebalance`) y reescalado nítido 4x (`ESRGAN_4x.pth`).
- **Descarga Directa desde Colab:** incorporación de Celda 0 interactiva en `Produccion_Audiovisual_Colab_Bridge.ipynb` con `files.download()` para que cualquier usuario o seguidor pueda descargar el ZIP del Studio directamente a su PC con 1 clic sin lidiar con secretos ni configuraciones de puente previas.
- **Doble resolución 16:9 y 9:16:** presets oficiales calibrados en `1024x1024` (portada), `1376x768` (YouTube) y `768x1376` (Shorts/TikTok).

## Cambios importantes respecto a versiones anteriores
- **LoRA:** el TOML escribía `lora_name`, una clave que el CLI de ACE-Step ignora, así que **la LoRA nunca se cargaba**. Ahora escribe `lora_path` (carpeta `lora_models/<nombre>/final`) y `lm_model_path`, y añade `bpm` y `keyscale`.
- `roti-r0kkk` no está en `lora_models`: mapea su ruta en `config.json` -> `music.lora_paths` (ej. `"roti-r0kkk": "rock_acestep1.5_v1"`) o se generará sin LoRA (la web avisa).
- TOML con escapado correcto (comillas, `"""`, barras) y títulos saneados (sin `../`).
- Masterización: 2 pasadas en web y MCP, `-ar 44100` (loudnorm sube a 192 kHz) y escritura atómica. Objetivo en `config.json` -> `mastering` (por defecto -11 LUFS; YouTube y Spotify normalizan hacia -14).
- Letra limpia automática: se quitan líneas con BPM o acotaciones de instrumentos entre paréntesis.
- Se arregló el bug que borraba el terminal de la pestaña Música tras usar el Copiloto IA.
- Los presets viven solo en el servidor (antes estaban duplicados y con una acotación en la letra).
- `.bat` en ASCII + CRLF; los scripts de ComfyUI usan `config.json` en vez de rutas fijas.
- **Motor y LoRAs:** tus 4 LoRAs se entrenaron para `acestep-v15-turbo` (dimension 2048); `acestep-v15-xl-turbo` usa 2560 y no puede cargarlas. El Studio ahora usa Turbo por defecto y, si pides XL con LoRA, cambia a Turbo y lo avisa.
- Formato de salida de ACE-Step: `flac` (antes `mp3` a 128k, que se recomprimia al masterizar). El master es el unico paso que crea el MP3 final.
- **Bug critico corregido:** el CLI de ACE-Step activa por defecto el planificador LM ('thinking') en tu GPU, que llama a input() para editar un borrador a mano. Sin terminal interactiva se queda colgado para siempre (sin uso de CPU/disco/GPU, indistinguible de 'trabajando'). El Studio ahora escribe 'thinking = false' en cada TOML.
