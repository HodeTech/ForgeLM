# Audit Event Kataloğu

> **Hedef kitle:** EU AI Act Madde 12 kayıt-tutma artefaktlarını inceleyen ForgeLM operatörleri, denetçiler ve aşağı akış doğrulayıcıları.
> **Ayna:** [audit_event_catalog.md](audit_event_catalog.md)

Bu katalog, ForgeLM'in EU AI Act Madde 12 tarafından zorunlu kılınan, append-only ve hash-zincirli kayıt-tutma artefaktı olan `audit_log.jsonl`'a yazabileceği tüm event'leri sıralar. Her satır ortak bir zarf paylaşır; aşağıdaki satırlardan birini `event` alanı seçer.

## Ortak zarf

Her satır, en azından aşağıdaki alanları içeren tek bir JSON nesnesidir:

| Alan          | Tip     | Açıklama                                                                                                  |
|---------------|---------|-----------------------------------------------------------------------------------------------------------|
| `timestamp`   | string  | ISO-8601 UTC zaman damgası (`datetime.now(timezone.utc).isoformat()`).                                    |
| `run_id`      | string  | Eğitim koşusu başına stabil tanımlayıcı (`fg-<uuid12>`).                                                  |
| `operator`    | string  | İnsan-atfedilebilir kimlik. `AuditLogger` öncelik sırasıyla çözer: (1) `$FORGELM_OPERATOR` setse onu; (2) yoksa kullanıcı adı çözülebildiğinde `<getpass.getuser()>@<hostname>`; (3) yalnızca `getpass.getuser()` çağrısı başarısızsa *ve* `FORGELM_ALLOW_ANONYMOUS_OPERATOR=1` setse `anonymous@<hostname>` (aksi halde koşu yüksek sesli iptal olur). |
| `event`       | string  | Bu kataloğa ait noktalı event adı.                                                                        |
| `prev_hash`   | string  | Önceki satırın SHA-256'sı (ilk girdi için `"genesis"`). Tampering-evident hash zincirini oluşturur.       |
| `_hmac`       | string? | Sadece `FORGELM_AUDIT_SECRET` set edildiğinde mevcut. `_hmac` olmadan satırın HMAC-SHA-256'sı.            |
| _payload_     | değişir | Her satırda ayrı listelenen, event'e özel anahtarlar.                                                     |

Hash zinciri, satır diske düştükten (`flush` + `fsync`) sonra ilerler; kirli bir kapanış zinciri resume için bütün bırakır.

## Event sözlüğü

### Pipeline yaşam döngüsü

| Event                      | Ne zaman emit edilir                                                            | Payload (zarfa ek olarak)                                                          | Madde |
|----------------------------|---------------------------------------------------------------------------------|-------------------------------------------------------------------------------------|-------|
| `pipeline.initialized`     | `ForgeTrainer.__init__` config + audit logger bağlantısını bitirdi; herhangi bir model yüklemeden önce yayılır. | `model`, `trainer_type` | 12 |
| `training.started`         | Trainer fine-tuning koşusunu başlatır.                                          | _(payload yok — yalnız zarf)_                                                      | 12    |
| `training.oom_recovery`    | OOM kurtarma yolu `per_device_train_batch_size`'i yarıya indirip yeniden denedi (eğitim-arası event). | `old_batch_size`, `new_batch_size`, `new_grad_accum` | 12 / 15 |
| `benchmark.evaluation_completed` | `lm-eval-harness` yapılandırılmış benchmark suite'inin değerlendirmesini bitirdi. | `passed`, `average`, `scores`                  | 15 |
| `safety.evaluation_completed`    | Güvenlik değerlendirmesi bitti (Llama Guard / ShieldGemma koşusu).             | `passed`, `safe_ratio`, `total_count`, `safety_score`, `categories` | 15 |
| `judge.evaluation_completed`     | LLM-as-judge skorlaması bitti.                                                  | `passed`, `average_score`                       | 15 |
| `evaluation.loss_gate_completed` | Kayıp/eval-loss otomatik geri-alma kapısı yapılandırılmış eşiklere göre karar verdi (geçti veya kaldı). | `passed`, `eval_loss`, `max_acceptable_loss`, `baseline_loss` | 15 |
| `pipeline.completed`       | Uçtan uca CLI koşusu (eğitim + değerlendirme + dışa aktarma) 0 koduyla biter. Çok-aşamalı pipeline orkestratörü (`_finalise_pipeline`) tarafından da **yapısal olarak farklı bir payload** ile yayılır — bir parser `success` alanının her zaman mevcut olduğunu varsaymamalı. | tek-aşamalı: `success`, `metrics_summary`; çok-aşamalı orkestratör: `pipeline_run_id`, `final_status`, `stopped_at` (`success` anahtarı yok) | 12    |
| `pipeline.failed`          | Pipeline tamamlanmadan bir hata ile iptal olur.                                 | `error`                                                                            | 12    |
| `pipeline.started`         | Çok-aşamalı pipeline orchestrator yeni bir koşu başlattı (`--resume-from` değil). | `pipeline_run_id`, `config_hash`, `stage_count`, `stage_names`                   | 12    |
| `pipeline.force_resume`    | `--resume-from`, saklanan config-hash uyuşmazlığını `--force-resume` ayarlı olduğu için geçti. | `pipeline_run_id`, `old_config_hash`, `new_config_hash`            | 12    |
| `pipeline.stage_started`   | Bir pipeline aşaması çalışmaya başladı (aşama-config birleştirme + doğrulama sonrası). | `pipeline_run_id`, `stage_name`, `stage_index`, `input_model`, `input_source` | 12    |
| `pipeline.stage_completed` | Bir pipeline aşaması bitti — başarıda `gate_decision=passed`, revert-dışı başarısızlıkta `failed`. | `pipeline_run_id`, `stage_name`, `gate_decision`, `metrics` (yalnız başarıda), `auto_revert_triggered` (yalnız başarısızlık yolunda) | 12 |
| `pipeline.resume_refused`  | `--resume-from`, önceki bir aşama hâlâ insan onayı beklediği için reddedildi (Article 14 gate henüz geçilmemiş). | `pipeline_run_id`, `requested_stage`, `blocking_stage`, `blocking_status` | 12, 14 |
| `pipeline.stage_gated`     | Bir aşama Article 14 insan-onay gate'inde durdu (exit 4); pipeline operatör eylemi bekleyerek durur. | `pipeline_run_id`, `stage_name`, `gate_decision` (`approval_pending`), `staging_path` | 12, 14 |
| `pipeline.stage_reverted`  | Bir aşamanın post-train gate'i modeli auto-revert etti (`auto_revert_triggered=true`); zincir durur. | `pipeline_run_id`, `stage_name`, `gate_decision` (`failed`), `auto_revert_triggered` | 12, 15 |

### Madde 14 — İnsan Gözetimi

| Event                        | Ne zaman emit edilir                                                                                             | Payload                                              | Madde |
|------------------------------|-------------------------------------------------------------------------------------------------------------------|------------------------------------------------------|-------|
| `human_approval.required`    | `requires_human_approval: true` işaretli bir kapı pipeline'ı duraklatıp operatör kararını bekler.                | `gate`, `reason`, `metrics`, `staging_path`, `run_id`, `config_hash`                    | 14    |
| `human_approval.granted`     | Operatör duraklatılan kapıyı `forgelm approve` ile onayladı.                                                     | `gate`, `approver`, `comment`, `run_id`, `promote_strategy`              | 14    |
| `human_approval.rejected`    | Operatör duraklatılan kapıyı `forgelm reject` ile reddetti.                                                      | `gate`, `approver`, `comment`, `run_id`, `staging_path`                  | 14    |

### Madde 15 — Model Bütünlüğü (auto-revert + güvenlik)

| Event                          | Ne zaman emit edilir                                                                                              | Payload                                                       | Madde |
|--------------------------------|--------------------------------------------------------------------------------------------------------------------|---------------------------------------------------------------|-------|
| `model.reverted`               | Auto-revert kalite regresyonu sonrası önceki bir checkpoint'i geri yükledi. _(Faz 8 — webhook bağlantılı.)_       | `reason` (tetikleyen kapı: `benchmark` / `safety` / `judge` / vs.), `detail` (kapıdan gelen insan-okunabilir hata sebebi) | 15    |
| `model.integrity_verified`     | Eğitim sonrası nihai-model bütünlük manifesto'su (`model_integrity.json`) yazıldı ve başarıyla yeniden hash'lendi. | `artifacts` (yeniden-hash'lenen dosya sayısı)                  | 15    |
| `audit.classifier_load_failed` | Güvenlik sınıflandırıcısı (örn. Llama Guard) yüklenemedi. Koşu yine `passed=False` kaydeder.                       | `classifier`, `reason`                                        | 15    |

### Madde 11 + Ek IV — Uyumluluk artefaktları

| Event                            | Ne zaman emit edilir                                                          | Payload                                          | Madde         |
|----------------------------------|-------------------------------------------------------------------------------|--------------------------------------------------|---------------|
| `compliance.governance_exported` | Madde 10 veri yönetişim raporu diske yazıldı.                                 | `output_path`, `dataset_count`                   | 10            |
| `compliance.governance_section_missing` | Yönetişim raporu yazıldı ancak Madde 10 veri-kalitesi bölümü eksikti (`data_audit_report.json` yok). | `section`, `expected_path`              | 10            |
| `compliance.governance_failed`   | Yönetişim raporu üretimi iptal edildi (örn. şema uyumsuzluğu).                | `reason`                                          | 10            |
| `compliance.artifacts_exported`  | Ek IV teknik dokümantasyon paketi (manifest, model card, audit zip) yazıldı.  | `output_dir`, `files`, `governance_ok`           | 11, Ek IV     |
| `compliance.artifacts_export_failed` | Ek IV / Madde 11 manifest export'u başarısız oldu veya yarım kaldı (disk dolu, SIGKILL, serileştirme hatası). | `reason`                                          | 11, Ek IV     |

### Madde 17 — GDPR Silinme Hakkı (Phase 21 — `forgelm purge`)

| Event                                      | Ne zaman yayılır                                                                                                  | Payload                                                                                                  | Madde |
|--------------------------------------------|-------------------------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------|-------|
| `data.erasure_requested`                   | Herhangi bir `forgelm purge --row-id` / `--run-id` çağrısının ilk adımı, herhangi bir silmeden ÖNCE.  `--check-policy` salt-okunur; event yaymaz. | `target_kind` ∈ `{row, staging, artefacts}`, `target_id` (row mode'da hash'lenmiş), `salt_source` (row mode), `corpus_path` (row), `output_dir` (run), `justification`, `dry_run` | 17    |
| `data.erasure_completed`                   | Başarılı silme tamamlandı.                                                                                        | Tüm `requested` field'ları + `bytes_freed`, `files_modified`, `pre_erasure_line_number` (row mode), `match_count` (row mode) | 17    |
| `data.erasure_failed`                      | Disk operasyonu raise etti VEYA eşleşen satır/koşum bulunamadı VEYA çoklu-satır policy belirsizliği reddetti.    | Tüm `requested` field'ları + `error_class`, `error_message`                                              | 17    |
| `data.erasure_warning_memorisation`        | Row erasure × bu corpus'u tüketen herhangi bir koşum için `final_model/` mevcut.                                  | Tüm `completed` field'ları + `affected_run_ids`                                                          | 17    |
| `data.erasure_warning_synthetic_data_present` | Row erasure × `output_dir`'de `synthetic_data*.jsonl` mevcut.                                                  | Tüm `completed` field'ları + `synthetic_files`                                                           | 17    |
| `data.erasure_warning_external_copies`     | Yüklü config boş-olmayan `webhook` block'u içeriyor; downstream tüketiciler bildirim almış olabilir.              | Tüm `completed` field'ları + `webhook_targets` (redact'li URL'ler)                                       | 17    |

### Madde 15 — GDPR Erişim Hakkı (Phase 38 — `forgelm reverse-pii`)

| Event                          | Ne zaman yayılır                                                                                                          | Payload                                                                                                                                                | Madde |
|--------------------------------|---------------------------------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------|-------|
| `data.access_request_query`    | Her `forgelm reverse-pii` çağrısında, scan tamamlandıktan sonra (veya mid-scan I/O hatası sonrası — `error_*` field'larıyla). | `query_hash` (raw identifier'ın salt'lı SHA-256'sı — asla raw; purge'ün per-output-dir salt'ını yeniden kullanır), `identifier_type` ∈ `{literal, email, phone, tr_id, us_ssn, iban, credit_card, custom}`, `scan_mode` ∈ `{plaintext, hash}`, `salt_source` ∈ `{plaintext, per_dir, env_var}`, `files_scanned` (path'ler), `match_count`, opsiyonel `error_class`/`error_message` | GDPR Md. 15 |

### Air-gap pre-cache (Phase 35 — `forgelm cache-models` / `cache-tasks`)

| Event                                | Ne zaman yayılır                                                              | Payload                                                                | Madde |
|--------------------------------------|-------------------------------------------------------------------------------|------------------------------------------------------------------------|-------|
| `cache.populate_models_requested`    | `forgelm cache-models` çağrısı başlar.                                        | `models`, `cache_dir`, `safety_classifier`                             | 12    |
| `cache.populate_models_completed`    | Her model başarıyla indirildi.                                                | Tüm `requested` field'ları + `total_size_bytes`, `count`               | 12    |
| `cache.populate_models_failed`       | Bir veya daha fazla model indirme başarısız (transport, disk-full, HF auth). | Tüm `requested` field'ları + `models_completed`, `error_class`, `error_message` | 12 |
| `cache.populate_tasks_requested`     | `forgelm cache-tasks` çağrısı başlar.                                         | `tasks`, `cache_dir`                                                   | 12    |
| `cache.populate_tasks_completed`     | Her lm-eval task dataset'i başarıyla hazırlandı.                              | Tüm `requested` field'ları + `count`                                   | 12    |
| `cache.populate_tasks_failed`        | Bilinmeyen task adı VEYA dataset download başarısız.                          | Tüm `requested` field'ları + `tasks_completed`, `error_class`, `error_message` | 12 |

### CLI / göç

_Ayrılmış ad alanı — `cli`, tanınan bir event-namespace önekidir (aşağıdaki "Yeni bir event eklemek" bölümüne bakın), ancak `forgelm/` içindeki hiçbir kod şu anda bir `cli.*` event'i emit etmiyor. Bu bölüme henüz satır eklenmedi._

### Audit-sistem event'leri (meta)

| Event                          | Ne zaman emit edilir                                                                                      | Payload                              | Madde |
|--------------------------------|------------------------------------------------------------------------------------------------------------|--------------------------------------|-------|
| `audit.classifier_load_failed` | _(Yukarıdaki Madde 15 satırına bakın.)_                                                                    | `classifier`, `reason`               | 15    |

## Yeni bir event eklemek

1. Mevcut isim alanlarını (`training.*`, `compliance.*`, `audit.*`, `human_approval.*`, `model.*`, `cli.*`) takip eden noktalı bir ad seçin.
2. Yukarıdaki tabloya, payload anahtarları ve desteklediği Madde dahil olmak üzere bir satır ekleyin.
3. Aynı satırı İngilizce kardeş katalog `audit_event_catalog.md`'ye de ekleyin (EN ↔ TR senkron kalmalı).
4. `AuditLogger.log_event(event, **payload)` üzerinden emit edin. `audit_log.jsonl`'a doğrudan `json.dump` çağırmayın; hash zinciri kanonik yazıcıya bağımlıdır.

## Bu kataloğun kapsamadığı log'lar

Ağaçta denetim izine benzeyen ama Madde 12 zincirinin parçası **olmayan** bir JSONL log'u daha var. Kimse yeniden keşfetmek zorunda kalmasın diye buraya kaydediliyor.

`forgelm quickstart`, [`forgelm/quickstart.py`](../../forgelm/quickstart.py) içindeki tek bir çağrı noktasından `<config-dizini>/quickstart_audit.jsonl` dosyasına tam olarak bir kayıt yazar: `quickstart.model_selection`. Bu bilinçli olarak bir **kolaylık log'udur**: zincirsizdir (`_hmac` yok, önceki-kayıt hash'i yok), koşum bağlamı ve çözülmüş model revizyonu taşımaz, yazımları best-effort'tur ve hata durumunda loglanıp yutulur. Kaydettiği şey için doğru ağırlık budur — hangi template ve VRAM değerinin hangi model seçimini ürettiği; henüz iliştirilecek bir eğitim koşumu yokken. Hiçbir şeye dayanmayan ikinci bir hash-zincirli iz, dürüst tek bir zincir artı açıkça etiketlenmiş bir kolaylık log'undan *daha zayıf* uyumluluk kanıtı olurdu; bu yüzden "yükseltilmemelidir". Madde 12 artefaktı, yukarıda anlatılan `compliance.AuditLogger`'ın `audit_log.jsonl` dosyası olmaya devam eder.

Oraya event ekleyecek biri için iki sonuç:

- **Katalog guard'ı o dosyayı göremez.** [`tools/check_audit_event_catalog.py`](../../tools/check_audit_event_catalog.py) onu birbirinden bağımsız iki nedenle kaçırır: `quickstart` onun `_EVENT_NAMESPACES` listesinde yoktur ve anahtar, emisyon regex'inin eşleştirdiği `event` yerine `event_type`'tır. Dosyayı hiç incelememiş olarak yeşil rapor verir. Geçen bir katalog guard'ını `quickstart.py` için kapsam saymayın.
- **Sınırı tutan şey bir testtir.** `tests/test_quickstart_compat.py::TestAuditLog`, tam olarak o `event_type` ile tam olarak bir event olduğunu doğrular. Yeni bir event orada düşer — bu da durup onun asıl zincire ait olup olmadığına karar vermek için amaçlanan uyarıdır.

## Tampering-evidence özeti

| Mekanizma                | Şuna karşı koruma sağlar                                                          | Her zaman açık mı?                                |
|--------------------------|-----------------------------------------------------------------------------------|---------------------------------------------------|
| SHA-256 hash zinciri     | Tek-satır düzenlemeler, silmeler, sıralama değişiklikleri.                        | Evet.                                             |
| Genesis manifest sidecar | Tüm log'un "genesis"'e geri kesilmesi.                                            | Evet (ilk event'te bir kez yazılır).              |
| `flock(LOCK_EX)`         | Aynı dizini paylaşan eşzamanlı trainer'lardan iç içe yazımlar.                    | Evet (Unix); Windows'ta no-op.                    |
| `flush` + `fsync`        | Buffer yazımı ile zincir ilerleme arasında güç-kesme / kernel-panic kaybı.        | Evet.                                             |
| Satır başına HMAC-SHA-256| Log yeniden yazımı sonrası sahte yeniden imzalama.                                | Sadece `FORGELM_AUDIT_SECRET` set olduğunda.      |

## Webhook olayları

Webhook payload'ları (Slack / Teams / jenerik HTTP) operatör bildirimlerine kapsamlanmış ayrı bir sözlüktür, regülasyon kaydı değil. Webhook olayları `audit_log.jsonl`'a **eklenmez**; yan-kanal bildirim bus'ı üzerinde gider.

> **Kanonik referans:** [`webhook_schema-tr.md`](webhook_schema-tr.md), alıcıya dönük eksiksiz sözleşmedir — tam payload biçimleri, tipler, kararlılık garantileri, dışa çıkış ek-alan allowlist'i ve maskeleme kuralları. Aşağıdaki tablo, bir denetçinin ihtiyaç duyduğu webhook ↔ denetim günlüğü **korelasyonuna** kapsamlanmıştır; bir alıcı yazarı bunun yerine oradan başlamalıdır. Olay eklemenin katkıcıya dönük kuralları [logging-observability.md](../standards/logging-observability.md)'dadır (İngilizce).

Bu sekiz olay, webhook alıcılarının `WebhookNotifier`'dan
beklemesi gereken **tek** olaylardır: beş tek-aşamalı yaşam döngüsü
olayı ve çok-aşamalı orkestratörün bunların yanı sıra emit ettiği
üç-olaylı `pipeline.*` ailesi. Her biri, karşılık gelen bir
denetim günlüğü olayını yansıtır; böylece aşağı akıştaki bir operatör
webhook ping → denetim girdisi korelasyonunu `run_name` + zaman
damgasıyla kurabilir. Uygulama: `forgelm/webhook.py`.

| Webhook `event` | Denetim günlüğü karşılığı | Tetikleyici | Kapı (gate) | Zorunlu payload alanları |
|---|---|---|---|---|
| `training.start` | `training.started` | `train()` çağrıldı, model yüklenmeden önce. | `webhook.notify_on_start` | `run_name`, `status="started"` |
| `training.success` | `pipeline.completed` | Koşu revert veya bekleyen onay olmadan tamamlandı. `evaluation.auto_revert: true` ile tüm kapılar geçildi; varsayılan `auto_revert: false` ile bir kapı geçemediği halde yalnızca kaydedildiğinde de tetiklenir (model yine terfi eder). | `webhook.notify_on_success` | `run_name`, `status="succeeded"`, `metrics` |
| `training.failure` | `pipeline.failed` | Eğitim sürecinin kendisi hata fırlattı (OOM, veri seti hatası, yakalanmayan istisna). | `webhook.notify_on_failure` | `run_name`, `status="failed"`, `reason` (maskelenmiş, ≤2048 karakter) |
| `training.reverted` | `model.reverted` | Eğitim sonrası bir kapı (değerlendirme, güvenlik, hakem, benchmark) çalışmayı reddetti ve `_revert_model` adaptörleri sildi. | `webhook.notify_on_failure` | `run_name`, `status="reverted"`, `reason` (maskelenmiş, ≤2048 karakter) |
| `approval.required` | `human_approval.required` | Çalışma başarılı oldu, `evaluation.require_human_approval=true`, model insan incelemesi için staging'de (EU AI Act Madde 14). | `webhook.notify_on_success` | `run_name`, `status="awaiting_approval"`, `model_path` |
| `pipeline.started` | `pipeline.started` | Çok-aşamalı bir pipeline koşusu başlar, herhangi bir aşama çalışmadan önce. | `webhook.notify_on_start` | `run_name`, `status="started"`, `stage_count` |
| `pipeline.completed` | `pipeline.completed` | Çok-aşamalı bir pipeline koşusu terminal durumuna ulaşır. Denetim olayıyla aynı adı paylaşır (bilinen wire/audit çakışması; payload alan-kümesiyle korele edin). | `webhook.notify_on_success` / `webhook.notify_on_failure` | `run_name`, `status`, `final_status`, `stopped_at` |
| `pipeline.stage_reverted` | `pipeline.stage_reverted` | Bir pipeline aşaması auto-revert olur, aşağı akış aşamaları skip işaretlenmeden önce. | `webhook.notify_on_failure` | `run_name`, `status="reverted"`, `stage_name`, `reason` (maskelenmiş, ≤2048 karakter) |

### Bu yaşam döngüsü durumlarından ikisinin neden ayrıldığı

- **`training.failure` vs `training.reverted`** — dashboard'ların
  "trainer çöktü" ile "trainer başarılı oldu fakat kalite / güvenlik /
  hakem kapısı reddetti" durumlarını ayırt etmesi gerekir. Her ikisi
  de operasyonel olarak eyleme dönüşürdür, ancak farklı runbook'lar
  gerektirir. Faz 8, bir Slack kanalının iki vakayı farklı renk
  kodlayabilmesi için (`#ff0000` ve `#ff9900`) tam da bu nedenle
  `notify_reverted`'i tanıttı.
- **`approval.required`** — çalışma başarılı olduktan *sonra*, fakat
  operatör dağıtımı onaylamadan *önce* yayılır. Bu bir başarısızlık
  değil; bir duraklamadır. `training.failure` üzerinde otomatik
  çağrı yapan alıcılar `approval.required` üzerinde çağrı yapmamalı.

### Payload şeması

Her webhook olayı aynı zarfı taşır:

```json
{
  "event": "training.start | training.success | training.failure | training.reverted | approval.required | pipeline.started | pipeline.completed | pipeline.stage_reverted",
  "run_name": "<dize>",
  "status": "started | succeeded | failed | reverted | awaiting_approval | completed | stopped_at_stage",
  "metrics": {"<isim>": <sayı>, ...},
  "reason": "<maskelenmiş dize ya da null>",
  "model_path": "<dosya sistemi yolu ya da null>",
  "attachments": [{"title": "...", "text": "...", "color": "..."}]
}
```

`metrics`, `reason` ve `model_path` her zaman şemada bulunur; yalnızca
ihtiyaç duyan olaylarda doldurulur. `attachments`, Slack uyumlu blok'tur
— diğer alıcılar görmezden gelebilir.

### Güvenlik garantileri

1. **Yalnızca `reason` değil, her serbest metin alanı maskelenir.**
   Maskeleme, serileştirmeden hemen önce tam olarak birleştirilmiş
   payload'a bir kez uygulanır; böylece `run_name`, `reason`,
   `model_path`, olaya özgü her dize alanı ve attachment `title` / `text`
   `forgelm.data_audit.mask_secrets`'ten geçer. AWS / GitHub / Slack /
   OpenAI / Google / JWT / özel-anahtar blokları / Azure storage dizeleri
   süreçten dışarı çıkmaz. `event`, `status` ve attachment `color` muaftır
   ve bayt-bayt aynıdır — alıcıların üzerinde yönlendirme yaptığı kapalı
   kod literali kümeleri. `data_audit` ithal edilemezse, her serbest metin
   alanı ham gönderilmek yerine
   `"[REDACTED — secrets masker unavailable]"` ile değiştirilir.
2. **Sebepler 2048 karaktere kırpılır.** Bundan uzun stack trace'ler
   `"… (truncated)"` ile kesilir.
3. **Model ağırlıkları yok.** `approval.required` yalnızca staging
   dosya sistemi yolunu taşır. Ağırlıklar diskte kalır; o dizini zaten
   operatör kontrol eder.
4. **Webhook URL'si sızdırılmaz.** URL'ler günlüklerde `scheme://host`'a
   maskelenir (userinfo, path ve query tamamen atılır — bkz. `_mask_netloc`,
   [`forgelm/_http.py`](../../forgelm/_http.py)) ve 2xx olmayan yanıt gövdesi
   bastırılır.
5. **SSRF koruması.** `webhook.allow_private_destinations=true`
   ayarlanmadığı sürece özel / loopback / link-local hedefler
   reddedilir.

### Saklama rehberi

Webhook payload'ları **geçicidir**. Denetim kaydı değildir. Uzun süreli
geçmişe ihtiyaç duyan alıcılar, webhook trafiğini arşivlemek yerine
denetim JSONL dosyasının (`<output_dir>/audit_log.jsonl`) anlık
görüntüsünü almalıdır; çünkü denetim günlüğü yalnız-eklenir
hash-zincirli kayıttır ve webhook akışı en-iyi-çabadır (best-effort).
