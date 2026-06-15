---
title: Deploy Hedefleri
description: Tek komutla Ollama, vLLM, TGI ve HuggingFace Endpoints deployment config üretin.
---

# Deploy Hedefleri

ForgeLM kendi inference sunucusunu yayınlamaz — bu kapsam dışı. Yerine `forgelm deploy` mevcut başlıca sunucular için çalışmaya hazır config'ler üretir: Ollama, vLLM, TGI ve HuggingFace Endpoints.

## Hızlı örnek

```shell
$ forgelm deploy ./checkpoints/customer-support \
    --target ollama \
    --output ./Modelfile
✓ ./Modelfile yazıldı

$ ollama create my-bot -f ./Modelfile
$ ollama run my-bot
```

## Desteklenen hedefler

| Hedef | Çıktı | Kullanım |
|---|---|---|
| `ollama` | `Modelfile` | Yerel CPU/GPU inference, hızlı prototipleme. |
| `vllm` | `vllm-config.yaml` | Yüksek-throughput GPU servis. |
| `tgi` | `tgi-launcher.sh` + Dockerfile | HuggingFace'in text-generation-inference. |
| `hf-endpoints` | `endpoints-config.json` | HuggingFace Inference Endpoints'a tek-tıkla deploy. |

KServe ve NVIDIA Triton v0.5.5'te **dahili** hedef değildir. `forgelm deploy --target` parser'ı yalnızca yukarıdaki dört runtime'ı kabul eder. KServe / Triton'da servis sunan operatörler `InferenceService` manifest'ini veya `model_repository/` layout'unu GGUF / safetensors artefakt'ından elle yazar.

## Ollama

```shell
$ forgelm deploy ./checkpoints/run --target ollama --output Modelfile
```

Üretir:

```text
FROM ./model.q4_k_m.gguf

SYSTEM "Telekom için kibar müşteri-destek temsilcisisin."

PARAMETER temperature 0.7
PARAMETER top_p 0.9
PARAMETER stop "<|im_end|>"

# customer-support v1.2.0 checkpoint'inden ForgeLM 0.5.5 ile üretildi
```

ForgeLM modelin tokenizer'ına dayanarak doğru `PARAMETER` satırlarını seçer (chat template, stop token) — manuel konfigürasyon gerekmez.

## vLLM

```shell
$ forgelm deploy ./checkpoints/run --target vllm --output vllm.yaml
```

Üretir:

```yaml
# vllm.yaml — ForgeLM tarafından üretildi
model: "/data/model"                   # checkpoint'i /data/model olarak mount et
tokenizer_mode: "auto"
dtype: "bfloat16"
max_model_len: 8192
gpu_memory_utilization: 0.85
trust_remote_code: false
served_model_name: "customer-support-v1.2"

# Chat template tokenizer'dan otomatik algılandı
chat_template: null                    # tokenizer'ın varsayılanını kullanır
```

Çalıştır:

```shell
$ python -m vllm.entrypoints.openai.api_server --config vllm.yaml --port 8000
```

Yüksek-throughput için ForgeLM model boyutu ve konfigüre GPU sayısına göre tavsiye edilen `--tensor-parallel-size`'ı da üretir.

## TGI (text-generation-inference)

```shell
$ forgelm deploy ./checkpoints/run --target tgi --output tgi/
$ ls tgi/
launcher.sh    Dockerfile    config.json
```

`launcher.sh`:

```shell
#!/bin/sh
text-generation-launcher \
  --model-id /data/model \
  --port 8080 \
  --max-input-length 4096 \
  --max-total-tokens 8192 \
  --dtype bfloat16 \
  --quantize bitsandbytes-nf4
```

Dockerfile çok aşamalı ve TGI'nin base imajını çeker — registry'nize push'layın ve `docker run` ile çalıştırın.

## HuggingFace Endpoints

```shell
$ forgelm deploy ./checkpoints/run --target hf-endpoints --output endpoints.json
```

Çıktı JSON, HuggingFace'in Inference Endpoints create-endpoint API'si için body:

```shell
$ curl -X POST https://api.endpoints.huggingface.cloud/v2/endpoint \
    -H "Authorization: Bearer $HF_TOKEN" \
    -H "Content-Type: application/json" \
    -d @endpoints.json
```

Veya tek-tıkla deployment için HuggingFace UI'sına yapıştırın.

## Konfigürasyon

```yaml
deployment:
  target: "vllm"
  served_model_name: "customer-support-v1.2"
  max_input_length: 4096
  max_total_tokens: 8192
  gpu_memory_utilization: 0.85
  chat_template: "default"              # veya .jinja dosyası yolu
  system_prompt_default: "Sen..."
```

YAML'da `deployment:` varsa `forgelm` son adım olarak (eval / güvenlik geçtikten sonra) `deploy`'u otomatik çalıştırır.

## Otomatik tespit edilen

| Ayar | Tespit kaynağı |
|---|---|
| Chat template | Modelin tokenizer config'i |
| Stop token'lar | Tokenizer special token'ları |
| `max_model_len` | Eğitimdeki `model.max_length` |
| Quantization | QLoRA çalıştırdığınız, GGUF export edip etmediğiniz vb. |
| Tensor parallelism | Model boyutu + mevcut GPU |

Herhangi bir otomatik tespiti açık YAML ile override edebilirsiniz.

## Sık hatalar

:::warn
**Quant'lamadan önce güvenlik eval'ini atlamak.** Bir q4_k_m GGUF Llama Guard'da full-precision adapter'dan kötü puan alabilir. `safety-eval` henüz GGUF yüklemez; export öncesi HuggingFace checkpoint'ine karşı tekrar koşturun ve quant sonrası sapmayı bilinen bir kısıtlama olarak değerlendirin.
:::

:::warn
**Generic chat template fallback.** Fine-tune özel chat template kullandıysa otomatik tespit base modelin template'ini (farklı format) seçebilir. Önce `forgelm chat` ile interaktif test edin.
:::

:::warn
**Eski template'ler.** Modelleri veya tokenizer'ları değiştirdiğinizde deploy config'lerini yeniden üretin. Yeni tokenizer'a işaret eden eski TGI config'i sessizce hatalı çıktı üretir.
:::

:::tip
ForgeLM'in ürettiği config'ler kaynak run-id ve zaman damgasını içeren bir yorum içerir. Üretilen config'i model release'inizle birlikte commit edin ki geri izleme net olsun.
:::

## Bkz.

- [GGUF Export](#/deployment/gguf-export) — Ollama / llama.cpp hedefleri için.
- [Etkileşimli Chat](#/deployment/chat) — deploy etmeden önce test.
- [Uyumluluk Genel Bakış](#/compliance/overview) — release'e ne ekleneceği.
