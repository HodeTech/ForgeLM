# `forgelm reverse-pii` — Subcommand Referansı

> **Hedef kitle:** Eğitim corpus'larına karşı GDPR Madde 15 erişim hakkı taleplerini yanıtlayan ForgeLM operatörleri ve ortaya çıkan `data.access_request_query` audit satırını doğrulayan denetçiler.
> **Ayna:** [reverse_pii_subcommand.md](reverse_pii_subcommand.md)

`forgelm reverse-pii`, ForgeLM eğitim corpus'ları için **GDPR Madde 15 erişim hakkı**'nın operatör-yüzlü uygulamasıdır (Phase 38). [`forgelm purge`](purge_subcommand-tr.md) "satırımı sil" derken, `reverse-pii` "identifier'ımın geçtiği her satırı bul" der.

## Synopsis

```text
forgelm reverse-pii --query VALUE
                    [--type {literal,email,phone,tr_id,us_ssn,iban,credit_card,custom}]
                    [--salt-source {per_dir,env_var}]
                    [--output-dir DIR] [--audit-dir DIR]
                    [--output-format {text,json}]
                    JSONL_GLOB [JSONL_GLOB ...]
```

| Argüman / flag | Zorunlu | Açıklama |
|---|---|---|
| `JSONL_GLOB` (positional, ≥1) | evet | Bir veya daha fazla JSONL path veya glob pattern (`data/*.jsonl`, `corpora/**/train.jsonl`). Recursive `**` desteklenir. |
| `--query VALUE` | evet | Aranacak identifier (e-posta, telefon, ID, regex pattern veya pre-hash'li digest). Audit emisyonundan önce hash'lenir; ancak corpus'tan tarama sırasında cleartext olarak okunur. |
| `--type {...}` | hayır (varsayılan `literal`) | Identifier kategorisi. `literal` ve tip-spesifik değerler (`email`, `phone`, `tr_id`, `us_ssn`, `iban`, `credit_card`) `--query`'yi literal substring olarak işler (`re.escape` uygulanır) — Madde 15 erişim talepleri için güvenli seçim, e-postalardaki noktalar keyfi karakterlere eşleşmemeli. `custom` `--query`'yi keyfi Python regex olarak yorumlar (dikkatli kullan; POSIX main-thread çağrılarında dosya başına SIGALRM timeout ile ReDoS guard'lı). |
| `--salt-source {per_dir,env_var}` | hayır | **Hash-mask scan**'a geçiş: `forgelm purge`'ün kullandığı aynı per-output-dir salt ile `SHA256(salt + identifier)` hesaplanır ve her JSONL satırında aranır. Bu flag olmadan, scan plaintext residual scan'dir. |
| `--output-dir DIR` | hayır | Per-output-dir salt dosyasını (`.forgelm_audit_salt`) içeren dizin. Salt buradan HEM audit-event hash'i için (her çağrı, plaintext veya hash-mask mod) HEM de `--salt-source` set olduğunda hash-mask scan için okunur/oluşturulur. İlk çözülen corpus dosyasının parent'ına default'lar; implicit fallback çözülen dizini adlandıran bir WARNING yayar; böylece operatör `forgelm purge` ile cross-tool korelasyon için `--output-dir`'i pin'leyebilir. |
| `--audit-dir DIR` | hayır | Audit chain entry'lerinin yazılacağı yer (default: `--output-dir` ile aynı; `forgelm verify-audit` aynı subject için Madde 17 + Madde 15 event'lerini tek zincirde korele eder). Yazılamaz bir explicit `--audit-dir` Madde 15 forensik kaydını sessizce düşürmek yerine yüksek sesle başarısız olur. |

## İki scan modu

### Plaintext residual scan (varsayılan)

Mask sızıntılarını tespit eder: operatör corpus'un ingest'te maskelendiğine inanmıştı, ama bir residual span sızdı.

```shell
$ forgelm reverse-pii --query "alice@example.com" --type email \
    --output-dir ./outputs data/*.jsonl
```

Scan her satırı cleartext okur. Audit event yine **hash'li** `query_hash`'i kaydeder; cleartext audit zincirine asla yazılmaz.

### Hash-mask scan (`--salt-source`)

`forgelm purge`'ün `target_id` field'ı için kullandığı aynı per-output-dir salt ile `SHA256(salt + identifier)` digest'leri embed eden **harici** bir pipeline tarafından maskelenmiş corpus'lar için. ForgeLM kendi hash-replacement ingest stratejisini ship etmez; bu mod, purge salt'ını paylaşılan secret olarak kullanarak toolkit dışında böyle bir pipeline kuran operatörler içindir.

```shell
$ forgelm reverse-pii --query "alice@example.com" --type email \
    --salt-source per_dir --output-dir ./outputs data/*.jsonl
```

`--salt-source env_var` `FORGELM_AUDIT_SECRET`'in set olmasını gerektirir; `per_dir` `<output_dir>/.forgelm_audit_salt`'taki salt dosyasını okur.

## Identifier tipleri

| Tip | İşleyiş |
|---|---|
| `literal` (varsayılan) | Literal substring (`re.escape` uygulanır). Noktaların keyfi karakterlere eşleşmemesi gereken e-postalar için güvenli. |
| `email`, `phone`, `tr_id`, `us_ssn`, `iban`, `credit_card` | `literal` ile aynı literal-substring işlemi. Tip etiketi audit satırının `identifier_type` field'ına yazılır (downstream filtreleme için). |
| `custom` | `--query`'yi Python regex olarak yorumlar. POSIX main-thread çağrılarında dosya başına 30s SIGALRM bütçesi ReDoS hang'lerine karşı koruma sağlar. **Windows'ta VE POSIX worker thread'lerinde SIGALRM guard no-op'tur** (signal handler'ları main thread'den kurulmalıdır); worker thread'den veya Windows'ta `--type custom` çalıştıran operatörler regex'lerini kendileri vet etmelidir. |

## `forgelm purge` ile cross-tool digest korelasyonu

`forgelm reverse-pii --query <value> --salt-source per_dir` ve `forgelm purge --row-id <value>` **aynı** `<output_dir>`'e (yani aynı `.forgelm_audit_salt`'ı tüketerek) çalıştırıldığında, `data.access_request_query` üzerindeki `query_hash` ile `data.erasure_*` event'leri üzerindeki `target_id` byte-byte aynıdır. Bu, compliance reviewer'ın Madde 15 erişim talebini ve aynı subject için Madde 17 silmesini tek bir audit zincirinde, cleartext identifier operatör terminalinden hiç çıkmadan korele etmesini sağlar. `tests/test_reverse_pii.py::test_purge_target_id_matches_reverse_pii_query_hash_on_same_output_dir` ile pin'lendi.

`<output_dir>/.forgelm_audit_salt`'taki salt **yalnızca identifier-hash salt'ıdır** — audit-chain HMAC anahtarına katılmaz; o anahtar bağımsız olarak `forgelm.compliance.AuditLogger.__init__` içinde `SHA-256(FORGELM_AUDIT_SECRET ‖ run_id)` şeklinde türetilir.

## Yayılan audit event'leri

Her çağrı tam olarak bir event yayar ([katalog satırı](audit_event_catalog-tr.md#madde-15--gdpr-erişim-hakkı-phase-38--forgelm-reverse-pii)):

| Event | Ne zaman yayılır | Anahtar payload |
|---|---|---|
| `data.access_request_query` | Scan tamamlandıktan sonra (veya mid-scan I/O hatası sonrası — `error_class` / `error_message` ile). | `query_hash` (raw identifier'ın salt'lı SHA-256'sı — asla raw; purge'ün per-output-dir salt'ını yeniden kullanır), `identifier_type` ∈ `{literal, email, phone, tr_id, us_ssn, iban, credit_card, custom}`, `scan_mode` ∈ `{plaintext, hash}`, `salt_source` ∈ `{plaintext, per_dir, env_var}`, `files_scanned` (path'ler), `match_count`, opsiyonel `error_class` / `error_message` |

Chain satırı **hash'li** identifier'ı kaydeder; böylece zincirin kendisi subject verisini sızdırmaz.

## Exit kodları

| Kod | Anlamı |
|---|---|
| 0 | `EXIT_SUCCESS` — scan tamamlandı (matches listesi boş olabilir — Madde 15 "eşleşme yok"u geçerli yanıt olarak açıkça kabul eder). |
| 1 | `EXIT_CONFIG_ERROR` — boş `--query`, hatalı `custom` regex, boş çözülmüş glob, `FORGELM_AUDIT_SECRET` set olmadan `--salt-source env_var`. |
| 2 | `EXIT_TRAINING_ERROR` — mid-scan I/O başarısızlığı, izin reddedildi, ReDoS SIGALRM timeout ya da **explicit `--audit-dir` yazılamaz** (explicit form Madde 15 forensic kaydı sessizce düşmesin diye gürültülü reddeder — `_maybe_audit_logger`). |

Kod 3 (`EXIT_EVAL_FAILURE`) ve 4 (`EXIT_AWAITING_APPROVAL`) bu subcommand'ın yüzeyinin parçası değildir.

## JSON çıktı zarfı

`--output-format json` ile scan stdout'a tam olarak bir JSON nesnesi yazdırır — `_run_reverse_pii_cmd`'den yayılır:

```json
{
  "success": true,
  "query_hash": "9f2c8b…",
  "identifier_type": "email",
  "scan_mode": "plaintext",
  "salt_source": "per_dir",
  "matches": [
    {
      "file": "data/train.jsonl",
      "line": 4119,
      "snippet": "…alice@example.com is the canonical contact…"
    }
  ],
  "files_scanned": [
    {"path": "data/train.jsonl", "match_count": 1},
    {"path": "data/validation.jsonl", "match_count": 0}
  ],
  "match_count": 1
}
```

Alan notları:
- Her match `{file, line, snippet}` (anahtarlar `file` — `path` değil; `snippet` — `preview` değil).
- `files_scanned` bir **`{path, match_count}` nesneleri listesidir** — integer sayı değil. Dosya sayısı için `len(envelope["files_scanned"])`, dosya-başına hit dağılımı için her girişin `match_count`'unu kullanın.
- `query_hash` cleartext sorgunun salted SHA-256'sıdır. Cleartext zarfta yansıtılmaz, audit chain'e de yazılmaz.
- `salt_source` ∈ `{plaintext, per_dir, env_var}` — burada `plaintext` "hash-mask scan yok" demek (varsayılan); `per_dir` / `env_var` `--salt-source` modunu yansıtır.

Başarısız scan standart hata zarfını yayar (`_output_error_and_exit`):

```json
{"success": false, "error": "Glob 'data/*.jsonl' resolved to zero files."}
```

## Bkz.

- [`../guides/gdpr_erasure.md`](../guides/gdpr_erasure-tr.md) §"Madde 15 erişim hakkı" — deployer akışı.
- [`purge_subcommand.md`](purge_subcommand-tr.md) — kardeş Madde 17 silinme hakkı aracı; cross-tool digest korelasyonu için per-output-dir salt'ı paylaşır.
- [`audit_event_catalog.md`](audit_event_catalog-tr.md) — zarf spec'i ile birlikte tam event sözlüğü.
- [`../qms/access_control.md`](../qms/access_control-tr.md) §3.4 — operatör kimliği sözleşmesi (her erişim talebi event'ında kayıtlı `FORGELM_OPERATOR`).
