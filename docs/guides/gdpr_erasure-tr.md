# GDPR Silinme Hakkı Rehberi (`forgelm purge`)

> **EU Genel Veri Koruma Tüzüğü Madde 17** ("unutulma hakkı") veri
> sahibine, sorumlunun kendisi hakkında tuttuğu kişisel verilerin
> silinmesini isteme hakkı verir. ForgeLM'in `forgelm purge`
> subcommand'ı, bir ForgeLM dağıtımının ürettiği veya tükettiği eğitim
> corpus'larına ve koşum-kapsamlı artefact'lara karşı bu talepleri
> karşılamak için operator-facing araçtır.

## `forgelm purge` neyi yapar (ve neyi yapmaz)

**Yapar:**

- JSONL eğitim corpus'undan tek bir satırı siler (`--row-id <id> --corpus <path>`).
- Bir koşumun staging dizinini veya compliance bundle'ını siler (`--run-id <id> --kind {staging,artefacts}`).
- Yüklü config'in `retention:` block'una karşı retention-policy ihlallerini raporlar (`--check-policy`).
- Her aksiyonu tamper-evident bir audit-event zinciriyle kaydeder — `request → completed` (veya `request → failed`) — böylece adli inceleyici tam olarak ne olduğunu yeniden inşa edebilir.
- Operatör tarafından sağlanan satır identifier'larını audit log'a girmeden önce SHA-256 ile hash'ler; böylece zincirin kendisi kişisel-veri sızıntısına dönüşmez (Madde 5(1)(c) veri minimizasyonu).

**Yapmaz:**

- Modelleri yeniden eğitmez. Bir satırı corpus'tan kaldırmak onu zaten eğitilmiş ağırlıklardan unutturmaz — silinmiş satır olmadan tam yeniden eğitim tek doğru çözüm. Tool, corpus'u tüketen herhangi bir koşum için `final_model/` mevcut olduğunda `data.erasure_warning_memorisation` yayar; böylece operatör boşluğu görür.
- Audit log'un kendisinden entry silmez. Madde 17(3)(b) audit / muhasebe kayıtlarını bir hukuki yükümlülük savunması olarak korur; tool silmeyi geçmişi yeniden yazmak yerine yeni bir event olarak kaydeder.
- Downstream tüketicilere silme bildirimi göndermez. Webhook receiver'ları, dataset mirror'ları, deploy edilmiş model endpoint'leri ve yayınlanmış checkpoint'ten türetilmiş üçüncü-taraf fine-tune'lar operatörün runtime-katmanı sorumluluğudur (Madde 17(2)). Tool yüklü config webhook block'u içerdiğinde `data.erasure_warning_external_copies` yayar; böylece audit log'u sorgulayan downstream tüketici açık hatırlatmayı görür.
- Yedekleri silmez. Operatörün `<output_dir>` sınırı dışındaki replikalar, snapshot'lar ve storage backup'lar altyapı tarafı endişelerdir.

## Üç mod

### 1. Corpus satır silme

```shell
$ forgelm purge \
    --row-id "ali@example.com" \
    --corpus train.jsonl \
    --justification "GDPR Mad.17 ticket #1234" \
    --output-dir ./outputs
```

Tool şunları yapar:

1. Per-output-dir salt'ı `<output_dir>/.forgelm_audit_salt`'ta çözer (ilk kullanımda oluşturur; mode `0600`; `FORGELM_AUDIT_SECRET[:16]` set'liyse XOR'lar). **Not:** bu XOR yalnızca **identifier hashing**'i besler — audit-chain HMAC anahtarı bağımsız olarak `SHA-256(FORGELM_AUDIT_SECRET ‖ run_id)` şeklinde türetilir (bkz. [`docs/qms/access_control.md`](../qms/access_control-tr.md) §3.4 / `forgelm/compliance.py:104-114`). İki primitif kasıtlı olarak ayrıdır.
2. Audit log'a `data.erasure_requested` yazar — **hash'lenmiş** target_id (`SHA-256(salt + value)`) ve operatör-sağlanan justification ile.
3. Eşleşen JSONL satırını `id` (veya `row_id`) field'ı ile bulur — re-order edilmiş bir dosyada sessiz yanlış-satır silmesini engellemek için satır-numarası fallback'i **reddedilir**. ForgeLM şu an bir id-doldurma yardımcısı sunmuyor (Phase 28 backlog'unda `forgelm audit --add-row-ids` flag'i bekliyor); id'siz corpus'lara sahip operatörler purge'den önce id'leri operatör-tarafı bir script ile (örn. `jq -c 'to_entries | with_entries(...)'` ya da tek-seferlik bir Python döngüsü) pre-populate etmeli.
4. Corpus'u atomic olarak yeniden yazar (kardeş bir temp dosyaya yazar + `os.replace`); operatörler ya tam silme-öncesi dosyayı ya da tam silme-sonrası dosyayı görür, asla kısmi state'i değil.
5. Uyarı koşullarını tespit eder ve eşleşen event'leri yayar (memorization, synthetic-data varlığı, dış kopyalar).
6. Disk operasyonu raise ederse `data.erasure_failed`, başarılıysa `data.erasure_completed` yazar; zincir nihai state'i yansıtır.

Çoklu-satır eşleşmeleri varsayılan olarak reddedilir (`--row-matches one`); id'yi paylaşan her satırı silmek için `--row-matches all` geç (operatör niyeti onaylar).

Disk'e dokunmadan silmeyi önizlemek + audit zincirini (dry_run=true ile) yaymak için `--dry-run` kullan.

### 2. Koşum-kapsamlı artefact silme

```shell
$ forgelm purge --run-id fg-abc123def456 --kind staging --output-dir ./outputs
$ forgelm purge --run-id fg-abc123def456 --kind artefacts --output-dir ./outputs
```

- `--kind staging` — `<output_dir>/final_model.staging.<run_id>/`'i siler (mevcutsa legacy `final_model.staging/` da).
- `--kind artefacts` — `<run_id>`'i adında embed eden `<output_dir>/compliance/*.json` dosyalarını siler.
- `--kind logs` **bilinçli olarak yok**: audit log'lar append-only Madde 17(3)(b) kayıtları; tool tarafından silinmez.

### 3. Retention policy raporu

```shell
$ forgelm purge --check-policy --config configs/run.yaml --output-dir ./outputs
```

Salt-okunur tarama. Output dizinini gezer, her artefact'ın yaşını (canonical: audit-log genesis `timestamp`'i; fallback: filesystem `mtime` + `age_source=mtime` flag'i ile) yüklü config'in `retention:` horizon'larına karşı kıyaslar ve structured violation list yayar.

**Başarılı policy raporları 0 ile çıkar** (rapor-değil-gate semantiği — design §10 Q5). Config-load başarısızlıkları `EXIT_CONFIG_ERROR` (sıfır olmayan) ile çıkar: eksik / okunamayan / Pydantic validation'ı geçemeyen explicit `--config`, "loader failed" durumunu "ihlal yok" sanmamak için `EXIT_CONFIG_ERROR` olarak yüzeye çıkar. CI gate isteyen operatörler `--output-format json` kullanır ve `jq '.violations | length'`'e pipe'lar; bu, public exit-code kontratını `0/1/2/3/4` her ForgeLM subcommand'ında tutarlı tutar.

## `retention:` config block'u

```yaml
retention:
  audit_log_retention_days: 1825          # 5 yıl (Madde 12 kayıt-tutma)
  staging_ttl_days: 7                     # `forgelm reject` üzerine aksiyon almak için bir iş haftası
  ephemeral_artefact_retention_days: 90   # üç-aylık review cadence'i
  raw_documents_retention_days: 90        # data audit yeniden çalıştırmadan önce ingestion penceresi
  enforce: log_only                       # log_only / warn_on_excess / block_on_excess
```

Herhangi bir horizon'u `0` olarak ayarlamak o artefact kind'ı için policy'yi devre dışı bırakır (sınırsız tut). `enforce`, trainer pre-flight gate'inin ihlallere nasıl tepki vereceğini kontrol eder.

## Deprecation: `evaluation.staging_ttl_days`

Wave 1 `evaluation.staging_ttl_days` field'ı (v0.5.5'te shipped) **deprecate** edildi. Yerine `retention.staging_ttl_days` kullan:

- "Set" kararı Pydantic v2'nin `model_fields_set` setine bakılarak verilir: bir field YAML'de açıkça yazıldıysa (örn. `evaluation.staging_ttl_days: 7`) Pydantic'in default'la doldurmasından bağımsız olarak "set" sayılır. Bu, "default değerle eşit" gibi heuristik tahminlerin operatörü yanıltmasını engeller.
- Sadece legacy field açıkça set'liyse `retention.staging_ttl_days`'e alias-forward edilir ve tek bir `DeprecationWarning` yayılır.
- Sadece canonical field açıkça set'liyse sessiz canonical path.
- İkisi de **aynı** değerlerle açıkça set'liyse `DeprecationWarning` yayılır (canonical block kazanır).
- İkisi de **farklı** değerlerle açıkça set'liyse config-load zamanında `ConfigError` raise edilir. Sessiz kazanan = yanlış kazanan.

Deprecate edilen field **v0.8.0**'da kaldırılır.

## Audit-event sözlüğü

`forgelm purge` ile altı yeni event ship eder (`docs/reference/audit_event_catalog.md`'de katalog'lanır):

| Event | Ne zaman | Anahtar field'lar |
|---|---|---|
| `data.erasure_requested` | Herhangi bir `forgelm purge --row-id` / `--run-id` çağrısının ilk adımı, herhangi bir silmeden önce (`--check-policy` salt-okunur; audit event yaymaz) | `target_kind` ∈ `{row, staging, artefacts}`, `target_id` (row mode'da hash'lenmiş), `salt_source` (row mode), `corpus_path` (row), `output_dir` (run), `justification`, `dry_run` |
| `data.erasure_completed` | Başarılı silme bittiğinde | Tüm `requested` field'ları + `bytes_freed`, `files_modified`, `pre_erasure_line_number` (row mode), `match_count` (row mode) |
| `data.erasure_failed` | Disk operasyonu raise etti VEYA eşleşen satır/koşum bulunamadı VEYA çoklu-satır policy belirsizliği reddetti | Tüm `requested` field'ları + `error_class`, `error_message` |
| `data.erasure_warning_memorisation` | **Yalnız row modunda.** Row erasure × corpus'u tüketen herhangi bir koşum için `final_model/` mevcut. (Run modu memorisation uyarısı emit etmez — run-scoped `staging` / `artefacts` türleri tanım gereği promote-öncesi.) | Tüm `completed` field'ları + `affected_run_ids` |
| `data.erasure_warning_synthetic_data_present` | **Yalnız row modunda.** Row erasure × `output_dir`'de `synthetic_data*.jsonl` mevcut. (Run modu bu uyarıyı yüzeylemez — synthetic veri corpus-scoped'dur, run-scoped değil.) | Tüm `completed` field'ları + `synthetic_files` |
| `data.erasure_warning_external_copies` | Yüklü config webhook block'u içeriyor | Tüm `completed` field'ları + `webhook_targets` |

## Silme sonrası zincir doğrulama

`forgelm verify-audit <output_dir>/audit_log.jsonl` herhangi sayıda erasure event'inden sonra zinciri doğrulamaya devam eder — tool yeni event'ler ekler, eski olanları yeniden yazmaz; SHA-256 hash zinciri intact kalır.

## Exit kodları

| Kod | Anlamı |
|---|---|
| 0 | Başarı veya başarılı `--check-policy` raporu (rapor-değil-gate semantiği). |
| 1 | Config hatası: bilinmeyen `--row-id`, eksik `--corpus`, mutually-exclusive flag kombinasyonu, çelişen `staging_ttl_days` değerleri veya eksik / okunamayan / Pydantic validation'ı geçemeyen `--check-policy --config <path>`. |
| 2 | Runtime hatası: I/O failure, permission denied, atomic-rename failure. |

`--check-policy` asla 3 kodu döndürmez. Başarılı bir rapor 0 ile çıkar; verilen `--config`'i yükleme başarısızlığı ise yanıltıcı bir "ihlal yok" raporuna düşmek yerine `EXIT_CONFIG_ERROR` (sıfır olmayan) ile çıkar. CI gate isteyen operatörler ihlal sayısını JSON çıktıdan kendileri hesaplar (design §10 Q5).

## Madde 15 erişim hakkı (`forgelm reverse-pii`)

GDPR'nin *diğer* veri-sahibi hakkı — "verim corpus'ta var mı?" —
için companion subcommand `forgelm reverse-pii`.  `purge` "satırımı
sil" sorusuna yanıt verirken, `reverse-pii` "kimlik bilgim hangi
satırlarda görünüyor" sorusuna yanıt verir.

```shell
# Plaintext residual scan: mask leak'leri tespit eder (operatör
# corpus'un maskelendiğine inanıyordu ama bir residual span maskeleme
# pass'ında kaçmış).
$ forgelm reverse-pii --query "ali@example.com" --type email \
    --output-dir ./outputs data/*.jsonl

# Hash-mask scan: corpus DIŞSAL bir pipeline ile maskelenmiş —
# `purge`'ün `target_id` audit-event hash'lemesinde kullandığı
# per-output-dir salt'ını paylaşan SHA256(salt + identifier)
# digest'leri corpus'a gömülmüş.  ForgeLM'in kendisi bir
# hash-replacement ingest stratejisi YOLLAMAZ; bu mod, purge'ün
# salt'ını ortak gizli olarak kullanan harici bir pipeline kuran
# operatörler içindir.
$ forgelm reverse-pii --query "ali@example.com" --type email \
    --salt-source per_dir --output-dir ./outputs data/*.jsonl
```

Audit chain `data.access_request_query`'yi identifier *salt'lı
hash'lenmiş* olarak kaydeder — `forgelm purge`'ün `target_id` field'ı
için kullandığı aynı per-output-dir salt'ı yeniden kullanılır.
Madde 15 erişim talepleri kendisi de subject'in verisini audit log'a
sızdırmamalıdır.  Salt'lı form, compliance reviewer'ın aynı subject
için Madde 17 (purge) ve Madde 15 (reverse-pii) olaylarını cleartext
görmeden ilişkilendirmesini sağlar (digest'ler eşleşir).

**Identifier tipleri** (`--type`): `literal` (varsayılan), `email`,
`phone`, `tr_id`, `us_ssn`, `iban`, `credit_card`, `custom`.
`custom` dışındaki tüm tipler query'i literal substring olarak ele
alır (regex şekil-match yapmaz — o iş audit-time detector'ın görevi,
erişim talebinin değil).  `custom` query'i Python regex olarak
yorumlar; POSIX **ana iş parçacığında** çalışırken 30s'lik per-file
SIGALRM bütçesi ReDoS hang'lerine karşı koruma sağlar.  Windows VE
POSIX worker thread'lerinde SIGALRM guard no-op olur (sinyal
handler'ları yalnızca ana iş parçacığından kurulabilir); worker
thread'den veya Windows üzerinde `forgelm reverse-pii --type custom`
çalıştıran operatörler regex'lerini kendileri doğrulamalıdır.

**Audit-dir varsayılanı**: audit chain varsayılan olarak
`<output-dir>/audit_log.jsonl`'a yazılır — `forgelm purge` ile aynı
yola, böylece bir `verify-audit` koşumu aynı subject için Madde 17
(silme) ve Madde 15 (erişim) olaylarını tek bir chain üzerinde
ilişkilendirir.  Override için `--audit-dir <writable-dir>` geçin;
explicit verilen `--audit-dir`'a yazılamıyorsa dispatcher Madde 15
forensic kaydını sessizce düşürmek yerine `EXIT_TRAINING_ERROR` ile
reddeder.

**Exit kodları:** `0` = scan tamamlandı (matches listesi boş
olabilir); `1` = config hatası (boş query, malformed regex, boş
glob); `2` = runtime hatası (mid-scan I/O failure).  JSON envelope
şeması için bkz.
[`../usermanuals/tr/reference/json-output.md`](../usermanuals/tr/reference/json-output.md).

## Bkz.

- `docs/qms/sop_data_management.md` — retention + erasure prosedürleri dahil tam data-lifecycle SOP.
- `docs/usermanuals/tr/compliance/overview.md` — buraya link veren operator-facing compliance overview.
- [`docs/design/gdpr_erasure.md`](../design/gdpr_erasure.md) — bu implementation'ın gerçekleştirdiği Phase 20 design dokümanı.
