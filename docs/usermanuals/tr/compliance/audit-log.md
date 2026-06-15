---
title: Audit Log
description: Eğitim, değerlendirme ve geri alma kararlarını append-only event log olarak tutar — Madde 12.
---

# Audit Log

EU AI Act Madde 12, yüksek-riskli AI sistemlerinin operasyonel olarak ilgili olayların log'unu tutmasını gerektirir. ForgeLM'in `audit_log.jsonl` dosyası; eğitim başlangıcı, eval kapıları, otomatik geri alma kararları ve model export'unu kapsayan, append-only ve SHA-256-anchored bir olay dizisidir.

## Format

Satır başına bir JSON nesnesi:

```jsonl
{"timestamp":"2026-04-29T14:01:32Z","run_id":"abc123","operator":"ci-runner@ml","event":"training.started","prev_hash":"genesis","_hmac":"..."}
{"timestamp":"2026-04-29T14:33:08Z","run_id":"abc123","operator":"ci-runner@ml","event":"audit.classifier_load_failed","prev_hash":"sha256:1a2b...","classifier":"meta-llama/Llama-Guard-3-8B","reason":"...","_hmac":"..."}
{"timestamp":"2026-04-29T14:33:10Z","run_id":"abc123","operator":"ci-runner@ml","event":"model.reverted","prev_hash":"sha256:3c4d...","reason":"safety","detail":"safe_ratio eşik altında","_hmac":"..."}
{"timestamp":"2026-04-29T14:33:11Z","run_id":"abc123","operator":"ci-runner@ml","event":"pipeline.completed","prev_hash":"sha256:5e6f...","success":true,"_hmac":"..."}
```

(Tam canonical olay listesi için aşağıdaki "Olay tipleri" tablosuna ve [GitHub'daki Audit Event Kataloğu](https://github.com/HodeTech/ForgeLM/blob/main/docs/reference/audit_event_catalog-tr.md)'na bakın. Eski draft'larda görünen `run_start` / `run_complete` / `data_audit_complete` / `training_epoch_complete` / `benchmark_complete` / `safety_eval_complete` / `auto_revert` adları ship olmadı — `forgelm/` içinde emit eden hiçbir call site yok.)

Her kayıtta:
- **`timestamp`** — ISO-8601 UTC zaman damgası.
- **`run_id`** — kaydı emit eden koşu.
- **`operator`** — çözümlenen operatör kimliği (`$FORGELM_OPERATOR` veya `<kullanıcı>@<host>`).
- **`event`** — olay tipi (aşağıda).
- **`prev_hash`** — önceki kaydın SHA-256'sı (tamper-evidence için zincirleme; ilk kayıt `"genesis"`).
- **`_hmac`** — satır başına HMAC etiketi, yalnızca `FORGELM_AUDIT_SECRET` set olduğunda bulunur.
- Olaya özgü alanlar.

`seq` alanı **yoktur**. Boşluk- ve silme-tespiti tamamen `prev_hash`
zincirine (ve genesis-manifest sidecar'ına) dayanır, sıra numaralarına değil.

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
[GitHub'daki Audit Event Kataloğu](https://github.com/HodeTech/ForgeLM/blob/main/docs/reference/audit_event_catalog-tr.md) altındadır.

## Tasarım gereği append-only

ForgeLM önceki log kayıtlarını asla yeniden yazmaz. Yeni olaylar sona eklenir. Zincirlenen `prev_hash` modifikasyonu tespit edilebilir kılar: N. kaydı değiştirirseniz N+1'den itibaren her kaydın `prev_hash` referansı yanlış olur.

:::warn
**Konvansiyon, zorlama değil.** Toolkit append-only yazar ve zinciri hashler ama dosya filesystem'inizde — yazma erişimi olan herkes düzenleyebilir. Gerçek tamper-evidence için log'u ayrı bir write-once depoya gönderin (S3 Object Lock, ledger DB, HSM). Bu sizin operasyonel sorumluluğunuzdur.
:::

## Bütünlük doğrulama

```shell
$ forgelm verify-audit <output_dir>/audit_log.jsonl
OK: 87 entries verified
```

`FORGELM_AUDIT_SECRET` set iken `--require-hmac` ekleyin; başarı satırı `OK: 87 entries verified (HMAC validated)` olur. Tampered veya truncate edilmiş bir log `FAIL at line N: <neden>` ile başarısız olur (`prev_hash` zincir kırığı, HMAC uyuşmazlığı veya genesis-manifest uyuşmazlığı). Kanıt olarak işlem görmeden önce araştırın.

## Koşu başına

Her eğitim koşusu kendi `<output_dir>/audit_log.jsonl`'ini (top-level — `compliance/` altında değil) ve genesis-pin sidecar `<output_dir>/audit_log.jsonl.manifest.json`'ı yazar. Proje-başı global bir log dosyası **yoktur**. Koşular-arası izlenebilirlik için her koşunun output dizinini aynı upstream depoya (S3 prefix, ledger DB) gönderin ve `run_id` üzerinden korelasyon yapın.

## Konfigürasyon

`compliance.audit_log:` bloğu **yoktur**. Audit log'u açık/kapalı yapmak için bir knob değildir — her ForgeLM koşusu otomatik olarak `<output_dir>/audit_log.jsonl` yazar (ve genesis-pin sidecar `<output_dir>/audit_log.jsonl.manifest.json`). HMAC zincirlemesini etkinleştirmek için trainer'ı çalıştırmadan önce `FORGELM_AUDIT_SECRET` env var'ını set edin; ek bir YAML knob'u yoktur.

Güçlü bir secret kullanın: bir secret manager'dan 32+ rastgele byte. Kısa, düşük-entropili bir `FORGELM_AUDIT_SECRET` kabul edilir ama (16 karakterin altında) bir weak-secret WARNING'i loglar; çünkü satır başına HMAC'in gücü secret'ın entropisiyle sınırlıdır. ForgeLM bir key-management sistemi değildir — secret'ı tüketir, üretmez veya döndürmez.

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
$ jq -r '.event + "\t" + .timestamp' checkpoints/run/audit_log.jsonl
training.started               2026-04-29T14:01:32Z
audit.classifier_load_failed   2026-04-29T14:33:08Z
model.reverted                 2026-04-29T14:33:10Z
pipeline.completed             2026-04-29T14:33:11Z
```

Dashboard için JSONL doğal olarak Loki, OpenSearch veya herhangi bir log-aggregation aracına akar.

## Sık hatalar

:::warn
**"Bir typo'yu düzeltmek için" log'u editlemek.** Yapmayın. Kozmetik düzenlemeler bile zincir hash'ini bozar ve audit değerini düşürür. Gerçekten bilgi düzeltmek gerekirse, düzeltilen kaydın koşusunu ve zaman damgasını referans alan yeni bir olay ekleyin — orijinal satırı asla yeniden yazmayın.
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
