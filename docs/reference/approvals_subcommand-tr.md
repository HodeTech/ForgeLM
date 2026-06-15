# `forgelm approvals` — Subcommand Referansı

> **Hedef kitle:** Madde 14 insan-onay kararı bekleyen koşumları keşfeden ForgeLM operatörleri ve tek bir koşumun audit zincirini uçtan uca okuyan denetçiler.
> **Ayna:** [approvals_subcommand.md](approvals_subcommand.md)

`forgelm approvals`, [`forgelm approve` / `forgelm reject`](approve_subcommand-tr.md)'in **keşif tamamlayıcısıdır** (Phase 37). `--output-dir` altındaki `audit_log.jsonl`'i tarar ve karar bekleyen tüm koşumları (`--pending`) veya tek bir koşum için tam audit zincirini (`--show RUN_ID`) raporlar.

Subcommand salt-okunurdur: audit log'u, staging dizinini veya başka herhangi bir on-disk artefact'ı asla değiştirmez.

## Synopsis

```text
forgelm approvals --pending --output-dir DIR
                  [--output-format {text,json}]

forgelm approvals --show RUN_ID --output-dir DIR
                  [--output-format {text,json}]
```

`--pending` ve `--show` karşılıklı dışlayıcıdır (argparse zorunluluğu uygular). Tam olarak biri verilmelidir.

| Argüman / flag | Zorunlu | Açıklama |
|---|---|---|
| `--pending` | birinden biri | Audit log'unda eşleşen terminal karar (`granted` / `rejected`) bulunmayan `human_approval.required` event'i taşıyan tüm koşumları listeler. |
| `--show RUN_ID` | birinden biri | Tek bir koşum için tam approval-gate audit zincirini (talep → karar) artı on-disk staging dizin yapısını yazdırır. |
| `--output-dir DIR` | evet | `audit_log.jsonl` ve koşum başına `final_model.staging.<run_id>/` payload'ını içeren training output dizini (trainer run-id'li formu yayar; eski run-id'siz `final_model.staging/` düzeni geriye uyumlu fallback olarak korunur). |
| `--output-format {text,json}` | hayır (varsayılan `text`) | `json`, CI tüketicileri için stdout'a tam olarak bir yapısal nesne yazdırır. |

## `--pending` ne yapar

`forgelm.cli.subcommands._approvals._run_approvals_list_pending` içinde uygulanır:

1. `audit_log.jsonl`'in var olduğunu ve okunabilir olduğunu doğrular (`forgelm approve` ile aynı `_assert_audit_log_readable_or_exit` helper'ına delege eder).
2. Zinciri `human_approval.required` event'leri için tarar.
3. Her böyle event için, aynı `run_id`'de daha sonraki terminal karar (`granted` / `rejected`) tarar. Terminal kararı olmayan koşumlar pending olarak işaretlenir.
4. `RUN_ID`, `AGE` (şimdiye göre), `REQUESTED_AT` (ISO-8601), `STAGING` (present / missing) içeren bir tablo yazdırır.

Örnek text çıktı:

```text
Pending approvals (2):

RUN_ID            AGE   REQUESTED_AT               STAGING
----------------  ----  -------------------------  -------
fg-abc123def456   3h    2026-04-30T11:33:10+00:00  present
fg-def456abc789   1d    2026-04-29T14:12:55+00:00  present
```

Örnek JSON zarfı (per-summary alanları `_summarise_pending` tarafından inşa edilir):

```json
{
  "success": true,
  "pending": [
    {
      "run_id": "fg-abc123def456",
      "staging_path": "outputs/run42/final_model.staging.fg-abc123def456",
      "staging_exists": true,
      "requested_at": "2026-04-30T11:33:10+00:00",
      "age_seconds": 11340,
      "metrics": {"safety_score": 0.97, "judge_score": 8.4},
      "config_hash": null,
      "reason": "require_human_approval=true"
    }
  ],
  "count": 1
}
```

Alan notları:
- `age_seconds` integer (clock skew güvencesi — hiç negatif olmaz; text renderer bunu tabloda `3h` / `1d` olarak biçimlendirir).
- `staging_exists`, text `STAGING present|missing` hücresinin boolean karşılığı.
- `config_hash`, `human_approval.required` event payload'ından forward-compatibly okunur (legacy `config_fingerprint` anahtarına fallback ile). **Mevcut trainer emisyonu henüz iki alanı da doldurmuyor**, bu yüzden renderer `--json` çıktısında `null`, text tablosunda boş hücre gösterir; read path bağlı, ileride bir emitter doc değişikliği olmadan alanı aydınlatabilir.

## `--show RUN_ID` ne yapar

`forgelm.cli.subcommands._approvals._run_approvals_show` içinde uygulanır:

1. `--pending` ile aynı audit-log okunabilirlik kapısı.
2. Verilen `run_id` için her event'i replay eder (`human_approval.required`, `human_approval.granted`, `human_approval.rejected`).
3. Staging dizin içeriğini listeler (mevcutsa).

Bilinmeyen bir `run_id` üzerinde `--show` net bir hata mesajıyla 1 koduyla çıkar.

Örnek text çıktı:

```text
Run: fg-abc123def456
Status: pending

Audit chain (oldest first):
  [2026-04-30T11:33:10+00:00] human_approval.required — require_human_approval=true

Staging contents (4 entries):
  - adapter_config.json
  - adapter_model.safetensors
  - tokenizer.json
  - tokenizer_config.json
```

Örnek JSON zarfı (üst-düzey anahtarlar `_emit_show_json` tarafından inşa edilir):

```json
{
  "success": true,
  "run_id": "fg-abc123def456",
  "status": "pending",
  "chain": [
    {
      "event": "human_approval.required",
      "timestamp": "2026-04-30T11:33:10+00:00",
      "operator": "gha:Acme/pipelines:training:run-42"
    }
  ],
  "staging_contents": [
    "adapter_config.json",
    "adapter_model.safetensors",
    "tokenizer.json",
    "tokenizer_config.json"
  ]
}
```

Alan notları:
- `chain`, koşum için sıralı `human_approval.*` event listesidir (`required` → opsiyonel `granted` / `rejected`).
- `staging_contents`, staging path altındaki dosya/dizin adlarının düz listesi (staging dizini yoksa boş — örn. zaten promote edilmiş ya da purge edilmiş).
- `status` ∈ `{pending, granted, rejected}` — en son terminal kararı yansıtır (yoksa `pending`).

## Yayılan audit event'leri

**Hiçbiri.** `forgelm approvals` strict salt-okunur bir inspector'dır ve audit event yaymaz. Zincirde göreceğiniz event'ler yalnızca trainer (`human_approval.required`) ve `forgelm approve` / `forgelm reject` (`human_approval.granted` / `.rejected`) tarafından üretilenlerdir.

## Exit kodları

| Kod | Anlamı |
|---|---|
| 0 | `EXIT_SUCCESS` — listeleme veya `--show` başarılı. `--pending` kuyruk boşken 0 döner (pending karar yok, geçerli yanıttır). |
| 1 | `EXIT_CONFIG_ERROR` — `audit_log.jsonl` **mevcut ama okunamaz veya bozuk**, ne `--pending` ne `--show` verilmiş (argparse genelde yakalar) ya da `--show` üzerinde bilinmeyen `run_id`. |
| 2 | `EXIT_TRAINING_ERROR` — zinciri yinelerken mid-stream I/O başarısızlığı (NFS flap, kısmi-okuma OSError'ı). |

Kod 3 (`EXIT_EVAL_FAILURE`) ve 4 (`EXIT_AWAITING_APPROVAL`) bu subcommand'ın yüzeyinin parçası değildir.

> **Asimetrik missing-log davranışı.**
> - Eksik `audit_log.jsonl`'a karşı `--pending` boş bir pending listesiyle **0** döner — yeni bootstrap edilmiş bir output dizininin meşru olarak ikisi de yoktur (`_run_approvals_list_pending`).
> - Eksik `audit_log.jsonl`'a karşı `--show` **1** döner — gösterilecek bir koşum yok ve operatörün isteği spesifikti (`_run_approvals_show`).
>
> Permission-denied (yokluğa kıyasla) ikisinde de aynı şekilde ele alınır: boş bir sonuç sessizce sunmak yerine açık bir hatayla exit 1.

## CI kullanım deseni

JSON zarfı desteklenen CI yüzeyidir:

```bash
# Her staged model bir karara sahip olana kadar deploy job'ını blokla.
pending=$(forgelm approvals --pending --output-dir ./outputs --output-format json | jq '.count')
if [ "$pending" -gt 0 ]; then
    echo "::warning::$pending onay hâlâ bekliyor"
    exit 1
fi
```

Daha zengin bir policy kuran operatörler (örn. "herhangi bir bekleyen karar N saatten eskiyse deploy'u blokla") `age_seconds` field'ını parse eder — ör. "bir günden eski olan her şey" için `jq '[.pending[] | select(.age_seconds > 86400)] | length'`. Text çıktısını yalnızca tavsiye olarak ele alın — JSON zarfı stabil sözleşmedir.

## Bkz.

- [`approve_subcommand.md`](approve_subcommand-tr.md) — terminal karar tamamlayıcısı (`approve` / `reject`).
- [`../guides/human_approval_gate.md`](../guides/human_approval_gate-tr.md) — deployer akışı.
- [`audit_event_catalog.md`](audit_event_catalog-tr.md) — bu subcommand tarafından okunan `human_approval.*` satırları dahil tam event sözlüğü.
- [`../qms/access_control.md`](../qms/access_control-tr.md) §6 — segregation-of-duties cookbook (`--show`'un projelendirdiği `human_approval.granted` satırlarını kullanır).
