# `forgelm purge` — Subcommand Referansı

> **Hedef kitle:** Eğitim corpus'larına ve koşum-kapsamlı artefact'lara karşı GDPR Madde 17 silme taleplerinin corpus adımını yürüten ForgeLM operatörleri ve ortaya çıkan `data.erasure_*` audit zincirini doğrulayan denetçiler.
> **Ayna:** [purge_subcommand.md](purge_subcommand.md)

`forgelm purge`, bir **GDPR Madde 17 silmesinin** corpus ve koşum-artefact adımı için operatör-yüzlü araçtır (Phase 21); yanıtın geri kalanı ve eğitilmiş ağırlıklar sizin sürecinizde kalır. Bir corpus satırını atomik olarak siler; `--run-id` ile bir koşumun staging dizinini (`--kind staging`) veya adı run id'yi embed eden compliance dosyalarını (`--kind artefacts`, aşağıya bakın) siler; her adımı tamper-evident `audit_log.jsonl` zincirine kaydeder.

Deployer akışı (DSAR ticket → CLI çağrısı → doğrulama) için bkz. [`../guides/gdpr_erasure.md`](../guides/gdpr_erasure-tr.md). Bu sayfa flag-başına, event-başına referanstır.

## Synopsis

```text
forgelm purge --row-id ID --corpus PATH [--row-matches {one,all}]
              [--output-dir DIR] [--config PATH] [--justification TEXT]
              [--dry-run] [--output-format {text,json}]

forgelm purge --run-id RUN_ID --kind {staging,artefacts}
              [--output-dir DIR] [--justification TEXT] [--dry-run]
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
| `--output-dir DIR` | hayır | `--corpus`'un parent'ına default'lar. Per-output-dir salt `<output_dir>/.forgelm_audit_salt`'ta `target_id` hash'leme için okunur. Implicit fallback, operatörün `--output-dir`'i eğitim çıktı dizinine ve `forgelm reverse-pii` ile cross-tool korelasyon için sabitleyebilmesi adına çözülen dizini ve memorisation kontrolünün arayacağı `final_model/`'i adlandıran bir WARNING yayar. |
| `--justification TEXT` | hayır | Operatör-sağlanan, her erasure event'ına yazılan sebep. Dahili ticket id'nizi referans verin; subject identifier yapıştırmayın. |
| `--dry-run` | hayır | Silmeyi önizler + audit zincirini (`dry_run=true` ile) yayar; disk'e dokunmaz. |

**Atomik yazma sözleşmesi.** Corpus, kardeş bir temp dosya + `os.replace` ile yeniden yazılır; kesilen bir purge ya tam silme-öncesi dosyayı ya da tam silme-sonrası dosyayı bırakır — asla kısmi state'i değil.

**Satır-numarası fallback'i reddedilir** (design §4.2). ForgeLM şu an bir id-doldurma yardımcısı sunmuyor (`forgelm audit --add-row-ids` Phase 28 backlog'unda); id'siz corpus'lara sahip operatörler `forgelm purge --row-id` çalıştırmadan önce id'leri operatör-tarafı bir script ile pre-populate etmelidir.

### Koşum-kapsamlı artefact silme (`--run-id` + `--kind`)

| Kind | Hedef |
|---|---|
| `staging` | `<output_dir>/final_model.staging.<run_id>/`; legacy `final_model.staging/` yalnızca içerdiği `staging_run_id` dosyasının içeriği `<run_id>`'ye eşitse. ForgeLM bu işaretçiyi yazmaz; işaretsiz bir legacy staging dizinini kendiniz silin. |
| `artefacts` | `<output_dir>/compliance/` altında doğrudan bulunan, adı `<run_id>`'yi `_`, `-`, `.` ya da adın başı/sonu ile sınırlı olarak içeren her dosya (yalnızca `.json` değil). |

> **`--kind artefacts` standart compliance paketini bulamaz.** Trainer ve exporter sabit dosya adları yazar (`compliance_report.json`, `training_manifest.yaml`, `data_provenance.json`, `data_governance_report.json`; yapılandırıldığında `risk_assessment.json` / `annex_iv_metadata.json`) ve hiçbiri run id içermez. Normal bir çıktı dizininde komut bu yüzden hiçbir şey bulmaz, `data.erasure_failed` (`NoMatchingArtefacts`) yayar ve 1 ile çıkar; hiçbir şey silmez. Yalnızca adına run id'yi kendiniz yazdığınız dosyalara etki eder. Resolver düzeltilene kadar paketi depolama katmanınız üzerinden kaldırın.

`--kind logs` **kasıtlı olarak yok**: audit log append-only'dir ve tool onu asla silmez. Veri sahibini adlandıran girdilerin tutulup tutulamayacağı veri sorumlusunun belgelenmiş kararıdır: bir Madde 17(1) gerekçesi geçerliyse girdiler yalnızca bir Madde 17(3) istisnasının kapsadığı yerde kalır (17(3)(b), yalnızca işlemenin veri sorumlusunun tabi olduğu AB veya üye devlet hukukundan doğan bir yasal yükümlülük ya da kamu yararına bir görev için gerekli olduğu ölçüde; 17(3)(e) hukuki talepleri kapsar); hiçbir gerekçe geçerli değilse silme gerekmez, ancak talebe yine de Madde 12 süresi içinde gerekçeli bir yanıt verilir (Madde 12(4)).

### Retention-policy raporu (`--check-policy`)

Salt-okunur tarama. `<output_dir>` üzerinde dolaşır, her artefact'ın yaşını (kanonik: `final_model.staging.<run_id>/` dizini için koşumun ilk audit event'i, yoksa audit-log genesis `timestamp`'i; fallback: dosya sistemi `mtime`'ı, `age_source=mtime` flag'iyle) yüklü config'in `retention:` horizon'larıyla karşılaştırır ve yapısal bir ihlal listesi yayar.

**Paylaşılan çıktı dizinleri.** `compliance/`, `data_audit_report.json` ve ham-belge dizinlerinin yaşı, dosyanın en son yazıldığı andan değil, audit log'un *ilk* event'inden sayılır. Birçok koşum için yeniden kullanılan bir dizinde bugün yazılmış bir paket de ufku aşmış olarak raporlanabilir: raporun listelediği bir şeyi silmeden önce dosyanın kendi tarihine bakın.

**Başarılı policy raporu her zaman 0 ile çıkar** (report-not-gate semantik, design §10 Q5). Config-load başarısızlıkları `EXIT_CONFIG_ERROR` (1) ile çıkar — eksik, okunamaz veya Pydantic doğrulamasından geçemeyen explicit `--config` non-zero olarak yüzeyselleşir; böylece operatör "loader başarısız" durumunu "ihlal yok" sanmaz. CI gate kuran operatörler `--output-format json` kullanır ve raporu kendileri sınar, örneğin `set -o pipefail` altında `jq -e '.success == true and has("count") and .count == 0'`: `count`, hiçbir `retention:` bloğu yüklenmediğinde yoktur; böylece gate hiç kontrol edilmemiş bir dizinde geçmek yerine başarısız olur, ve `--output-dir` gerçek eğitim çıktı dizini olmalıdır (olmayan bir dizin `count: 0` raporlar).

## Salt çözümlemesi

Per-output-dir salt `<output_dir>/.forgelm_audit_salt`'ta ilk kullanımda oluşturulur (mode `0600`, atomik O_EXCL yazma) — `forgelm.cli.subcommands._purge._read_persistent_salt` tarafından. `forgelm.cli.subcommands._purge._resolve_salt` `(salt_bytes, salt_source)` döner:

- `salt_source = "per_dir"` — `FORGELM_AUDIT_SECRET` set değil; persistent salt verbatim kullanılır.
- `salt_source = "env_var"` — `FORGELM_AUDIT_SECRET` set; ilk 16 byte persistent salt ile XOR'lanır.

**Bu XOR yalnızca identifier hashing'i besler.** Audit-chain HMAC anahtarı bağımsız olarak `forgelm.compliance.AuditLogger.__init__` içinde `SHA-256(FORGELM_AUDIT_SECRET ‖ run_id)` şeklinde türetilir. İki primitif kasıtlı olarak ayrıdır; `FORGELM_AUDIT_SECRET` rotasyonu her ikisini de rotate eder, ama birini incelemek diğerini ifşa etmez.

## `forgelm reverse-pii` ile cross-tool digest korelasyonu

`forgelm purge` `target_id` kaydeder (salt ile `--row-id` değerinin SHA-256'sı); `forgelm reverse-pii` ise `--query` değerinin aynı fonksiyonunu `query_hash` olarak kaydeder. İkisi yalnızca şu üç koşulun hepsi sağlandığında byte-byte aynıdır: değer aynı metindir (satırın `id` / `row_id` alanı ile sorgu aynı string olmalıdır; opak UUID satır id'leri bir e-posta sorgusuna asla eşit olmaz), iki komut da aynı `--output-dir`'i (aynı `.forgelm_audit_salt`'ı) kullanır ve `FORGELM_AUDIT_SECRET` ikisinde de aynı durumdadır (ayarsız ya da aynı değerle ayarlı; ayarlıysa purge `salt_source=env_var` kaydeder). Erişim talebini varsayılan düz-metin modunda çalıştırın: `--salt-source` gerekmez ve purge digest'lerini eşleştirmenin bir yolu değildir; çünkü `reverse-pii`'yi corpus *içinde* digest arayan bir hash-mask taramasına geçirir ve düz metin içeren bir corpus'ta hiçbir şey bulmaz. Id'ler farklıysa bir talebi silmesine, `forgelm purge --justification`'a verdiğiniz ticket referansı üzerinden bağlayın. Compliance reviewer böylece aynı veri sahibi için Madde 17 silme ve Madde 15 erişim taleplerini cleartext identifier'ı asla görmeden korele edebilir. `tests/test_reverse_pii.py::test_purge_target_id_matches_reverse_pii_query_hash_on_same_output_dir` ile pin'lendi.

## Yayılan audit event'leri

Altı event de [`audit_event_catalog.md`](audit_event_catalog-tr.md)'deki ortak zarfı taşır. Katalog satırları kolaylık için burada da listelenir.

| Event | Ne zaman yayılır | Anahtar payload |
|---|---|---|
| `data.erasure_requested` | Herhangi bir `--row-id` / `--run-id` çağrısının ilk adımı, herhangi bir silmeden ÖNCE. `--check-policy` salt-okunurdur ve event yaymaz. | `target_kind` ∈ `{row, staging, artefacts}`, `target_id` (row mode'da hash'li), `salt_source` (row mode), `corpus_path` (row), `output_dir` (run), `justification`, `dry_run` |
| `data.erasure_completed` | Başarılı silme tamamlandı. | Tüm `requested` field'ları + `bytes_freed`, `files_modified`, `pre_erasure_line_number` (row mode), `match_count` (row mode) |
| `data.erasure_failed` | Disk operasyonu raise etti VEYA eşleşen satır/koşum yok VEYA çoklu-satır policy belirsizliği reddetti. | Tüm `requested` field'ları + `error_class`, `error_message` |
| `data.erasure_warning_memorisation` | Row erasure × `--output-dir` altında doğrudan bir `final_model/` dizini mevcut (birebir ad; `--output-dir` varsayılan olarak corpus'un dizinidir, bu yüzden eğitim çıktı dizininizi verin). ForgeLM corpus'u hangi koşumların tükettiğini belirlemez. | Tüm `completed` field'ları + `affected_run_ids` |
| `data.erasure_warning_synthetic_data_present` | Row erasure × `output_dir`'de `synthetic_data*.jsonl` mevcut. | Tüm `completed` field'ları + `synthetic_files` |
| `data.erasure_warning_external_copies` | **Yalnız row modunda, `--config` verildiğinde.** Yüklü config boş-olmayan `webhook` block'u içeriyor; downstream tüketiciler bildirim almış olabilir. | Tüm `completed` field'ları + `webhook_targets` (redact'li URL'ler) |

`forgelm verify-audit <output_dir>/audit_log.jsonl` herhangi bir sayıda erasure event'i sonrası zinciri doğrulamaya devam eder — tool yeni event'ler ekler, eskileri yeniden yazmaz.

## Exit kodları

| Kod | Anlamı |
|---|---|
| 0 | Başarı veya başarılı `--check-policy` raporu (report-not-gate semantik). |
| 1 | Config hatası: bilinmeyen `--row-id`, `--run-id` ile eşleşen staging dizini / compliance dosyası yok (standart sabit-adlı pakete karşı `--kind artefacts` dahil), eksik `--corpus` ya da `--corpus`'suz `--row-id`, eksik `--kind`, seçilmemiş mod veya eksik / okunamaz / Pydantic doğrulamasından geçemeyen `--check-policy --config <path>`. |
| 2 | Runtime hatası: I/O başarısızlığı, izin reddedildi, atomic-rename başarısızlığı; ayrıca argüman-parser kullanım hatası (`--row-id` ile `--run-id` gibi birlikte verilen iki mod, `--kind logs`), JSON zarfı yerine usage basar. |

`--check-policy` asla 3 veya 4 dönmez. Kod 3 (`EXIT_EVAL_FAILURE`) ve 4 (`EXIT_AWAITING_APPROVAL`) eğitim pipeline'ı için ayrılmıştır ve bu subcommand'ın yüzeyinin parçası değildir.

## JSON çıktı zarfı

`--output-format json` ile argüman ayrıştırmayı geçen her çağrı stdout'a tam olarak bir JSON nesnesi yazdırır (argüman-parser hatası bunun yerine usage basar ve 2 ile çıkar). Başarılı ya da başarısız her zarf boolean bir `success` taşır; ona göre dallanın. Şekil moda göre değişir.

**Row modu (başarı)** — `_perform_row_erasure_and_audit`'den yayılır:

```json
{
  "success": true,
  "mode": "row",
  "dry_run": false,
  "row_id_hash": "01aeebf6566c0f6ec4a95a4678a2eb3ef45022ffb7bd9d5662b706d6d6506efc",
  "salt_source": "per_dir",
  "corpus_path": "/abs/path/data/train.jsonl",
  "matches": 1,
  "first_line": 142,
  "bytes_freed": 482,
  "warnings": []
}
```

`row_id_hash`, salt ile `--row-id` değerinin tam SHA-256 hex digest'idir; `data.erasure_*` event'leri onun ilk 16 hex hanesini `target_id` olarak taşır. `--dry-run` çağrılarında aynı zarf `dry_run: true` ile ve `bytes_freed` olmadan görünür (rewrite hiç olmaz).

**Run modu (başarı)** — `_run_purge_run_id`'den yayılır:

```json
{
  "success": true,
  "mode": "run",
  "kind": "staging",
  "dry_run": false,
  "run_id": "fg-abc123def456",
  "deleted": ["<output_dir>/final_model.staging.fg-abc123def456"],
  "bytes_freed": 2048
}
```

`deleted` gerçekten silinen yolların **listesidir**; yollar `--output-dir`'e verdiğiniz biçimde yazılır (ya da `--dry-run`'da `would_delete` anahtarı aynı şekli taşır ve `deleted` yoktur).

**`--check-policy` modu** — `_run_purge_check_policy`'ten yayılır:

```json
{
  "success": true,
  "violations": [
    {
      "artefact_kind": "compliance_bundle",
      "path": "<output_dir>/compliance",
      "age_days": 121.4,
      "horizon_days": 90,
      "age_source": "audit"
    },
    {
      "artefact_kind": "staging_dir[fg-old]",
      "path": "<output_dir>/final_model.staging.fg-old",
      "age_days": 121.4,
      "horizon_days": 7,
      "age_source": "audit"
    }
  ],
  "count": 2
}
```

`artefact_kind` şunlardan biridir: `audit_log`, `staging_dir`, `staging_dir[<run_id>]`, `compliance_bundle`, `data_audit_report` veya `raw_documents[<dizin>]`. `age_source` ∈ `{audit, mtime}` — `audit` yaşın audit log'dan sayıldığı anlamına gelir (staging dizini için koşumun ilk event'i, yoksa log'un ilk event'i); `mtime` operatörün farkındalığı için işaretlenmiş filesystem fallback'idir. Hiçbir `retention:` bloğu yüklenmediğinde (`--config` yok ya da bloğu olmayan bir config) zarf `count`'suz `{"success": true, "violations": [], "note": "..."}` olur: hiçbir şey kontrol edilmemiştir.

**Hata zarfı (her mod)** — `_output_error_and_exit`:

```json
{
  "success": false,
  "error": "No row with id=<id_hash:023f957b697d…> in 'data/train.jsonl'.  Refusing to delete."
}
```

Tanımlayıcı asla geri yazılmaz: mesaj `--row-id` değeri yerine bir hash öneki taşır.

## Bkz.

- [`../guides/gdpr_erasure.md`](../guides/gdpr_erasure-tr.md) — deployer akışı (DSAR ticket → CLI → zincir doğrulama).
- [`reverse_pii_subcommand.md`](reverse_pii_subcommand-tr.md) — kardeş Madde 15 erişim hakkı aracı.
- [`audit_event_catalog.md`](audit_event_catalog-tr.md) — zarf spec'iyle birlikte tam event sözlüğü.
- [`../qms/access_control.md`](../qms/access_control-tr.md) §3.4 — operatör kimliği sözleşmesi (her erasure event'ında kayıtlı `FORGELM_OPERATOR`).
