---
title: Docker
description: Reproducible runs across local, on-prem, and cloud — same image, same behaviour.
---

# Docker

ForgeLM ships an official Dockerfile and `docker-compose.yml` for reproducible training runs across local development, on-prem clusters, and cloud GPUs.

## Quick start

Pull the official image:

```shell
$ docker pull ghcr.io/hodetech/forgelm:latest
$ docker run --gpus all \
    -v $PWD:/workspace \
    -v $HOME/.cache/huggingface:/root/.cache/huggingface \
    -e HF_TOKEN \
    ghcr.io/hodetech/forgelm:latest \
    forgelm --config /workspace/configs/run.yaml
```

The `--gpus all` flag requires NVIDIA Container Toolkit on the host. Mount your project as `/workspace` and the HuggingFace cache so model downloads persist across runs.

## Image variants

| Tag | Includes | Size |
|---|---|---|
| `latest` | Base + all extras (ingestion, scale, export, deepspeed) | ~12 GB |
| `slim` | Base only — no extras | ~6 GB |
| `airgap` | Base + extras + pre-cached Qwen 2.5 7B + Llama Guard | ~30 GB |

For most teams, `latest` is the right pick. For air-gap deployments, build a custom image based on `airgap` with your specific models pre-cached. See [Air-Gap Operation](#/operations/air-gap).

## docker-compose

For multi-service local development (training + experiment tracking + webhook receiver):

```yaml
# docker-compose.yml
version: "3.9"

services:
  trainer:
    image: ghcr.io/hodetech/forgelm:latest
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]
    volumes:
      - .:/workspace
      - hf-cache:/root/.cache/huggingface
    environment:
      - HF_TOKEN
      - WANDB_API_KEY
      - SLACK_WEBHOOK
    command: forgelm --config /workspace/configs/run.yaml

  mlflow:
    image: ghcr.io/mlflow/mlflow:latest
    ports:
      - "5000:5000"
    volumes:
      - ./mlruns:/mlflow/mlruns
    command: mlflow server --host 0.0.0.0 --port 5000

volumes:
  hf-cache: {}
```

```shell
$ docker compose up trainer
```

## Building from source

If you need custom modifications:

```shell
$ git clone https://github.com/HodeTech/ForgeLM
$ cd ForgeLM
$ docker build -t forgelm:custom -f Dockerfile .
$ docker run --gpus all forgelm:custom forgelm --version
```

The `Dockerfile` is multi-stage: a builder stage installs heavy dependencies (CUDA, PyTorch, bitsandbytes), then a runtime stage copies in the compiled artefacts. Total build time: ~15 minutes on a fast connection.

## Air-gap image

For air-gapped environments, build an image with everything pre-cached:

```dockerfile
# airgap.Dockerfile
FROM ghcr.io/hodetech/forgelm:latest

# Pre-cache the base model
RUN forgelm cache-models --model "Qwen/Qwen2.5-7B-Instruct"

# Pre-cache Llama Guard
RUN forgelm cache-models --safety "meta-llama/Llama-Guard-3-8B"

# Pre-cache eval tasks
RUN forgelm cache-tasks --tasks hellaswag,arc_easy,truthfulqa,mmlu

# Force offline mode in container
ENV HF_HUB_OFFLINE=1
ENV TRANSFORMERS_OFFLINE=1
ENV HF_DATASETS_OFFLINE=1

ENTRYPOINT ["forgelm"]
CMD ["--help"]
```

Build, save, transfer:

```shell
$ docker build -t forgelm-airgap -f airgap.Dockerfile .
$ docker save forgelm-airgap | gzip > forgelm-airgap.tar.gz
# Transfer forgelm-airgap.tar.gz to the air-gapped host
$ docker load < forgelm-airgap.tar.gz
```

## Common pitfalls

:::warn
**Forgetting `--gpus all`.** Without it, the container can't see your GPU and ForgeLM falls back to CPU — training a 7B model on CPU is impossible.
:::

:::warn
**Not mounting the HuggingFace cache.** Every container start re-downloads the base model (15+ GB). Always mount `~/.cache/huggingface` so downloads persist.
:::

:::warn
**Hardcoded API keys in Dockerfile.** Build args bake values into the image layer history. Use runtime environment variables instead: `docker run -e HF_TOKEN ...`.
:::

:::tip
For Kubernetes deployments, use the same image with a custom resource definition pointing at GPU nodes. ForgeLM's behaviour is identical — just the orchestration layer changes.
:::

## See also

- [Air-Gap Operation](#/operations/air-gap) — for fully offline deployments.
- [CI/CD Pipelines](#/operations/cicd) — using the image in GitHub Actions.
- [Installation](#/getting-started/installation) — non-Docker install.
