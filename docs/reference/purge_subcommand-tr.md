# `forgelm purge` — Subcommand Referansı

> **Hedef kitle:** Eğitim corpus'larına ve koşum-kapsamlı artefact'lara karşı GDPR Madde 17 silme taleplerini karşılayan ForgeLM operatörleri ve ortaya çıkan `data.erasure_*` audit zincirini doğrulayan denetçiler.
> **Ayna:** [purge_subcommand.md](purge_subcommand.md)

`forgelm purge`, ForgeLM eğitim corpus'ları ve koşum artefact'ları için **GDPR Madde 17 silinme hakkı**'nın operatör-yüzlü uygulamasıdır (Phase 21). Bir satırı, bir koşumun staging dizinini veya bir koşumun compliance bundle'ını atomik olarak siler ve her adımı tamper-evident `audit_log.jsonl` zincirine kaydeder.

Deployer akışı (DSAR ticket → CLI çağrısı → doğrulama) için bkz. [`../guides/gdpr_erasure.md`](../guides/gdpr_erasure-tr.md). Bu sayfa flag-başına, event-başına referanstır.

## Synopsis

```text
forgelm purge --row-id ID --corpus PATH [--row-matches {one,all}]
              [--output-dir DIR] [--justification TEXT] [--dry-run]
              [--output-format {text,json}]

forgelm purge --run-id RUN_ID --kind {staging,artefacts}
              --output-dir DIR [--justification TEXT] [--dry-run]
              [--output-format {text,json}]

forgelm purge --check-policy --config PATH [--output-dir DIR]
              [--output-format {text,json}]
```

Üç mod karşılıklı dışlayıcıdır (`argparse` tek-mod grup zorunluluğunu uygular).

## Modlar

### Corpus-satır silme (`--row-id`)

JSONL eğitim corpus'undan `id` (veya `row_id`) field'ı ile tanımlanan tek bir satırı siler. `forgelm.cli.subcommands._purge._run_purge_row_id` içinde uygulanır.

| Flag | Zorunlu | Açıklama |
|---|---|---|
| `--row-id ID` | evet | Silinecek identifier. Audit emisyonundan önce `forgelm.cli.subcommands._purge._hash_target_id` üzerinden hash'lenir. |
| `--corpus PATH` | evet | Tek bir JSONL dosyası. Dizin modu reddedilir — operatörler kendi script'lerinde döngü kurar. |
| `--row-matches {one,all}` | hayır (varsayılan `one`) | `one` >=2 eşleşmede reddeder; `all` her eşleşmeyi siler (operatör niyeti onaylar). |
| `--output-dir DIR` | hayır | `--corpus`'un parent'ına default'lar. Per-output-dir salt `<output_dir>/.forgelm_audit_salt`'ta `target_id` hash'leme için okunur. Implicit fallback, `forgelm reverse-pii` ile cross-tool korelasyon için operatörün `--output-dir`'i sabitleyebilmesi adına çözülen dizini adlandıran bir WARNING yayar. |
| `--justification TEXT` | hayır | Operatör-sağlanan, her erasure event'ına yazılan sebep. Dahili ticket id'nizi referans verin; subject identifier yapıştırmayın. |
| `--dry-run` | hayır | Silmeyi önizler + audit zincirini (`dry_run=true` ile) yayar; disk'e dokunmaz. |

**Atomik yazma sözleşmesi.** Corpus, kardeş bir temp dosya + `os.replace` ile yeniden yazılır; kesilen bir purge ya tam silme-öncesi dosyayı ya da tam silme-sonrası dosyayı bırakır — asla kısmi state'i değil.

**Satır-numarası fallback'i reddedilir** (design §4.2). ForgeLM şu an bir id-doldurma yardımcısı sunmuyor (`forgelm audit --add-row-ids` Phase 28 backlog'unda); id'siz corpus'lara sahip operatörler `forgelm purge --row-id` çalıştırmadan önce id'leri operatör-tarafı bir script ile pre-populate etmelidir.

### Koşum-kapsamlı artefact silme (`--run-id` + `--kind`)

| Kind | Hedef |
|---|---|
| `staging` | `<output_dir>/final_model.staging.<run_id>/` (varsa legacy `final_model.staging/` da). |
| `artefacts` | Adı `<run_id>` embed eden `<output_dir>/compliance/*.json` dosyaları. |

`--kind logs` **kasıtlı olarak yok**: audit log'lar append-only Madde 17(3)(b) kayıtlarıdır ve tool tarafından silinmez.

### Retention-policy raporu (`--check-policy`)

Salt-okunur tarama. `<output_dir>` üzerinde dolaşır, her artefact'ın yaşını (kanonik: audit-log genesis `timestamp`'i; fallback: dosya sistemi `mtime`'ı, `age_source=mtime` flag'iyle) yüklü config'in `retention:` horizon'larıyla karşılaştırır ve yapısal bir ihlal listesi yayar.

**Başarılı policy raporu her zaman 0 ile çıkar** (report-not-gate semantik, design §10 Q5). Config-load başarısızlıkları `EXIT_CONFIG_ERROR` (1) ile çıkar — eksik, okunamaz veya Pydantic doğrulamasından geçemeyen explicit `--config` non-zero olarak yüzeyselleşir; böylece operatör "loader başarısız" durumunu "ihlal yok" sanmaz. CI gate kuran operatörler `--output-format json` kullanıp `jq '.violations | length'`'e pipe eder.

## Salt çözümlemesi

Per-output-dir salt `<output_dir>/.forgelm_audit_salt`'ta ilk kullanımda oluşturulur (mode `0600`, atomik O_EXCL yazma) — `forgelm.cli.subcommands._purge._read_persistent_salt` tarafından. `forgelm.cli.subcommands._purge._resolve_salt` `(salt_bytes, salt_source)` döner:

- `salt_source = "per_dir"` — `FORGELM_AUDIT_SECRET` set değil; persistent salt verbatim kullanılır.
- `salt_source = "env_var"` — `FORGELM_AUDIT_SECRET` set; ilk 16 byte persistent salt ile XOR'lanır.

**Bu XOR yalnızca identifier hashing'i besler.** Audit-chain HMAC anahtarı bağımsız olarak `forgelm.compliance.AuditLogger.__init__` içinde `SHA-256(FORGELM_AUDIT_SECRET ‖ run_id)` şeklinde türetilir. İki primitif kasıtlı olarak ayrıdır; `FORGELM_AUDIT_SECRET` rotasyonu her ikisini de rotate eder, ama birini incelemek diğerini ifşa etmez.

## `forgelm reverse-pii` ile cross-tool digest korelasyonu

`forgelm purge --row-id <value>` ve `forgelm reverse-pii --query <value> --salt-source per_dir` **aynı** `<output_dir>`'e (yani aynı `.forgelm_audit_salt`'ı tüketerek) çalıştırıldığında, `data.erasure_*` event'lerindeki `target_id` ile `data.access_request_query` event'indeki `query_hash` byte-byte aynıdır. Compliance reviewer böylece aynı veri sahibi için Madde 17 silme ve Madde 15 erişim taleplerini cleartext identifier'ı asla görmeden korele edebilir. `tests/test_reverse_pii.py::test_purge_target_id_matches_reverse_pii_query_hash_on_same_output_dir` ile pin'lendi.

## Yayılan audit event'leri

Altı event de [`audit_event_catalog.md`](audit_event_catalog-tr.md)'deki ortak zarfı taşır. Katalog satırları kolaylık için burada da listelenir.

| Event | Ne zaman yayılır | Anahtar payload |
|---|---|---|
| `data.erasure_requested` | Herhangi bir `--row-id` / `--run-id` çağrısının ilk adımı, herhangi bir silmeden ÖNCE. `--check-policy` salt-okunurdur ve event yaymaz. | `target_kind` ∈ `{row, staging, artefacts}`, `target_id` (row mode'da hash'li), `salt_source` (row mode), `corpus_path` (row), `output_dir` (run), `justification`, `dry_run` |
| `data.erasure_completed` | Başarılı silme tamamlandı. | Tüm `requested` field'ları + `bytes_freed`, `files_modified`, `pre_erasure_line_number` (row mode), `match_count` (row mode) |
| `data.erasure_failed` | Disk operasyonu raise etti VEYA eşleşen satır/koşum yok VEYA çoklu-satır policy belirsizliği reddetti. | Tüm `requested` field'ları + `error_class`, `error_message` |
| `data.erasure_warning_memorisation` | Row erasure × bu corpus'u tüketen herhangi bir koşum için `final_model/` mevcut. | Tüm `completed` field'ları + `affected_run_ids` |
| `data.erasure_warning_synthetic_data_present` | Row erasure × `output_dir`'de `synthetic_data*.jsonl` mevcut. | Tüm `completed` field'ları + `synthetic_files` |
| `data.erasure_warning_external_copies` | Yüklü config boş-olmayan `webhook` block'u içeriyor; downstream tüketiciler bildirim almış olabilir. | Tüm `completed` field'ları + `webhook_targets` (redact'li URL'ler) |

`forgelm verify-audit <output_dir>/audit_log.jsonl` herhangi bir sayıda erasure event'i sonrası zinciri doğrulamaya devam eder — tool yeni event'ler ekler, eskileri yeniden yazmaz.

## Exit kodları

| Kod | Anlamı |
|---|---|
| 0 | Başarı veya başarılı `--check-policy` raporu (report-not-gate semantik). |
| 1 | Config hatası: bilinmeyen `--row-id`, eksik `--corpus`, karşılıklı dışlayıcı flag kombinasyonu, çakışan `staging_ttl_days` değerleri veya eksik / okunamaz / Pydantic doğrulamasından geçemeyen `--check-policy --config <path>`. |
| 2 | Runtime hatası: I/O başarısızlığı, izin reddedildi, atomic-rename başarısızlığı. |

`--check-policy` asla 3 veya 4 dönmez. Kod 3 (`EXIT_EVAL_FAILURE`) ve 4 (`EXIT_AWAITING_APPROVAL`) eğitim pipeline'ı için ayrılmıştır ve bu subcommand'ın yüzeyinin parçası değildir.

## JSON çıktı zarfı

`--output-format json` ile her çağrı stdout'a tam olarak bir JSON nesnesi yazdırır. Şekil moda göre değişir — toplam üç zarf.

**Row modu (başarı)** — `_perform_row_erasure_and_audit`'den yayılır:

```json
{
  "mode": "row",
  "dry_run": false,
  "salt_source": "per_dir",
  "corpus_path": "/abs/path/data/train.jsonl",
  "matches": 1,
  "first_line": 142,
  "bytes_freed": 482,
  "warnings": []
}
```

`--dry-run` çağrılarında aynı zarf `dry_run: true` ile ve `bytes_freed` olmadan görünür (rewrite hiç olmaz).

**Run modu (başarı)** — `_run_purge_run_id`'den yayılır:

```json
{
  "mode": "run",
  "kind": "staging",
  "dry_run": false,
  "run_id": "fg-abc123def456",
  "deleted": ["/abs/path/outputs/run42/final_model.staging.fg-abc123def456"],
  "bytes_freed": 102400000
}
```

`deleted` gerçekten silinen mutlak yolların **listesidir** (ya da `--dry-run`'da `would_delete` anahtarı aynı şekli taşır ve `deleted` yoktur).

**`--check-policy` modu** — `_run_purge_check_policy`'ten yayılır:

```json
{
  "success": true,
  "violations": [
    {
      "artefact_kind": "ephemeral_artefact",
      "path": "/abs/path/outputs/run42/compliance/data_audit_report.json",
      "age_days": 121.4,
      "horizon_days": 90,
      "age_source": "audit_genesis"
    }
  ],
  "count": 1
}
```

`age_source` ∈ `{audit_genesis, mtime}` — `audit_genesis` kanonik yaş (koşumun ilk audit event timestamp'i); `mtime` operatörün farkındalığı için işaretlenmiş filesystem fallback'i.

**Hata zarfı (her mod)** — `_output_error_and_exit`:

```json
{
  "success": false,
  "error": "Row id 'ali@example.com' not found in 'data/train.jsonl'."
}
```

Asimetri notu: row/run başarı zarfları açık `success` field'ı **taşımaz** (`mode` ayraç başarı sinyalidir); `--check-policy` ve hata zarfı taşır. CI gate'leri `mode` anahtarına (başarı yolu) ya da `success: false`'a (hata yolu) dallanmalı; `success: true`'nin varlığı/yokluğuna değil.

## Bkz.

- [`../guides/gdpr_erasure.md`](../guides/gdpr_erasure-tr.md) — deployer akışı (DSAR ticket → CLI → zincir doğrulama).
- [`reverse_pii_subcommand.md`](reverse_pii_subcommand-tr.md) — kardeş Madde 15 erişim hakkı aracı.
- [`audit_event_catalog.md`](audit_event_catalog-tr.md) — zarf spec'iyle birlikte tam event sözlüğü.
- [`../qms/access_control.md`](../qms/access_control-tr.md) §3.4 — operatör kimliği sözleşmesi (her erasure event'ında kayıtlı `FORGELM_OPERATOR`).
