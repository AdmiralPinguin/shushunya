# vLLM Host

Experimental OpenAI-compatible backend for
`google/gemma-4-12B-it-qat-w4a16-ct`.

## Local Files

- Model: `CoreOfMadness/models/google-gemma-4-12B-it-qat-w4a16-ct`
- Runtime: `CoreOfMadness/vllm-host/venv`
- Port: `8081`
- Scripts:
  - `scripts/start-vllm-gemma4-qat.sh`
  - `scripts/stop-vllm-gemma4-qat.sh`
  - `scripts/check-vllm-gemma4-qat.sh`

Model and runtime directories are local-only and are not tracked in Git.

## RTX 2060 12GB Test Result

The model downloads and vLLM 0.24.0 recognizes it as:

- architecture: `Gemma4UnifiedForConditionalGeneration`
- quantization: `compressed-tensors`
- kernel: `MarlinLinearKernel` for `CompressedTensorsWNA16`

The default 16k GPU-only profile does not fit: vLLM loads about `8.28 GiB`
of model weights and needs about `5.25 GiB` more for 16k KV cache.

The following profile can start the HTTP server with `max_model_len=16384`:

```bash
MAX_MODEL_LEN=16384 \
GPU_MEMORY_UTILIZATION=0.96 \
EXTRA_ARGS='--enforce-eager --language-model-only --max-num-batched-tokens 16384 --max-num-seqs 1 --cpu-offload-gb 8 --kv-cache-dtype float16' \
./CoreOfMadness/vllm-host/scripts/start-vllm-gemma4-qat.sh
```

It is not usable on RTX 2060 for generation. The first inference crashes in
the Gemma4 attention kernel:

```text
triton.runtime.errors.OutOfResources: out of resource: shared memory,
Required: 98304, Hardware limit: 65536.
```

`--attention-backend FLEX_ATTENTION` also fails on the same hardware limit,
requiring `163840` shared memory. `--kv-cache-dtype fp8` is also unavailable
on RTX 2060 because vLLM requires SM89+ for FP8 KV on this path.

Conclusion: keep llama.cpp as the working RTX 2060 backend. This vLLM model is
a good candidate to retest after a GPU upgrade.
