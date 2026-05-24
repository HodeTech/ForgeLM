---
title: Docker
description: Yerel, on-prem ve cloud arası yeniden üretilebilir koşular — aynı imaj, aynı davranış.
---

# Docker

ForgeLM, yerel geliştirme, on-prem cluster ve cloud GPU'lar arası yeniden üretilebilir eğitim koşuları için resmi bir Dockerfile ve `docker-compose.yml` yayınlar.

## Hızlı başlangıç

Resmi imajı çekin:

```shell
$ docker pull ghcr.io/hodetech/forgelm:latest
$ docker run --gpus all \
    -v $PWD:/workspace \
    -v $HOME/.cache/huggingface:/root/.cache/huggingface \
    -e HF_TOKEN \
    ghcr.io/hodetech/forgelm:latest \
    forgelm --config /workspace/configs/run.yaml
```

`--gpus all` host'ta NVIDIA Container Toolkit ister. Projenizi `/workspace` olarak ve HuggingFace cache'ini mount edin; model indirmeleri koşular arası kalıcı olur.

## İmaj varyantları

| Tag | İçerir | Boyut |
|---|---|---|
| `latest` | Base + tüm extra'lar (ingestion, scale, export, deepspeed) | ~12 GB |
| `slim` | Sadece base — extra yok | ~6 GB |
| `airgap` | Base + extra'lar + önceden-cache'li Qwen 2.5 7B + Llama Guard | ~30 GB |

Çoğu ekip için `latest` doğru tercih. Air-gap deployment'ları için `airgap`'ten türeyen, kendi spesifik modellerinizi içeren özel imaj kurun. Bkz. [Air-Gap Operasyonu](#/operations/air-gap).

## docker-compose

Çoklu-servis yerel geliştirme (eğitim + deney takibi + webhook receiver) için:

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

## Kaynaktan build

Özel modifikasyonlar gerekirse:

```shell
$ git clone https://github.com/HodeTech/ForgeLM
$ cd ForgeLM
$ docker build -t forgelm:custom -f Dockerfile .
$ docker run --gpus all forgelm:custom forgelm --version
```

Dockerfile çok-aşamalı: builder aşaması ağır bağımlılıkları (CUDA, PyTorch, bitsandbytes) kurar, sonra runtime aşaması derlenmiş artifact'ları kopyalar. Toplam build süresi: hızlı bağlantıda ~15 dakika.

## Air-gap imajı

Air-gap ortamlar için her şey önceden cache'lenmiş imaj kurun:

```dockerfile
# airgap.Dockerfile
FROM ghcr.io/hodetech/forgelm:latest

# Base modeli önceden cache'le
RUN forgelm cache-models --model "Qwen/Qwen2.5-7B-Instruct"

# Llama Guard'ı önceden cache'le
RUN forgelm cache-models --safety "meta-llama/Llama-Guard-3-8B"

# Eval görevlerini önceden cache'le
RUN forgelm cache-tasks --tasks hellaswag,arc_easy,truthfulqa,mmlu

# Konteynerda offline modu zorla
ENV HF_HUB_OFFLINE=1
ENV TRANSFORMERS_OFFLINE=1
ENV HF_DATASETS_OFFLINE=1

ENTRYPOINT ["forgelm"]
CMD ["--help"]
```

Build, kaydet, aktar:

```shell
$ docker build -t forgelm-airgap -f airgap.Dockerfile .
$ docker save forgelm-airgap | gzip > forgelm-airgap.tar.gz
# forgelm-airgap.tar.gz'ı air-gap host'a aktar
$ docker load < forgelm-airgap.tar.gz
```

## Sık hatalar

:::warn
**`--gpus all`'i unutmak.** Bu olmadan konteyner GPU'nuzu göremez ve ForgeLM CPU'ya fallback yapar — 7B modeli CPU'da eğitmek imkânsız.
:::

:::warn
**HuggingFace cache'ini mount etmemek.** Her konteyner başlangıcında base model (15+ GB) yeniden indirilir. İndirmelerin kalıcı olması için `~/.cache/huggingface`'i her zaman mount edin.
:::

:::warn
**Dockerfile'da hardcoded API key'ler.** Build arg'ları imaj layer geçmişine gömülür. Bunun yerine runtime environment variable kullanın: `docker run -e HF_TOKEN ...`.
:::

:::tip
Kubernetes deployment'ları için aynı imajı GPU node'larına işaret eden custom resource definition'la kullanın. ForgeLM'in davranışı aynı — sadece orkestrasyon katmanı değişir.
:::

## Bkz.

- [Air-Gap Operasyonu](#/operations/air-gap) — tam offline deployment.
- [CI/CD Hatları](#/operations/cicd) — imajı GitHub Actions'da kullanmak.
- [Kurulum](#/getting-started/installation) — Docker dışı kurulum.
