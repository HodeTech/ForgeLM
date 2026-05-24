---
title: Audit Log
description: Eğitim, değerlendirme ve geri alma kararlarını append-only event log olarak tutar — Madde 12.
---

# Audit Log

EU AI Act Madde 12, yüksek-riskli AI sistemlerinin operasyonel olarak ilgili olayların log'unu tutmasını gerektirir. ForgeLM'in `audit_log.jsonl` dosyası; eğitim başlangıcı, eval kapıları, otomatik geri alma kararları ve model export'unu kapsayan, append-only ve SHA-256-anchored bir olay dizisidir.

## Format

Satır başına bir JSON nesnesi:

```jsonl
{"ts":"2026-04-29T14:01:32Z","seq":1,"event":"training.started","run_id":"abc123","operator":"ci-runner@ml","_hmac":"..."}
{"ts":"2026-04-29T14:33:08Z","seq":2,"event":"audit.classifier_load_failed","classifier":"meta-llama/Llama-Guard-3-8B","reason":"...","_hmac":"..."}
{"ts":"2026-04-29T14:33:10Z","seq":3,"event":"model.reverted","reason":"safety.regression","metrics":{...},"_hmac":"..."}
{"ts":"2026-04-29T14:33:11Z","seq":4,"event":"pipeline.completed","exit_code":0,"prev_hash":"sha256:beef...","_hmac":"..."}
```

(Tam canonical olay listesi için aşağıdaki "Olay tipleri" tablosuna ve [GitHub'daki Audit Event Kataloğu](https://github.com/HodeTech/ForgeLM/blob/main/docs/reference/audit_event_catalog.md)'na bakın. Eski draft'larda görünen `run_start` / `run_complete` / `data_audit_complete` / `training_epoch_complete` / `benchmark_complete` / `safety_eval_complete` / `auto_revert` adları ship olmadı — `forgelm/` içinde emit eden hiçbir call site yok.)

Her kayıtta:
- **`ts`** — ISO-8601 UTC zaman damgası.
- **`seq`** — koşu içinde monotonik sıra numarası (her koşuda sıfırlanır).
- **`event`** — olay tipi (aşağıda).
- **`prev_hash`** — önceki kaydın SHA-256'sı (tamper-evidence için zincirleme).
- Olaya özgü alanlar.

## Olay tipleri

| Olay | Ne zaman |
|---|---|
| `training.started` | Trainer fine-tuning'e başladığında. |
| `pipeline.completed` | Uçtan-uca CLI çalıştırması exit kod 0 ile bittiğinde. |
| `pipeline.failed` | Pipeline bir hata ile abort olduğunda. |
| `model.reverted` | Auto-revert kalite regresyonundan sonra önceki checkpoint'i geri yüklediğinde. |
| `human_approval.required` | `evaluation.require_human_approval=true` koşumu operatör kararı için duraklattığında. |
| `human_approval.granted` | Operatör `forgelm approve` ile duraklatılmış gate'i onayladığında. |
| `human_approval.rejected` | Operatör `forgelm reject` ile duraklatılmış gate'i reddettiğinde. |
| `audit.classifier_load_failed` | Safety classifier (örn. Llama Guard) yüklenemediğinde. |
| `compliance.governance_exported` | EU AI Act Madde 10 yönetişim raporu yazıldığında. |
| `compliance.artifacts_exported` | Annex IV bundle'ı (manifest + model card + audit zip) yazıldığında. |
| `data.erasure_*` | `forgelm purge` yaşam döngüsünü kapsayan altı-event ailesi (Madde 17). |
| `data.access_request_query` | `forgelm reverse-pii` çağrısı (GDPR Madde 15). |
| `cli.legacy_flag_invoked` | Deprecated bir CLI flag'i kullanıldığında. |

Tam event kataloğu (payload şeması ve emit yeri ile)
[GitHub'daki Audit Event Kataloğu](https://github.com/HodeTech/ForgeLM/blob/main/docs/reference/audit_event_catalog.md) altındadır.

## Tasarım gereği append-only

ForgeLM önceki log kayıtlarını asla yeniden yazmaz. Yeni olaylar sona eklenir. Zincirlenen `prev_hash` modifikasyonu tespit edilebilir kılar: N. kaydı değiştirirseniz N+1'den itibaren her kaydın `prev_hash` referansı yanlış olur.

:::warn
**Konvansiyon, zorlama değil.** Toolkit append-only yazar ve zinciri hashler ama dosya filesystem'inizde — yazma erişimi olan herkes düzenleyebilir. Gerçek tamper-evidence için log'u ayrı bir write-once depoya gönderin (S3 Object Lock, ledger DB, HSM). Bu sizin operasyonel sorumluluğunuzdur.
:::

## Bütünlük doğrulama

```shell
$ forgelm verify-audit <output_dir>/audit_log.jsonl
✓ 87 kayıt, tüm zaman damgaları monotonik
✓ tüm prev_hash zincirleri geçerli
✓ seq numaralarında boşluk yok
```

`verify-audit` zincir kırığı raporlarsa, log üretimden sonra değiştirilmiş demektir. Kanıt olarak işlem görmeden önce araştırın.

## Koşu başına

Her eğitim koşusu kendi `<output_dir>/audit_log.jsonl`'ini (top-level — `compliance/` altında değil) ve genesis-pin sidecar `<output_dir>/audit_log.jsonl.manifest.json`'ı yazar. Proje-başı global bir log dosyası **yoktur**. Koşular-arası izlenebilirlik için her koşunun output dizinini aynı upstream depoya (S3 prefix, ledger DB) gönderin ve `run_id` üzerinden korelasyon yapın.

## Konfigürasyon

`compliance.audit_log:` bloğu **yoktur**. Audit log'u açık/kapalı yapmak için bir knob değildir — her ForgeLM koşusu otomatik olarak `<output_dir>/audit_log.jsonl` yazar (ve genesis-pin sidecar `<output_dir>/audit_log.jsonl.manifest.json`). HMAC zincirlemesini etkinleştirmek için trainer'ı çalıştırmadan önce `FORGELM_AUDIT_SECRET` env var'ını set edin; ek bir YAML knob'u yoktur.

## Dış depolara yönlendirme

ForgeLM yerleşik bir log-yönlendirme katmanı **göndermez**. `compliance.audit_log.forward_to:` bloğu yoktur. Tamper-evidence için log'u operasyonel olarak yönlendirin:

```bash
# Filebeat / Fluent Bit / Vector ile JSONL'i sürekli izleyin ve S3 Object Lock'a / Splunk'a / Datadog'a gönderin
filebeat -c filebeat.yml -e
```

Veya post-run olarak yükleyin:

```bash
aws s3 cp <output_dir>/audit_log.jsonl s3://compliance-audit-logs/forgelm/<run_id>/ --no-progress
```

`forgelm verify-audit <output_dir>/audit_log.jsonl --require-hmac` ardından zincirin S3'e yüklendikten sonra hâlâ doğrulanabilir olduğunu teyit eder.

## Log'u okuma

İnsan incelemesi için:

```shell
$ jq -r '.event + "\t" + .ts' checkpoints/run/audit_log.jsonl
training.started               2026-04-29T14:01:32Z
audit.classifier_load_failed   2026-04-29T14:33:08Z
model.reverted                 2026-04-29T14:33:10Z
pipeline.completed             2026-04-29T14:33:11Z
```

Dashboard için JSONL doğal olarak Loki, OpenSearch veya herhangi bir log-aggregation aracına akar.

## Sık hatalar

:::warn
**"Bir typo'yu düzeltmek için" log'u editlemek.** Yapmayın. Kozmetik düzenlemeler bile zincir hash'ini bozar ve audit değerini düşürür. Gerçekten bilgi düzeltmek gerekirse `corrects_seq` referansıyla yeni bir `correction` olayı ekleyin.
:::

:::warn
**Log'u sadece eğitim host disklerinde tutmak.** Disk arızası = kaybedilen audit kanıtı. Her zaman dayanıklı depolamaya yönlendirin (versiyonlu + Object Lock'lu S3, ledger DB).
:::

:::tip
**Üretimde koşular arası log zincirleyin.** Bir checkpoint'i üretime terfi ettirdiğinizde önceki sürümü referans alan `model_promoted` olayı ekleyin. Denetçiler eğitimden deployment'a kesintisiz chain-of-custody görmeyi sever.
:::

## Bkz.

- [Annex IV](#/compliance/annex-iv) — audit log'a işaret eden teknik doküman.
- [Otomatik Geri Alma](#/evaluation/auto-revert) — `model.reverted` olaylarını üretir.
- [İnsan Gözetimi](#/compliance/human-oversight) — onay olaylarını üretir.
