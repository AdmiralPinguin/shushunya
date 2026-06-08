#!/usr/bin/env python3
import os
from pathlib import Path
from typing import Optional

import gradio as gr
import psutil
import torch
from diffusers import StableDiffusionXLPipeline


ROOT = Path(__file__).resolve().parent
MODEL_ID = "stabilityai/stable-diffusion-xl-base-1.0"
MODEL_DIR = ROOT / "models" / "stable-diffusion-xl-base-1.0"
HF_HOME = ROOT / "hf_home"

os.environ.setdefault("HF_HOME", str(HF_HOME))
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
os.environ.setdefault("OMP_NUM_THREADS", "32")
os.environ.setdefault("MKL_NUM_THREADS", "32")

pipe: Optional[StableDiffusionXLPipeline] = None


def memory_status() -> str:
    mem = psutil.virtual_memory()
    used = (mem.total - mem.available) / 1024**3
    total = mem.total / 1024**3
    return f"RAM: {used:.1f} / {total:.1f} GB. Device: CPU/RAM."


def load_pipeline() -> StableDiffusionXLPipeline:
    global pipe
    if pipe is not None:
        return pipe

    source = MODEL_DIR if (MODEL_DIR / "model_index.json").exists() else MODEL_ID
    pipe = StableDiffusionXLPipeline.from_pretrained(
        source,
        torch_dtype=torch.float32,
        low_cpu_mem_usage=True,
        use_safetensors=True,
    )
    pipe.set_progress_bar_config(disable=False)
    return pipe


def generate(
    prompt: str,
    negative_prompt: str,
    width: int,
    height: int,
    steps: int,
    guidance: float,
    seed: int,
):
    if not prompt.strip():
        raise gr.Error("Введите промпт.")

    generator = None
    if seed >= 0:
        generator = torch.Generator(device="cpu").manual_seed(seed)

    result = load_pipeline()(
        prompt=prompt.strip(),
        negative_prompt=negative_prompt.strip() or None,
        width=width,
        height=height,
        num_inference_steps=steps,
        guidance_scale=guidance,
        generator=generator,
    )
    return result.images[0], memory_status()


with gr.Blocks(title="DemonsForge SDXL") as demo:
    gr.Markdown("# DemonsForge SDXL")
    with gr.Row():
        with gr.Column(scale=1):
            prompt = gr.Textbox(label="Prompt", lines=5)
            negative_prompt = gr.Textbox(label="Negative prompt", lines=2)
            with gr.Row():
                width = gr.Slider(512, 1536, value=1024, step=64, label="Width")
                height = gr.Slider(512, 1536, value=1024, step=64, label="Height")
            with gr.Row():
                steps = gr.Slider(10, 60, value=30, step=1, label="Steps")
                guidance = gr.Slider(1.0, 12.0, value=7.0, step=0.1, label="Guidance")
            seed = gr.Number(value=-1, precision=0, label="Seed (-1 random)")
            run = gr.Button("Generate", variant="primary")
            status = gr.Textbox(label="Status", value=memory_status)
        output = gr.Image(label="Output", type="pil")

    run.click(
        generate,
        inputs=[prompt, negative_prompt, width, height, steps, guidance, seed],
        outputs=[output, status],
    )


if __name__ == "__main__":
    demo.queue(default_concurrency_limit=1).launch(
        server_name="0.0.0.0",
        server_port=7861,
        share=False,
    )
