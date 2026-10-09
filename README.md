# 🚀 DANY AI STUDIO (v2.7)

> **Suite Híbrida Unificada de Producción Musical, Arte Visual y Vídeo Cinemático con Inteligencia Artificial**  
> *Dany AI Studio es una suite audiovisual híbrida para Windows y Google Colab. Incluye la interfaz web completa para componer canciones con ACE-Step 1.5 y YuE 2, generar portadas en alta resolución con Krea-2 Turbo (ComfyUI + Qwen3-VL), masterizar audio a estándar EBU R128 (-11 LUFS), crear vídeos cinemáticos y conectar con GPU en la nube sin saturar la VRAM.*

[![Version](https://img.shields.io/badge/version-2.7-cyan.svg)](https://github.com/danym73/Dany_AI_Studio)
[![YouTube Tutorial](https://img.shields.io/badge/YouTube-Tutorial%20Oficial-red.svg?logo=youtube)](https://www.youtube.com/watch?v=6xXdZtV8vSc)
[![Engine](https://img.shields.io/badge/audio-ACE--Step%201.5%20%7C%20YuE%202-purple.svg)](https://huggingface.co/ACE-Step/Ace-Step1.5)
[![Visual](https://img.shields.io/badge/covers-Krea--2%20Turbo-blue.svg)](#)
[![Video](https://img.shields.io/badge/video-LTX--2.5%20%7C%20MiniMax%20H3-orange.svg)](#)
[![Mastering](https://img.shields.io/badge/mastering-EBU%20R128%20(-11%20LUFS)-green.svg)](#)
[![License](https://img.shields.io/badge/license-MIT-lightgrey.svg)](#)

---

## 🌟 ¿Qué es Dany AI Studio?

**Dany AI Studio** es una estación de trabajo completa (Hub Web local + API + Servidor MCP para agentes) diseñada para crear canciones completas de más de 3 minutos, portadas de alta resolución y vídeos cinematográficos sincronizados al tempo (*Beat Sync*) en cuestión de segundos.

Su arquitectura híbrida resuelve el problema que sufren la mayoría de creadores: **el cuello de botella de la memoria VRAM**. Ejecuta el orquestador y tareas ágiles en tu ordenador local y delega el renderizado pesado a GPUs de Google Colab de forma 100% desatendida y segura.

---

## ⚡ Características Principales

### 🎵 1. Motor de Composición Musical (ACE-Step 1.5 & YuE 2)
- **Generación ultrarrápida:** canciones completas con voz e instrumentación en ~35–45 segundos.
- **Doble motor calibrado:** 
  - `acestep-v15-xl-turbo` + `acestep-5Hz-lm-1.7B` para voces nítidas sin saturación de memoria.
  - `YuE 2` para narrativas largas, rock étnico y fonética multilingüe.
- **Gestión inteligente de LoRAs:** integración calibrada de estilos (Rock, Gothic Metal, Southern, Deep House) y voces personalizadas.
- **Letras humanas y limpias:** depuración automática de metadatos técnicos en el texto.

### 🖼️ 2. Generador de Portadas IA (Krea-2 Turbo)
- **Pipeline oficial Krea-2 Turbo:** inferencia híbrida optimizada con GGUF/FP8 (`Krea-2-Turbo-Q4_K_M.gguf` / `krea2_raw_bf16.safetensors`) y VAE dedicado.
- **Expansor de prompts con IA:** nodo `TextGenerate` con **Qwen3-VL Thinking** para transformar una idea básica en un prompt cinematográfico de alta fidelidad.
- **Rebalanceo de capas:** balanceo de condicionamiento adaptativo (`ConditioningKrea2Rebalance`).
- **Upscaling nítido 4x:** post-procesado con `ESRGAN_4x.pth` para portadas maestras en ultra alta definición.
- **Formatos nativos:** 1:1 Cuadrado (1024×1024), 16:9 Horizontal (1376×768) y 9:16 Vertical (768×1376).

### 🎬 3. Vídeo Cinemático & Animación
- Integración directa con **ComfyUI Local & Nube**.
- Soporte para **LTX-2.5 22B**, **MiniMax H3** (planos secuencia continuos y gramática de tiros de cámara) y escalado neuronal con **DLSS 5 Enhancer** / RTX VSR.
- Generación de clips sincronizados a tempo cada 1.5–2.5s.

### 🎛️ 4. Masterización Automática EBU R128 (-11 LUFS)
- Procesado profesional de 2 pasadas en FFmpeg: volumen integrado a **-11.0 LUFS**, True Peak **<= -0.5 dBTP**, LRA 7.0 a 320 kbps.
- Exportación automática sincronizada para **YouTube 16:9** y **Shorts / TikTok 9:16** (30s con CTA).

### ☁️ 5. Colab Cloud Bridge Seguro
- Conexión cifrada mediante firma **HMAC-SHA256**, caducidad de trabajos y protección anti-replay.
- Ejecución desatendida sin necesidad de comandos bash inseguros.
- Cuaderno oficial interactivo con **descarga automática con 1 clic**.

---

## 📥 Descarga e Instalación Rápida

### Opción A: Descarga Directa (Recomendada)
1. Descarga el paquete oficial desde Google Drive:  
   👉 **[Descargar Dany_AI_Studio_v2.7_Gratis.zip](https://drive.google.com/file/d/1HYgh8zT_oPAcZ1xTuGFkmjYjswdxm7nc/view?usp=sharing)**  
   *(O explora la [Carpeta Compartida de Google Drive](https://drive.google.com/drive/folders/122G3iczOwqzD1Lgx8QX-0m18UQUBp6_3?usp=sharing))*
2. Descomprime el archivo en una carpeta de tu PC (ej: `C:\Dany_AI_Studio`).
3. Haz doble clic en **`install.bat`** (comprueba Python, GPU y dependencias automáticamente).
4. Haz doble clic en **`start_studio.bat`**. ¡Se abrirá la aplicación en tu navegador en `http://localhost:7877`!

### Opción B: Clonar vía Git
```bash
git clone https://github.com/danym73/Dany_AI_Studio.git
cd Dany_AI_Studio
install.bat
start_studio.bat
```

---

## 🛠️ Requisitos del Sistema

- **Sistema Operativo:** Windows 10 / 11 (64-bit)
- **Procesador:** Intel Core i5 / AMD Ryzen 5 o superior
- **Memoria RAM:** 16 GB mínimo (32 GB recomendado)
- **Tarjeta Gráfica:** NVIDIA RTX (serie 3000, 4000 o 5000 con 8 GB+ VRAM para local). Si tienes menos VRAM o gráfica integrada, puedes delegar los renders a Google Colab gratis.
- **Python:** 3.10, 3.11 o 3.12
- **FFmpeg:** Instalado en el sistema (el script `install.bat` te avisará si falta).

---

## 📖 Estructura del Proyecto

```text
Dany_AI_Studio/
├── studio_core.py             # Motor central y lógica unificada (config, TOML, mastering, Krea-2, HMAC)
├── studio_server.py           # Servidor web local y endpoints REST
├── mcp_server.py              # Conector MCP para agentes de IA
├── config.json                # Configuración de rutas y motores
├── install.bat                # Instalador desatendido para Windows
├── start_studio.bat           # Lanzador directo del Studio
├── Produccion_Audiovisual_... # Cuaderno de Google Colab con descarga directa
├── workflows/                 # Plantillas JSON oficiales para ComfyUI (Krea-2 Turbo)
├── static/                    # Interfaz web moderna (HTML5, Vanilla CSS, JS)
│   ├── index.html
│   ├── style.css
│   └── app.js
└── LEEME_PRIMERO.md           # Guía rápida y notas de versión
```

---

## 📺 Tutorial en YouTube & Demostración

Aprende a instalar y exprimir todas las funciones de la suite paso a paso con los vídeos oficiales:

- 🎬 **Tutorial Maestro 16:9:** [Crea Música y Vídeo con IA GRATIS (Dany AI Studio)](https://www.youtube.com/watch?v=6xXdZtV8vSc)
- 📱 **Short / Reel 9:16:** [Demostración Rápida en YouTube Shorts](https://www.youtube.com/shorts/Wqs7DDYHqG4)
- 🔔 **Canal Oficial:** [Suscríbete a Daniel Marius en YouTube](https://www.youtube.com/@danielmarius-c4v?sub_confirmation=1)

---

## 🤝 Comunidad y Soporte

- 📺 **Vídeos y Nuevas Funciones:** [Canal de Daniel Marius en YouTube](https://www.youtube.com/@danielmarius-c4v)
- 💬 **Sugerencias y Problemas:** Abre un [Issue en GitHub](https://github.com/danym73/Dany_AI_Studio/issues)

---

## 📄 Licencia

Este proyecto se distribuye bajo la licencia **MIT**. Eres libre de usarlo, modificarlo y compartirlo para producciones personales y comerciales.
