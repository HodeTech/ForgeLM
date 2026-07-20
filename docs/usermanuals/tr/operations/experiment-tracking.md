---
title: Deney Takibi
description: report_to ayarı üzerinden W&B, MLflow ve TensorBoard entegrasyonu.
---

# Deney Takibi

ForgeLM deney takibini yeniden icat etmez — ekibinizin zaten kullandığı şeye `training.report_to` alanı üzerinden entegre olur. Desteklenen backend'ler: `tensorboard`, `wandb`, `mlflow` ve `none` (`forgelm/config.py` içindeki `TrainingConfig.report_to` üzerindeki dört `Literal` değeri).

## Hızlı örnek

```yaml
training:
  trainer_type: "sft"
  report_to: "wandb"                     # tek değer: tensorboard | wandb | mlflow | none
  run_name: "customer-support-v1.2.0"    # opsiyonel; None ise otomatik üretilir
```

ForgeLM, konfigüre edilen backend'e loss, learning rate, eval metrikleri ve benchmark puanları akıtır. **Per-backend nested config blokları (örn. `training.wandb: { project: ... }`) şemanın parçası değildir** — her backend'in connection / authentication / project / artifact davranışı kendi well-known environment variable'ları üzerinden konfigüre edilir (HF Transformers `Trainer`'ının izlediği framework yöntemi). ForgeLM tarafındaki tek knob'lar `training.report_to` (hangi backend) ve `training.run_name`'dir.

## Desteklenen backend'ler

### Weights & Biases (W&B)

```yaml
training:
  report_to: "wandb"
```

Konfigürasyon environment variable'ları üzerinden (nested YAML bloğu yok):

- `WANDB_API_KEY` — auth token'ı (ya da eğitim host'unda bir kez `wandb login` çalıştırın).
- `WANDB_PROJECT` — proje adı.
- `WANDB_ENTITY` — takım / org slug'ı.
- `WANDB_LOG_MODEL` — checkpoint'leri W&B artefakt olarak yüklemek için `true`.

W&B `[tracking]` extra'sını gerektirir: `pip install 'forgelm[tracking]'`.

### MLflow

```yaml
training:
  report_to: "mlflow"
```

Konfigürasyon environment variable'ları üzerinden:

- `MLFLOW_TRACKING_URI` — sunucu URL'si (örn. `http://mlflow.internal:5000`).
- `MLFLOW_EXPERIMENT_NAME` — deney adı.
- `MLFLOW_TRACKING_USERNAME` / `MLFLOW_TRACKING_PASSWORD` (ya da `MLFLOW_TRACKING_TOKEN`).

MLflow `[tracking]` extra'sını gerektirir.

### TensorBoard

```yaml
training:
  report_to: "tensorboard"
```

Varsayılan. Log dosyaları `<training.output_dir>/runs/`'e iner. Dış servis gerekmez ama TensorBoard entegrasyonu için `tensorboard` (PyTorch ≥ 1.4 ile) veya `tensorboardX` ayrıca kurulmalıdır — HF Transformers ikisinden birini import-time'da arar (`transformers.integrations.is_tensorboard_available`) ve hiçbiri yoksa `TensorBoardCallback` requires tensorboard to be installed yüksek sesle başarısız olur.

### Birden çok backend'e akıtma

`training.report_to` tek-Literal değerdir, list değil. Aynı koşumda birden çok backend'e akıtmak için ForgeLM'in `--report-to` CLI override'ını kullanın; `transformers.TrainingArguments.report_to`'nun list-değer kabul eden constructor argümanına denk gelir. (`TRAINER_REPORT_TO` env var'ı HF Transformers konvansiyonu **değildir** ve ForgeLM tarafından da tanınmaz — yalnızca constructor / CLI override yolları desteklenir.) Tek-Literal config alanı bir kanonik backend'i pin'leyen güvenli varsayılandır.

## Loglanan şeyler

| Metrik | Ne zaman |
|---|---|
| `train/loss` | Her adım |
| `train/lr` | Her adım |
| `train/grad_norm` | Her adım (HF Trainer her zaman loglar) |
| `eval/loss` | Her eval aralığı |
| `benchmark/<görev>` | Koşu başına bir kez (eval sonrası) |
| `safety/<kategori>/max` | Koşu başına bir kez (güvenlik eval sonrası) |
| `safety/<kategori>/mean` | Koşu başına bir kez |
| `system/gpu_utilization` | Her 30 s'de örneklenir |
| `system/vram_used_gb` | Her 30 s'de örneklenir |

## Koşu adlandırma

```yaml
training:
  run_name: "customer-support-v1-2"     # plain string; null = otomatik üretilir
```

`training.run_name` ForgeLM tarafındaki tek koşu-adlandırma knob'udur. `training.tags:` listesi ve `training.notes:` alanı **yoktur** — tag / note / artifact-upload / artifact-type ayarlarını trainer'ı çağırmadan önce **backend'in kendi environment variable'ları** üzerinden set edin:

```bash
# W&B
export WANDB_TAGS="dpo,qlora,tr,v1.2"
export WANDB_NOTES="dpo_beta 0.1'den 0.15'e çıkarıldı"
export WANDB_LOG_MODEL="checkpoint"   # or "end" — controls artifact upload

# MLflow
export MLFLOW_TAGS='{"trainer":"dpo","quantization":"qlora"}'
```

## Artifact yönetimi

ForgeLM `training.wandb:` veya `training.mlflow:` sub-bloğu **sunmaz**. Artifact yükleme backend environment variable'ları üzerinden konfigüre edilir (`WANDB_LOG_MODEL`, `MLFLOW_TRACKING_URI` + launch wrapper'ınızda per-run logging API'leri) — HF Transformers `Trainer`'ının izlediği aynı yöntem.

Çok büyük checkpoint'ler için W&B/MLflow artifact store'larından çok model registry (HuggingFace Hub) tercih edin. Free tier'lar küçük boyutlarda sınırlanır.

## Koşu karşılaştırma

Her backend'in UI'sı karşılaştırmayı doğal şekilde halleder — karşılaştırılabilir koşular `run_name` öneki, etiketler ve config hash paylaşır. Yerleşik CLI özeti yolda:

> Not: `forgelm compare-runs` subcommand'ı gelecekteki bir Pro CLI sürümü için planlanmıştır ([GitHub'daki Phase 13 yol haritası](https://github.com/HodeTech/ForgeLM/blob/main/docs/roadmap.md)). Bugün aynı karşılaştırma tracking backend'inizin UI'sı (W&B / MLflow / Comet) veya her koşunun JSON envelope'una karşı küçük bir `jq` ile koşturulur.

Bugünkü çalışan akış (W&B / MLflow / Comet UI canonical yüzey; aşağıdaki ad-hoc CLI karşılaştırma için `jq` kısayolu):

```shell
$ for v in v1.0 v1.1 v1.2; do
    jq --arg v "$v" '{run: $v, hellaswag: .benchmark.hellaswag, truthfulqa: .benchmark.truthfulqa, S5_max: .safety.S5}' \
       runs/$v/eval.json
  done
```

Özel `forgelm compare-runs` UX'i (planlanan, BUGÜN runnable DEĞİL):

```text
# preview (planlanan Pro CLI — şu anda runnable değil)
forgelm compare-runs runs/v1.0 runs/v1.1 runs/v1.2
                  v1.0    v1.1    v1.2
hellaswag        0.612   0.617   0.621
truthfulqa       0.480   0.482   0.475   ↓
S5_max           0.041   0.038   0.082   ↑↑
loss             1.43    1.39    1.35
```

Oklar otomatik türetilir: anlamlı iyileşme yeşil, regresyon kırmızı.

## Sık hatalar

:::warn
**API key'leri hardcoded yapmak.** W&B / MLflow / Comet key'lerini doğrudan YAML'a koymayın. Her zaman `${ENV_VAR}` interpolasyonu kullanın. Sırların dump'lanmış config'e dahil olmadığını teyit etmek için `audit_log.jsonl`'a bakın.
:::

:::warn
**Erişilemeyen backend'lere raporlama.** `wandb.ai` çalışmıyorsa veya firewall'unuz engelliyorsa ForgeLM uyarı bırakır ama eğitimi başarısız etmez. Uyarıları izleyin; aksi halde hiçbir şeyin loglanmadığını kaçırabilirsiniz.
:::

:::tip
**Birden çok backend paralel kullanın.** Bir koşu sırasında lokal debugging için TensorBoard, sonradan ekipler-arası işbirliği için W&B. İkisini de konfigüre edin — ekstra maliyet yok.
:::

## Bkz.

- [Konfigürasyon Referansı](#/reference/configuration) — tam `report_to` ve backend ayarları.
- [JSON Çıktı Modu](#/operations/cicd) — kendi takibinize log'ları pipe etmek için.
