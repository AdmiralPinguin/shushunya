#!/usr/bin/env python3
import os
from pathlib import Path
from typing import Optional

import gradio as gr
import psutil
import torch
from diffusers import FluxPipeline


ROOT = Path(__file__).resolve().parent
MODEL_ID = "black-forest-labs/FLUX.1-schnell"
MODEL_DIR = ROOT / "models" / "FLUX.1-schnell"
HF_HOME = ROOT / "hf_home"

os.environ.setdefault("HF_HOME", str(HF_HOME))
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
os.environ.setdefault("OMP_NUM_THREADS", "32")
os.environ.setdefault("MKL_NUM_THREADS", "32")

pipe: Optional[FluxPipeline] = None


def memory_status() -> str:
    mem = psutil.virtual_memory()
    used = (mem.total - mem.available) / 1024**3
    total = mem.total / 1024**3
    return f"RAM: {used:.1f} / {total:.1f} GB. Device: CPU/RAM."


def load_pipeline() -> FluxPipeline:
    global pipe
    if pipe is not None:
        return pipe

    source = MODEL_DIR if (MODEL_DIR / "model_index.json").exists() else MODEL_ID
    pipe = FluxPipeline.from_pretrained(
        source,
        torch_dtype=torch.float32,
        low_cpu_mem_usage=True,
    )
    pipe.set_progress_bar_config(disable=False)
    return pipe


def generate(prompt: str, width: int, height: int, steps: int, seed: int):
    if not prompt.strip():
        raise gr.Error("Введите промпт.")

    generator = None
    if seed >= 0:
        generator = torch.Generator(device="cpu").manual_seed(seed)

    result = load_pipeline()(
        prompt=prompt.strip(),
        width=width,
        height=height,
        num_inference_steps=steps,
        guidance_scale=0.0,
        generator=generator,
    )
    return result.images[0], memory_status()


with gr.Blocks(title="DemonsForge FLUX") as demo:
    gr.Markdown("# DemonsForge FLUX")
    with gr.Row():
        with gr.Column(scale=1):
            prompt = gr.Textbox(label="Prompt", lines=6)
            with gr.Row():
                width = gr.Slider(512, 1536, value=1024, step=64, label="Width")
                height = gr.Slider(512, 1536, value=1024, step=64, label="Height")
            steps = gr.Slider(1, 12, value=4, step=1, label="Steps")
            seed = gr.Number(value=-1, precision=0, label="Seed (-1 random)")
            run = gr.Button("Generate", variant="primary")
            status = gr.Textbox(label="Status", value=memory_status)
        output = gr.Image(label="Output", type="pil")

    run.click(
        generate,
        inputs=[prompt, width, height, steps, seed],
        outputs=[output, status],
    )


if __name__ == "__main__":
    demo.queue(default_concurrency_limit=1).launch(
        server_name="0.0.0.0",
        server_port=7862,
        share=False,
    )
