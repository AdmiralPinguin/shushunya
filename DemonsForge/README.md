# DemonsForge

Local image generation setup.

The apps are configured to run on CPU/RAM by default so GPU memory can stay available for the main LLM.

## Models

- SD 3.5 Large: `stabilityai/stable-diffusion-3.5-large`
- SDXL Base: `stabilityai/stable-diffusion-xl-base-1.0`
- FLUX Schnell: `black-forest-labs/FLUX.1-schnell`

## Gated model access

SD 3.5 Large and FLUX Schnell are gated on Hugging Face. Before downloading them, log in and accept access for:

https://huggingface.co/stabilityai/stable-diffusion-3.5-large

https://huggingface.co/black-forest-labs/FLUX.1-schnell

Then run:

```bash
DemonsForge/bin/huggingface-cli login
./download-model.sh
./download-flux.sh
```

## Start

```bash
./start.sh       # SD 3.5 Large, port 7860
./start-sdxl.sh  # SDXL, port 7861
./start-flux.sh  # FLUX, port 7862
```

Open one of:

http://localhost:7860
http://localhost:7861
http://localhost:7862
