---
title: Dağıtık Eğitim
description: DeepSpeed ZeRO, FSDP ve Unsloth backend'i ile çoklu-GPU eğitim.
---

# Dağıtık Eğitim

Modeliniz tek GPU belleğinden büyüdüğünde — ya da sadece daha hızlı eğitmek istediğinizde — dağıtık eğitim devreye girer. ForgeLM DeepSpeed ZeRO-2/3, PyTorch FSDP ve Unsloth tek-GPU hızlandırma backend'ini destekler.

## Karar ağacı

```mermaid
flowchart TD
    Q1{Kaç GPU<br/>var?}
    Q2{Tek GPU<br/>OOM mu?}
    Q3{Çoklu-node<br/>gerekli mi?}
    Q4{DeepSpeed mi<br/>PyTorch yerli mi?}

    Q1 -->|1| Q2
    Q1 -->|2-8| Q4
    Q1 -->|>8 node| Q3
    Q2 -->|Hayır| Unsloth([Unsloth + LoRA])
    Q2 -->|Evet| QLoRA([QLoRA])
    Q3 --> ZeRO3([ZeRO-3])
    Q4 -->|DeepSpeed| Z[ZeRO-2 veya ZeRO-3]
    Q4 -->|PyTorch yerli| FSDP([FSDP])

    classDef question fill:#161a24,stroke:#0ea5e9,color:#e6e7ec
    classDef result fill:#1c2030,stroke:#22c55e,color:#e6e7ec
    class Q1,Q2,Q3,Q4 question
    class Unsloth,QLoRA,ZeRO3,Z,FSDP result
```

## Backend özeti

| Backend | Çoklu-GPU? | Çoklu-node? | Notlar |
|---|---|---|---|
| **Tek GPU + Unsloth** | Hayır | Hayır | Llama/Qwen/Mistral'da vanilla'dan 2-5× hızlı. Tek GPU'daysanız önce bunu deneyin. |
| **DeepSpeed ZeRO-2** | Evet | Evet | Optimizer state'i sharder. İyi hız, her modelde çalışır. |
| **DeepSpeed ZeRO-3** | Evet | Evet | Optimizer + gradient + parametre sharder. Çok büyük modeller için şart. |
| **DeepSpeed ZeRO-3 Offload** | Evet | Evet | CPU/NVMe'ye boşaltır. Devasa modelleri sığdırmak için hızdan ödün verir. |
| **FSDP** | Evet | Evet | PyTorch yerli. Aynı konfigürasyonda ZeRO-3'ten biraz hızlı; ekosistemi daha az olgun. |

## Unsloth (tek GPU)

Unsloth, Llama, Qwen, Mistral ve birkaç model için drop-in optimizasyondur. Attention ve MLP katmanlarını Triton'da yeniden yazarak ~2-5× hızlanma sağlar; kalite kaybı yoktur.

```yaml
model:
  name_or_path: "Qwen/Qwen2.5-7B-Instruct"
  backend: "unsloth"                    # ihtiyacınız olan tek bayrak

training:
  trainer_type: "sft"
  # ... eğitim config'i değişmez
```

:::tip
Unsloth model-özgü kernel'lere sahiptir. Mimariniz desteklenmiyorsa ForgeLM uyarı bırakır ve standart backend'e döner. Desteklenen aileler [Konfigürasyon Referansı](#/reference/configuration)'nda listelenmiştir.
:::

## DeepSpeed ZeRO-2

ZeRO-2 optimizer state'i sharder (Adam gibi adaptif optimizer'larda en ağır VRAM bileşeni). 4-8 GPU'da 13B-30B modeller için etkili.

```yaml
distributed:
  strategy: "deepspeed"
  zero_stage: 2
  gradient_accumulation_steps: 4
  cpu_offload: false
```

Başlatma:

```shell
$ accelerate launch --num_processes 4 -m forgelm --config configs/run.yaml
# veya
$ deepspeed --num_gpus 4 -m forgelm --config configs/run.yaml
```

## DeepSpeed ZeRO-3

ZeRO-3 ek olarak gradient ve parametreleri de GPU'lar arası sharder. Her GPU modelin sadece `1/N`'ini tutar. 70B+ modeller için şart.

```yaml
distributed:
  strategy: "deepspeed"
  zero_stage: 3
  gradient_accumulation_steps: 8
  cpu_offload: false                    # 70B'i 8x24 GB'a sığdırmak için true
  nvme_offload_path: null               # ZeRO-Infinity için NVMe yolu
```

| Model | GPU | ZeRO-3 + offload? |
|---|---|---|
| 30B | 4× A100 40 GB | Opsiyonel |
| 70B | 8× A100 40 GB | CPU offload gerekli |
| 70B | 4× A100 80 GB | Offload gerekmez |
| 405B | 8× H100 80 GB | NVMe offload |

## FSDP (PyTorch yerli)

FSDP, ZeRO-3 gibi sharder ama PyTorch'un yerli FullyShardedDataParallel'ini kullanır. Aynı kurulumda biraz hızlı; ekosistem desteği biraz daha az (ör. bazı HF entegrasyonları DeepSpeed bekler).

```yaml
distributed:
  strategy: "fsdp"
  fsdp_state_dict_type: "FULL_STATE_DICT"
  fsdp_auto_wrap_policy: "TRANSFORMER_BASED_WRAP"
  fsdp_offload_params: false
```

## Gradient accumulation

Hangi backend'i kullanırsanız kullanın, gradient accumulation VRAM'in izin verdiğinden büyük etkili batch size'a izin verir:

```yaml
training:
  per_device_train_batch_size: 1        # cihaz başına
  gradient_accumulation_steps: 32       # etkili batch = 1 × 32 × num_gpus
```

8 GPU × 1 batch × 32 accumulation = 256 etkili batch size — büyük eğitim koşularının çoğunun hedefi.

## Sık hatalar

:::warn
**ZeRO-3 + LoRA yüklemesi başarısız.** ZeRO-3, eğitilmeyen parametreler için özel işleme gerektirir. `lora.modules_to_save`'i dikkatli ayarlayın ve raw `python -m` yerine `accelerate launch` kullanın.
:::

:::warn
**DeepSpeed ve FSDP config'lerini karıştırmak.** Birini seçin. Şema, aynı anda `distributed.zero_stage` ve `distributed.fsdp_*` ayarlamayı reddeder.
:::

:::warn
**Node'lar arası tutarsız batch boyutları.** Tüm node'lar batch size ve accumulation üzerinde anlaşmalı. ForgeLM uyumsuzlukta erkenden hata verir; her node'dan doğrulamayı unutmayın — başlatma node'undan `--dry-run` yeterli.
:::

:::tip
Çoklu-node için node'lar arası SSH erişimi yapılandırın ve node listesini kaydetmek için `accelerate config` kullanın. ForgeLM ortaya çıkan config'i otomatik alır.
:::

## Bkz.

- [GaLore](#/training/galore) — daha az VRAM'le full-parametre eğitim, ZeRO-3 alternatifi.
- [VRAM Fit-Check](#/operations/vram-fit-check) — çoklu-GPU iş başlatmadan önce doğrula.
- [CI/CD Hatları](#/operations/cicd) — otomatik hatlarda çoklu-GPU eğitim.
