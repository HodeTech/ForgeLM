---
title: Air-Gap Operasyonu
description: İnternet olmadan eğitim ve değerlendirme — her şeyi önceden cache'leyin, izole ağlarda koşturun.
---

# Air-Gap Operasyonu

Regülasyonlu sektörler (savunma, sağlık, bazı finans) ve yüksek-güvenlikli müşteri ortamları için eğitim, internet erişimi olmayan ağlarda gerçekleşmelidir. ForgeLM tamamen air-gap çalışmak üzere tasarlanmıştır: her internet'e dokunan adımın offline eşdeğeri vardır.

## Online olması gereken (tek sefer)

Air-gap'e geçmeden önce şunları önceden cache'leyin:

| Kaynak | Cache yeri |
|---|---|
| Base model | `~/.cache/huggingface/hub/` |
| Tokenizer | (aynı — modelle birlikte) |
| Llama Guard | `~/.cache/huggingface/hub/` |
| `lm-evaluation-harness` görev tanımları | `~/.cache/lm-evaluation-harness/` |
| Python paketleri (`forgelm` + extra'lar) | yerel pip wheel cache |

Bağlı bir makineden önceden cache'leyin:

```shell
$ forgelm cache-models \
    --model "Qwen/Qwen2.5-7B-Instruct" \
    --safety "meta-llama/Llama-Guard-3-8B" \
    --output ./airgap-bundle/

$ forgelm cache-tasks \
    --tasks hellaswag,arc_easy,truthfulqa,mmlu \
    --output ./airgap-bundle/
```

`airgap-bundle/`'i offline host'a aktarın (USB, scp, ne tür güvenlik politikanız izin veriyorsa).

## Air-gap host üzerinde

Yerel cache'i göstermek ve ağ çağrılarını kapatmak için environment variable'lar:

```shell
$ export HF_HOME="$(pwd)/airgap-bundle/huggingface"
$ export TRANSFORMERS_OFFLINE=1
$ export HF_HUB_OFFLINE=1
$ export HF_DATASETS_OFFLINE=1

$ forgelm --config configs/run.yaml --offline
```

`--offline` bayrağı ForgeLM'e ağ çağrısı yapmamasını söyler. Config'iniz cache'lenmemiş bir modele referans verirse eğitim açık bir hata mesajıyla başarısız olur:

```text
ERROR: model "meta-llama/Llama-Guard-3-8B" not in local cache.
       Bağlı bir makineden önceden cache'leyin: forgelm cache-models --safety meta-llama/Llama-Guard-3-8B
```

## `--offline`'ın zorladığı

| Alt sistem | `--offline` ile davranış |
|---|---|
| HuggingFace Hub indirmeleri | Devre dışı. Model/tokenizer cache'de yoksa hızlı başarısız. |
| W&B / MLflow / Comet | Devre dışı (veya MLflow için file URI ile sadece-yerel). |
| OpenAI / Anthropic judge'lar | Devre dışı. Yerel judge model kullanın. |
| Webhook'lar | Devre dışı (veya `webhook.allow_private` ile iç host'larla sınırlı). |
| Runtime'da pip install | Devre dışı. |
| Telemetry | Yok — ForgeLM zaten asla eve telefon etmez. |

## Yerel sentetik veri

Air-gap'te sentetik veri üretimi gerekirse yerel teacher kullanın:

```yaml
synthetic:
  enabled: true
  teacher_model: "Qwen/Qwen2.5-72B-Instruct"  # HF Hub id — cache'lenmiş olmalı
  teacher_backend: "local"                    # "api" değil — network çağrısı yok
```

Nested `synthetic.teacher:` alt-bloğu (`provider` / `model` / `load_in_4bit`) yoktur — `teacher_model` ve `teacher_backend` `synthetic:` üzerinde düz alanlardır. API-destekli bir teacher (`teacher_backend: "api"`, örn. OpenAI / Anthropic) `--offline`'da başarısız olur.

## Yerel LLM-as-judge

```yaml
evaluation:
  llm_judge:
    enabled: true
    judge_model: "Qwen/Qwen2.5-72B-Instruct"  # düz string — yerel model yolu veya HF Hub id
```

`judge_model` nested bir `{provider, model}` objesi değil, düz bir string alandır — `judge_api_key_env` ayarlanmamış (varsayılan) bir yerel HF yolu/id yerel bir judge'a çözümlenir.

72B yerel judge `gpt-4o-mini`'den yavaş ama kalite tipik kullanım için karşılaştırılabilir. Bkz. [LLM-as-Judge](#/evaluation/judge).

## Air-gap'te değerlendirme

`lm-evaluation-harness` genelde görev tanımlarını ve dataset'leri runtime'da indirir. Önceden cache'leyin:

```shell
$ forgelm cache-tasks --tasks hellaswag,arc_easy,truthfulqa,mmlu
```

Cache'i işaret edecek bir `evaluation.benchmark.tasks_dir` (veya başka bir) YAML alanı yoktur — `forgelm cache-tasks`, `lm-evaluation-harness`'ın altındaki `datasets` kütüphanesinin eval zamanında okuduğu aynı `HF_DATASETS_CACHE` (yoksa `HF_HOME/datasets`'e düşer) environment variable'ını kullanır. `--benchmark-only` çalıştırmadan önce yukarıdaki ["Air-gap host üzerinde"](#air-gap-host-üzerinde) bölümündeki environment variable'ları (`HF_HOME`, `HF_DATASETS_OFFLINE=1`) ayarlayın; cache otomatik olarak kullanılır — config bloğuna gerek yoktur.

## Air-gap modu doğrulama

```shell
$ forgelm doctor --offline
forgelm doctor — environment check

  [✓ pass] python.version          Python 3.11.4 (CPython).
  [✓ pass] torch.cuda              torch 2.4.0 with CUDA 12.4.
  [✓ pass] gpu.inventory           1 GPU(s) — GPU0: NVIDIA A100 (80.0 GiB).
  [✓ pass] extras.qlora            Installed (module bitsandbytes, purpose: 4-bit / 8-bit QLoRA training).
  [✓ pass] extras.eval             Installed (module lm_eval, purpose: lm-evaluation-harness benchmark scoring).
  [! warn] extras.tracking         Optional extra missing — install with: pip install 'forgelm[tracking]' (purpose: Weights & Biases experiment tracking).
  [✓ pass] hf_hub.offline_cache    HF cache at /opt/airgap/huggingface/hub: 3.4 GiB across 47 file(s). HF_HUB_OFFLINE=1.
  [✓ pass] disk.workspace          Workspace /opt/airgap — 412.0 GiB free of 500.0 GiB.
  [! warn] operator.identity       FORGELM_OPERATOR not set; audit events will fall back to 'airgap-op@nodeA'. Pin FORGELM_OPERATOR=<id> for CI / pipeline runs.

Summary: 7 pass, 2 warn, 0 fail.
```

`forgelm doctor --offline` herhangi bir şeyi `fail` olarak raporlarsa, air-gap operatörünün vaktini boşa harcamadan *önce* düzeltin. HF cache taraması önce `HF_HUB_CACHE`'i, sonra `HF_HOME/hub`'ı, en son varsayılan `~/.cache/huggingface/hub`'ı dener — taramayı deterministik yapmak için `HF_HUB_CACHE`'i pakete dahil ettiğiniz cache'e işaret ettirin.

## Paket boyutu tahmini

Tipik bir fine-tuning projesi için:

| Kaynak | Yaklaşık boyut |
|---|---|
| Base model (7B) safetensors | 14 GB |
| Llama Guard 3 8B | 16 GB |
| Tokenizer + config | <100 MB |
| Eval görev tanımları + cache'li dataset'ler | 500 MB - 2 GB |
| Python wheel'ler (forgelm + extra'lar) | 1-2 GB |
| **Toplam** | **~32-34 GB** |

Depolamayı buna göre planlayın.

## Sık hatalar

:::warn
**Environment variable'ları ayarlamayı unutmak.** `HF_HUB_OFFLINE=1` olmadan HuggingFace kütüphaneleri sessizce eve telefon etmeye çalışıp yerel cache'e fallback yapar. Fallback iyi çalışır ama dışarıya bağlantı yapmış olursunuz — bazı ortamlarda compliance ihlali.
:::

:::warn
**Önceden cache'lemeden `--offline` kullanmak.** İlk model yüklemesinde eğitim başarısız olur. Eksik kaynakları önden yakalamak için eğitim başlatmadan önce `forgelm doctor --offline` çalıştırın.
:::

:::tip
**Her şeyi önceden cache'lenmiş bir CI imajı kurun.** Air-gap CI için önceden-cache'li paketi Docker imajına paketleyin. Her CI koşusu cache'lenmiş durumdan başlar. Bkz. [Docker](#/operations/docker).
:::

## Bkz.

- [Docker](#/operations/docker) — önceden-cache'li imaj kurma.
- [Kurulum](#/getting-started/installation) — temel kurulum.
- [LLM-as-Judge](#/evaluation/judge) — yerel judge model.
