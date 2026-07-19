---
title: JSON çıktı şemaları
description: Her forgelm subcommand için kilitli --output-format json envelope yapısı. CI tüketicileri bu alan adlarına bağımlıdır.
---

# JSON çıktı şemaları

`--output-format json` destekleyen her `forgelm` subcommand'ı stdout'a stabil bir JSON envelope üretir. Alan adları ve nesting [GitHub'daki release standardı](https://github.com/HodeTech/ForgeLM/blob/main/docs/standards/release.md) gereği public CLI kontratının parçası: bir anahtarı yeniden adlandırmak MAJOR-version break sayılır.

Bu sayfa kanonik referanstır. `forgelm` çıktısını parse eden CI/CD pipeline'ları burada belgelenen yapılara karşı pin'lemelidir.

## Ortak konvansiyonlar

- **stdout vs stderr.** JSON envelope **stdout**'a gider. İnsana okunabilir loglar (info / warning / error) **stderr**'e gider. `forgelm ... --output-format json | jq .` ile pipe edip operatör mesajlarını `2>` ile ayrı okuyun.
- **Üst seviye sarmalayıcı.** Her envelope `"success": true | false` ile başlar. Tüketiciler önce bu tek anahtarda branch açabilir.
- **Hata envelope'u.** `success: false` olduğunda envelope `"error": "<mesaj>"` (string) taşır. Opsiyonel zenginleştirilmiş alanlar (`exit_code`, `error_type`, `details`) [GitHub'daki error-handling standardı](https://github.com/HodeTech/ForgeLM/blob/main/docs/standards/error-handling.md) gereği var olabilir. Bu alanlardan kesinlik isteyen tüketiciler `$?` ile süreç exit kodunu da kontrol etmelidir.
- **Exit kodları.** Bkz. [Exit Kodları](#/reference/exit-codes). `success: false` her zaman non-zero bir exit ile eşleşir. `success: true` normal durumda exit `0` ile eşleşir, **tek istisna** dışında: Article 14 insan-onay gate'inde duraklayan bir eğitim çalışması `success: true` **ve** `awaiting_approval: true` emit ederken exit `4` ile çıkar (model stage'lenmiştir, henüz promote edilmemiştir). "Bitti" ile "insan onayı bekliyor"u ayırması gereken tüketiciler tek başına `success` yerine `awaiting_approval` discriminator'ını (veya `$?`'i) okumalıdır.
- **Argüman-ayrıştırma hataları istisnadır.** JSON-envelope garantisi argümanlar başarıyla ayrıştırıldıktan **sonra** başlar. Ayrıştırma anında saptanan bir kullanım hatası (bilinmeyen flag, hatalı `--choice`, `--workers 0` gibi aralık-dışı validator değeri) `argparse`'in kendisi tarafından raporlanır: **stderr**'e insana okunabilir bir `usage: ... error: ...` satırı ve exit kodu `2`, stdout'ta **hiçbir** JSON envelope olmadan. Her non-zero exit'te stdout-JSON okuyan bir tüketici, boş stdout'u (non-zero `$?` ile eşleşen) bir ayrıştırma/kullanım hatası olarak ele almalı ve mesaj için stderr'e dönmelidir. Envelope kontratı yalnızca dispatcher çalışmaya başladıktan sonra ortaya çıkan hatalar için geçerlidir.

## `forgelm doctor`

Ortam kontrolü. Bkz. [Doctor komutu](#/getting-started/first-run).

**Başarı envelope'u** (`forgelm doctor [--offline] --output-format json`):

```json
{
  "success": true,
  "checks": [
    {
      "name": "forgelm.install",
      "status": "pass",
      "detail": "ForgeLM 0.9.0 running from /opt/venv/lib/python3.11/site-packages/forgelm (inside site-packages).",
      "extras": {"version": "0.9.0", "location": "/opt/venv/lib/python3.11/site-packages/forgelm", "in_site_packages": true}
    },
    {
      "name": "python.version",
      "status": "pass",
      "detail": "Python 3.11.7 (CPython).",
      "extras": {"version": "3.11.7", "implementation": "CPython"}
    }
  ],
  "summary": {"pass": 8, "warn": 1, "fail": 0, "crashed": 0}
}
```

| Anahtar | Tip | Notlar |
|---|---|---|
| `success` | bool | Hiçbir probe `fail` değilse VE hiçbir probe crash etmediyse `true`; aksi halde `false`. |
| `checks` | list[object] | Çalıştırma sırasıyla probe başına bir entry. Probe adları stabildir (örn. `forgelm.install`, `python.version`, `torch.cuda`, `numpy.torch_abi`, `gpu.inventory`, `extras.qlora`, `hf_hub.reachable`, `hf_hub.offline_cache`, `disk.workspace`, `operator.identity`, `pypdf_normalise.turkish`). `--offline` modunda `hf_hub.offline_cache`, `hf_hub.reachable` yerine geçer. |
| `checks[].name` | str | Probe adı. Sürümler arası stabil; **"eklenir" (append) burada isim dağarcığını tanımlar, çalıştırma sırasını değil** — yeni probe'lar bir kez yayınlandıktan sonra asla yeniden adlandırılmaz, ama içeriklerinin okunması en faydalı olacağı yere eklenirler (örn. `forgelm.install`, listenin sonuna değil ilk satıra eklendi — çünkü raporun diğer her satırı, okuyucu raporu hangi ForgeLM kopyasının ürettiğini bilene kadar belirsizdir). Tüketiciler herhangi bir probe için sabit bir index varsaymamalıdır. |
| `checks[].status` | str | `pass`, `warn`, `fail`'tan biri. Raise eden bir probe `status: "fail"` ve `extras.crashed: true` ile görünür; crash ayrıca `summary.crashed` içinde sayılır. |
| `checks[].detail` | str | Sonuç için operatör-yüzlü tek satırlık açıklama. |
| `checks[].extras` | object | Probe-spesifik yapısal veri. Per-probe anahtarlar `_doctor.py` docstring'lerinde belgelidir; tüketiciler bilinmeyen anahtarları forward-compatible olarak ele almalıdır. |
| `summary` | object | `checks` arasında her status'ın sayısı. Toplam `len(checks)`'a eşittir. |

**Exit code mapping:** `0` = tüm probe'lar `pass` veya `warn`; `1` = en az bir `fail`; `2` = en az bir `crashed` (probe raise etti; sonraki probe'lar yine de çalıştı).

## `forgelm --dry-run` (config doğrulama)

`forgelm --config <yaml> --dry-run --output-format json`, ağır stack'i yüklemeden veya GPU'ya dokunmadan config'i doğrular ve eğitimin *ne yapacağını* özetler. En sık kullanılan CI ön-uçuş çağrısıdır. Geçerli bir config'te şu envelope'u **stdout**'a basıp exit code `0` ile çıkar:

```json
{
  "success": true,
  "status": "valid",
  "model": "org/model",
  "backend": "auto",
  "load_in_4bit": false,
  "trust_remote_code": false,
  "dora": false,
  "lora_rank": 16,
  "lora_alpha": 32,
  "dataset": "org/dataset",
  "epochs": 3,
  "batch_size": 1,
  "output_dir": "/work/output/final_model",
  "offline": false,
  "distributed": null,
  "rope_scaling": null,
  "neftune_noise_alpha": null,
  "webhook_configured": false,
  "galore_enabled": false,
  "galore_optim": null,
  "galore_rank": null,
  "auto_revert": false,
  "safety_enabled": false,
  "safety_scoring": null,
  "compliance_configured": false,
  "risk_classification": null
}
```

| Anahtar | Tip | Notlar |
|---|---|---|
| `success` | bool | Bu kod yolunda her zaman `true` — bir dry run stdout'a yalnızca config doğrulandıktan sonra ulaşır. Geçersiz config bunun yerine 2 anahtarlı hata envelope'u (`success: false`) ile `1` çıkar. |
| `status` | str | Pre-0.7.1 tüketicilerle geriye dönük uyumluluk için korunan sabit token `"valid"`. Yeni tüketiciler `success` üzerinde branch yapmalı. |
| `model` / `backend` / `dataset` | str | Config'ten çözülen model id'si, model backend'i ve dataset id'si. |
| `output_dir` | str | Etkin nihai-model yolu (`training.output_dir`, `training.final_model_dir` ile birleştirilmiş). |
| `load_in_4bit`, `trust_remote_code`, `dora`, `offline` | bool | İlgili config bayraklarının etkin değerleri. |
| `lora_rank`, `lora_alpha`, `epochs`, `batch_size` | int | Etkin eğitim/LoRA hiper-parametreleri. |
| `distributed` | str \| null | `distributed.strategy` veya dağıtık eğitim yapılandırılmadığında `null`. |
| `rope_scaling`, `neftune_noise_alpha` | object \| float \| null | Etkin long-context / NEFTune ayarları, ayarlanmadığında `null`. |
| `webhook_configured` | bool | Bir webhook URL'si (literal veya `url_env`) yapılandırıldığında `true`. |
| `galore_enabled`, `galore_optim`, `galore_rank` | bool / str / int | GaLore optimizer özeti; GaLore kapalıyken son ikisi `null`. |
| `auto_revert`, `safety_enabled`, `safety_scoring` | bool / bool / str | Değerlendirme-kapısı özeti (safety eval kapalıyken `safety_scoring` `null`). |
| `compliance_configured`, `risk_classification` | bool / str | EU AI Act uyumluluk özeti; compliance yapılandırılmadığında `risk_classification` `null`. |

**Exit code mapping:** geçerli config `0` (`EXIT_SUCCESS`) ile çıkar; geçersiz config standart hata envelope'u ile `1` (`EXIT_CONFIG_ERROR`) çıkar.

## `forgelm` (eğitim) — preflight abort envelope

Eğitim pipeline'ı (`forgelm --config <yaml> --output-format json`) ağır stack'i import etmeden önce bir torch/NumPy ABI sanity check çalıştırır. Sağlıklı bir ortamda preflight sessizdir ve eğitim normal şekilde devam eder; bilinen Intel Mac NumPy 2 / torch 2.2 mismatch'inde preflight şu envelope ile **stdout**'a basıp exit code `2` ile abort eder:

```json
{
  "success": false,
  "error": "numpy_torch_abi_mismatch",
  "torch_version": "2.2.2",
  "numpy_version": "2.4.4",
  "remediation": "torch 2.2.2 (compiled against NumPy 1.x) is paired with numpy 2.4.4. ... Fix with: pip install 'numpy<2' ..."
}
```

| Anahtar | Tip | Not |
|---|---|---|
| `success` | bool | Bu kod yolunda her zaman `false`; sağlıklı preflight hiçbir şey emit etmez ve pipeline devam eder. |
| `error` | str | Stabil token `"numpy_torch_abi_mismatch"`. CI tüketicileri tam bu değer üzerinden branch'leyebilir. |
| `torch_version` | str | Preflight anında `torch.__version__`'un raporladığı versiyon string'i. |
| `numpy_version` | str | Preflight anında `numpy.__version__`'un raporladığı versiyon string'i. |
| `remediation` | str | İnsan-okunabilir fix talimatı, tam `pip install 'numpy<2'` komutuyla biten. `forgelm doctor` `numpy.torch_abi` probe'unun `detail` alanıyla bire bir aynı metin — tek kaynak. |

**Exit code mapping:** preflight abort, `2` (`EXIT_TRAINING_ERROR`, runtime-error sınıfı) ile çıkar. Aynı `forgelm doctor` `numpy.torch_abi` probe'u önceden `status: "fail"` olarak yüzeye çıkarırdı; preflight, `doctor`'ı skip edip eğitimi doğrudan çalıştıran operatörler için ikinci savunma hattıdır.

## `forgelm` (eğitim) — sonuç envelope'u

Eğitim tamamlanana kadar çalıştığında pipeline **stdout**'a bir sonuç envelope'u emit eder. Aynı envelope şekli üç sonucu da kapsar; `success` + `awaiting_approval` + `reverted` anahtarları aralarında ayrım yapar.

**Başarı envelope'u** (model promote edildi; exit `0`):

```json
{
  "success": true,
  "metrics": {"eval_loss": 0.42, "benchmark/average": 0.78},
  "final_model_path": "/work/output/final_model",
  "reverted": false,
  "awaiting_approval": false,
  "run_id": "fg-abc123def456",
  "config_hash": "sha256:..."
}
```

**Onay-bekliyor envelope'u** (`evaluation.require_human_approval: true`; model **stage'lendi**, henüz promote edilmedi; exit `4`):

```json
{
  "success": true,
  "metrics": {"eval_loss": 0.42},
  "final_model_path": "/work/output/final_model.staging.fg-abc123def456",
  "reverted": false,
  "awaiting_approval": true,
  "staging_path": "/work/output/final_model.staging.fg-abc123def456"
}
```

**Revert envelope'u** (bir post-train gate `auto_revert: true` altında başarısız oldu; artefactlar silindi; exit `3`):

```json
{
  "success": false,
  "metrics": {"eval_loss": 0.91},
  "final_model_path": "/work/output/final_model",
  "reverted": true,
  "awaiting_approval": false
}
```

| Anahtar | Tip | Not |
|---|---|---|
| `success` | bool | Çalışma tamamlandığında (stage'lenmiş, onay-bekleyen çalışma dahil) `true`. Bir gate modeli auto-revert ettiğinde `false`. |
| `metrics` | object | Sayısal eğitim/eval/gate metrikleri (ör. `eval_loss`, `benchmark/average`, `safety/safe_ratio`). |
| `final_model_path` | str | Model artefactlarının yaşadığı yer. Onay-bekleyen bir çalışma için bu, `forgelm approve` promote edene kadar **staging** dizinidir. |
| `reverted` | bool | Bir gate (eval-loss / benchmark / safety / judge) modeli auto-revert ettiyse `true`. `awaiting_approval` ile karşılıklı dışlayıcıdır. |
| `awaiting_approval` | bool | **Discriminator.** Çalışma Article 14 insan-onay gate'inde durakladıysa (exit `4`) `true`. Revert edilmiş bir çalışma burada her zaman `false`'tur. |
| `staging_path` | str | Yalnızca `awaiting_approval` `true` iken vardır; `forgelm approve <run_id>` / `forgelm reject <run_id>`'e geçilecek on-disk staging dizini. |
| `run_id` | str | Çalışma tanımlayıcısı — çalışmayı `audit_log.jsonl`'i ve varsa onay gate'i ile ilişkilendirir. Trainer sonucu ürettiğinde her zaman vardır. |
| `config_hash` | str | Çalışmayı üreten doğrulanmış config'in `sha256:` digest'i (yeniden-üretilebilirlik çıpası). Trainer sonucu ürettiğinde her zaman vardır. |

Opsiyonel alt-bloklar (`benchmark`, `resource_usage`, `estimated_cost_usd`, `safety`, `judge`) yalnızca o değerlendirmeler çalıştığında eklenir.

**Exit code mapping:** `0` = başarı (promote edildi); `3` = `EXIT_EVAL_FAILURE` (bir gate modeli revert etti — `reverted: true`); `4` = `EXIT_AWAITING_APPROVAL` (`awaiting_approval: true`, insan onayı bekleyen stage). Revert edilmiş bir çalışma **asla** onay-bekliyor olarak raporlanmaz.

## `forgelm approvals --pending`

Bekleyen Madde 14 onay isteklerini en yeniden başa doğru listeler.

```json
{
  "success": true,
  "pending": [
    {
      "run_id": "fg-abc123def456",
      "staging_path": "/work/output/final_model.staging.fg-abc123def456",
      "staging_exists": true,
      "requested_at": "2026-05-04T12:34:56+00:00",
      "age_seconds": 3600.5,
      "metrics": {"safety_score": 0.91, "benchmark.hellaswag": 0.78},
      "config_hash": "sha256:...",
      "reason": "post-train safety eval below threshold"
    }
  ],
  "count": 1
}
```

| Anahtar | Tip | Notlar |
|---|---|---|
| `success` | bool | `true` (her zaman — boş liste başarıdır). |
| `pending` | list[object] | En yeniden başa; `count == len(pending)`. Boş liste = bekleyen onay yok. |
| `pending[].run_id` | str \| null | Audit event'inden run identifier. |
| `pending[].staging_path` | str \| null | Çözülmüş staging dizini (audit event `staging_path` taşımıyorsa ve canonical fallback yoksa `null`). Path-traversal korumalı — `--output-dir` dışındaki yollar resolve aşamasında reddedilir. |
| `pending[].staging_exists` | bool | `staging_path` mevcut bir dizine resolve oluyorsa `True`. |
| `pending[].requested_at` | str \| null | Audit event'inden ISO-8601 timestamp. |
| `pending[].age_seconds` | number \| null | `requested_at`'tan beri saniye; timestamp parse edilemezse `null`. |
| `pending[].metrics` | object | Audit event'inden serbest-form per-run metric'ler. |
| `pending[].config_hash` | str \| null | Bilindiğinde config fingerprint. |
| `pending[].reason` | str \| null | Audit event'inden operatör tarafından sağlanan sebep. |
| `count` | int | Bekleyen sayı; `len(pending)`'a eşit. |

## `forgelm approvals --show RUN_ID`

Bir koşu için tam onay-gate audit chain'i + staging içeriğini incele.

```json
{
  "success": true,
  "run_id": "fg-abc123def456",
  "status": "pending",
  "chain": [
    {"event": "human_approval.required", "run_id": "fg-abc123def456", "timestamp": "2026-05-04T12:34:56+00:00", "...": "..."}
  ],
  "staging_contents": ["adapter_config.json", "adapter_model.safetensors", "tokenizer.json"]
}
```

| Anahtar | Tip | Notlar |
|---|---|---|
| `success` | bool | Koşu bulunduğunda `true`; `RUN_ID`'nin audit event'i yoksa `false` (`error` ile). |
| `run_id` | str | Echo'lanan input `RUN_ID`. |
| `status` | str | `pending`, `granted`, `rejected`, `unknown`'dan biri. Latest-wins semantik: önceki bir karardan sonra re-stage edilen koşu `pending` gösterir. |
| `chain` | list[object] | `run_id` için her onay-gate audit event'i, append sırasıyla. |
| `staging_contents` | list[str] | `<output_dir>/final_model.staging.<run_id>` (veya canonical fallback) altında sıralı dosya/dizin adları. Staging eksik veya okunamıyorsa boş. |
| `corrupted` | bool | Yalnızca audit log'un strict parse'ı bir satırı reddedeceğinde bulunur ve `true` olur. O durumda gösterilen `status` **yetkili değildir** — `approve`/`reject` (log'u strict okur) log onarılana kadar işlem yapmayı reddeder. Temiz log'da yoktur. |
| `corruption_detail` | str | `corrupted` ile birlikte bulunur; ilk hatalı satırı belirtir (`audit log corrupted at line N: <neden>`). |

## `forgelm audit`

Eğitim öncesi veri denetimi. Tam rapor; ana alanlar gösterildi.

```json
{
  "success": true,
  "report_path": "audit/data_audit_report.json",
  "splits": {"train": {"sample_count": 100, "...": "..."}, "...": {}},
  "pii_summary": {"total_findings": 0, "by_kind": {}},
  "secrets_summary": {"total_findings": 0, "by_kind": {}},
  "cross_split_overlap": {"pairs": {}},
  "leakage": {"...": "..."},
  "quality_summary": {"samples_evaluated": 100, "samples_flagged": 3, "by_check": {"...": "..."}},
  "near_duplicates": {"...": "..."},
  "languages_top3": [{"code": "en", "count": 87}],
  "generated_at": "2026-05-04T12:34:56+00:00",
  "warnings": []
}
```

> **Faz 15 (v0.6.0) notu:** `quality_summary`, v0.6.0'dan itibaren default
> olarak doldurulur çünkü audit'in `--quality-filter` bayrağı default-AÇIK'a
> çevrildi. Atlamak için `--no-quality-filter` geçirin; o durumda alan `{}`
> olur.

Tam şema manuel içindeki [Veri Audit'i](#/data/audit) sayfasındadır. CI gate'leri için `report_path` (on-disk JSON'un yeri) + `success`'e karşı pin'leyin.

## `forgelm ingest`

Doküman ingestion stdout envelope'ı. Faz 15, `notes_structured` altına
additive alanlar ekledi; Faz 15 öncesi anahtarlar değişmedi.

```json
{
  "success": true,
  "output_path": "data/curated.jsonl",
  "chunk_count": 245,
  "files_processed": 12,
  "files_skipped": 0,
  "total_chars": 824513,
  "format_counts": {".pdf": 4, ".docx": 6, ".md": 2},
  "pii_redaction_counts": {"email": 3, "phone": 1},
  "secrets_redaction_counts": {},
  "pdf_header_footer_lines_stripped": 18,
  "pdf_paragraph_packed_lines_stripped": 4,
  "script_sanity_triggered": 1,
  "strip_pattern_substitutions": 12,
  "urls_handled": 7,
  "frontmatter_pages_dropped": 3,
  "notes": ["..."],
  "notes_structured": {
    "files_processed": 12,
    "files_skipped": 0,
    "chunk_count": 245,
    "total_chars": 824513,
    "strategy": "paragraph",
    "format_counts": {".pdf": 4, ".docx": 6, ".md": 2},
    "pdf_header_footer_lines_stripped": 18,
    "pdf_paragraph_packed_lines_stripped": 4,
    "script_sanity_summary": {"files_checked": 12, "files_triggered": 1, "...": "..."},
    "frontmatter_pages_dropped": [0, 1, 11],
    "strip_pattern_substitutions": 12,
    "urls_handled": 7,
    "quality_presignal": {"samples_evaluated": 245, "samples_flagged": 17, "by_check": {"alpha_ratio": 5, "weird_chars": 1, "repeated_lines": 11}}
  }
}
```

Çok dosyalı bir batch'te, üst düzey `frontmatter_pages_dropped` tamsayısı
tüm dosyalar boyunca gerçek bir toplamdır; `notes_structured.frontmatter_pages_dropped`
ise distinct-index bir örnektir (hangi sayfa pozisyonlarının düşürüldüğü,
kaç kez düşürüldüğü değil) — üst düzey toplamdan daha kısa olabilir ve bu
bir hata değildir.

Faz 15 additive alanları [Doküman Ingestion](#/data/ingestion)'da
belgelenmiştir. Faz 15 öncesi `output_path` / `chunk_count` / `notes`
okuyan tüketiciler değişmeden çalışmaya devam eder.

## `forgelm verify-audit`

Audit log chain bütünlüğü kontrolü.

```json
{
  "success": true,
  "valid": true,
  "entries_count": 87,
  "hmac_verified": true,
  "errors": []
}
```

| Anahtar | Tip | Notlar |
|---|---|---|
| `success` | bool | `true` ⟺ `valid: true`. |
| `valid` | bool | Herhangi bir prev_hash mismatch / monotonicity break / seq gap varsa `false`. |
| `entries_count` | int | Düzgün-formed audit satır sayısı. |
| `hmac_verified` | bool \| null | `--hmac-secret` her `hmac` alanıyla eşleşince `true`; mismatch'te `false`; chain'de HMAC alanı yoksa `null`. |
| `errors` | list[str] | Tespit edilen sorun başına bir insan-okunur satır. |

## `forgelm approve` / `forgelm reject`

```json
{
  "success": true,
  "run_id": "fg-abc123def456",
  "approver": "alice@example.com@workstation-7",
  "final_model_path": "/work/output/final_model",
  "promote_strategy": "rename"
}
```

`promote_strategy` değeri `"rename"` (aynı-cihaz atomik `os.rename`) ya da `"move"` (cihazlar-arası `shutil.move` fallback)'tır.

`approve` başarıda `0` ile çıkar; `reject` rejection kaydı sonrası `0` ile çıkar (staging dizini forensics için korunur). Bilinmeyen `run_id` / config hatasında `error` ile `success: false`.

## `forgelm purge`

Üç-modlu dispatcher: `--row-id`, `--run-id` veya `--check-policy`. Wave 2b Phase 21 — GDPR Madde 17 silme hakkı.

**Satır-silme başarı zarfı — wet run** (`forgelm purge --row-id ROW --corpus PATH`):

```json
{
  "success": true,
  "mode": "row",
  "dry_run": false,
  "row_id_hash": "abc123...64-hex",
  "salt_source": "per_dir",
  "corpus_path": "/work/train.jsonl",
  "matches": 1,
  "first_line": 42,
  "bytes_freed": 142,
  "warnings": []
}
```

**Satır-silme başarı zarfı — dry run:** aynı şekil eksi `bytes_freed` (rewrite atlanır); `warnings: []` ve `dry_run: true`.

**Run-silme başarı zarfı — wet run** (`--run-id RUN --kind {staging,artefacts}`):

```json
{
  "success": true,
  "mode": "run",
  "kind": "staging",
  "dry_run": false,
  "run_id": "fg-abc123",
  "deleted": ["/work/output/final_model.staging.fg-abc123"],
  "bytes_freed": 1048576
}
```

**Run-silme başarı zarfı — dry run:** `deleted` yerine `would_delete` (aynı liste-of-paths şekli); `bytes_freed`'i çıkar.

**Check-policy başarı zarfı — retention block configured** (`--check-policy [--config PATH]`):

```json
{
  "success": true,
  "violations": [
    {
      "artefact_kind": "staging_dir[fg-abc123]",
      "path": "/work/output/final_model.staging.fg-abc123",
      "age_days": 14.7,
      "horizon_days": 7,
      "age_source": "audit"
    }
  ],
  "count": 1
}
```

**Check-policy başarı zarfı — retention block yok** (operatörün config'i `retention:` block'unu atladı veya `--config` verilmedi): zarf `count`'u düşürür ve no-op'u açıklayan bir `note` field ekler:

```json
{
  "success": true,
  "violations": [],
  "note": "No `retention:` block in the loaded config; nothing to enforce.  See `docs/guides/gdpr_erasure.md` for the schema."
}
```

| Anahtar | Tip | Notlar |
|---|---|---|
| `success` | bool | Başarılı operasyon için `true`; config / runtime hatasında `false` (`error` ile). |
| `mode` (row/run) | str | Discriminator; `"row"` veya `"run"`. `--check-policy`'de yok. |
| `kind` (run) | str | `"staging"` veya `"artefacts"`. Sadece run mode. |
| `dry_run` | bool | `--dry-run` flag'ini yansıtır. Row + run zarflarında mevcut; `--check-policy`'de yok (mode tanım gereği read-only). |
| `row_id_hash` | str | `salt + raw_value`'nun 64-karakter küçük-harfli hex SHA-256 digest'i. Cleartext değer hiçbir zaman zarfta yer almaz. **Not:** digest plain hex olarak emit edilir (`sha256:` öneki YOK) — tüketiciler `hashlib.sha256(...).hexdigest()` ile doğrudan `==` karşılaştırma yapabilsin diye. |
| `salt_source` | str | `FORGELM_AUDIT_SECRET` toggle'ına göre `"per_dir"` veya `"env_var"`. |
| `corpus_path` | str | Operatörün `--corpus` ile geçtiği JSONL corpus'un absolute path'i. Sadece row mode. |
| `matches` | int | `--row-id` ile eşleşen satırların sayısı. Sadece row mode. |
| `first_line` | int | İlk eşleşen satırın 1-tabanlı satır numarası, rewrite *öncesi* yakalanmış. Sadece row mode. |
| `bytes_freed` | int | Silme tarafından geri kazanılan byte. Wet run'larda mevcut (row + run); dry run'larda yok. |
| `warnings` | list[str] | Row erasure ile birlikte emit edilen `data.erasure_warning_*` audit event isimleri (memorisation / synthetic-data / external-copies). Sadece row mode. |
| `would_delete` (run dry) / `deleted` (run wet) | list[str] | Run mode: dispatcher'ın silmek için hedeflediği path'ler. Dry vs wet'te farklı anahtar — tüketiciler şekle göre branch'leyebilsin diye. |
| `violations` | list[object] | Sadece `--check-policy`. `artefact_kind` şunlardan biri: `audit_log`, `staging_dir`, `staging_dir[<run_id>]`, `compliance_bundle`, `data_audit_report`, `raw_documents[...]`. `age_source` ∈ `{audit, mtime}`. |
| `count` | int | Sadece `--check-policy` ve `retention:` block configured ise; `len(violations)`'a eşit. No-retention-block dalında yok (onun yerine `note` ekler). |
| `note` | str | Sadece `--check-policy` ve `retention:` block configured DEĞİLSE. GDPR guide'a işaret eden operator-facing tek-satır. |

**Exit kodu:** `0` = başarı veya başarılı policy raporu; `1` = config hatası (bilinmeyen satır, eksik corpus, çelişen flag, malformed `--check-policy --config`); `2` = runtime hatası (I/O, atomic rename başarısız).

## `forgelm reverse-pii`

Wave 3 Phase 38 — GDPR Madde 15 erişim hakkı, `forgelm purge`'ün companion'ı. JSONL corpora'larını dolaşır ve sağlanan identifier'ın görüldüğü her satırı raporlar.

**Başarı zarfı:**

```json
{
  "success": true,
  "query_hash": "abc...64-hex",
  "identifier_type": "email",
  "scan_mode": "plaintext",
  "salt_source": "per_dir",
  "matches": [
    {
      "file": "/work/data/train.jsonl",
      "line": 42,
      "snippet": "…Contact: alice@example.com regarding the appointment…"
    }
  ],
  "files_scanned": [
    {"path": "/work/data/train.jsonl", "match_count": 1}
  ],
  "match_count": 1
}
```

| Anahtar | Tip | Notlar |
|---|---|---|
| `query_hash` | str | `salt + raw --query`'nin 64-karakter küçük-harfli hex SHA-256 digest'i — `salt`, `forgelm purge`'ün `target_id` için de kullandığı per-output-dir audit salt'ı.  Raw değer hiçbir zaman zarfta yer almaz; purge'ün `target_id`'si ile cross-tool korelasyon hazır gelir. |
| `identifier_type` | str | `--type` echo'su ∈ `{literal, email, phone, tr_id, us_ssn, iban, credit_card, custom}`.  Varsayılan `literal` — query literal substring olarak eşleştirilir. |
| `scan_mode` | str | `"plaintext"` (varsayılan — verbatim arama; mask-leak detection) veya `"hash"` (`--salt-source` set; corpus'ta `salt + query`'nin SHA-256'sı aranır). |
| `salt_source` | str | Hangi salt-resolution path kullanıldı: `"plaintext"` (`--salt-source` yok), `"per_dir"` (yalnız per-output-dir salt dosyası) veya `"env_var"` (`FORGELM_AUDIT_SECRET` ile XOR).  Audit event'inde de yansıtılır; bir compliance reviewer iki digest'in aynı şekilde salt'lanıp salt'lanmadığını anlayabilir. |
| `matches` | list[object] | Eşleşen her satır için bir entry.  `snippet` eşleşen span'in etrafında merkezlenip 160 karaktere kapanır (`…` ayraç); eşleşen identifier her zaman pencere içinde kalır. |
| `files_scanned` | list[object] | Glob-resolution sırasında per-file match sayıları.  Hangi path'lerin tarandığının kaynağı budur. |
| `match_count` | int | `len(matches)`'a eşit; "match var mı yok mu" branch'leyen CI gate'leri için convenience. |

**Exit kodu:** `0` = scan tamamlandı (matches listesi boş olabilir); `1` = config hatası (boş `--query`, `--type custom` malformed regex, empty glob expansion, `FORGELM_AUDIT_SECRET` olmadan `--salt-source env_var` — veya `FORGELM_AUDIT_SECRET` set iken `--salt-source per_dir`); `2` = runtime hatası (mid-scan I/O failure, malformed UTF-8 corpus, custom-regex ReDoS timeout, `--audit-dir` explicit verildiğinde audit-init başarısızlığı).

## `forgelm cache-models`

Wave 2b Phase 35 — air-gap workflow blocker. HuggingFace Hub cache'ini önceden doldurur.

**Başarı zarfı** (`forgelm cache-models --model M [--safety S] [--output DIR]`):

```json
{
  "success": true,
  "models": [
    {
      "name": "meta-llama/Llama-3.2-3B",
      "cached_path": "/work/hf_cache/models--meta-llama--Llama-3.2-3B",
      "size_bytes": 3221225472,
      "size_mb": 3072.0,
      "duration_s": 142.7
    }
  ],
  "total_size_mb": 3072.0,
  "cache_dir": "/work/hf_cache"
}
```

| Anahtar | Tip | Notlar |
|---|---|---|
| `models` | list[object] | Her `--model` için bir entry; `--safety` (verildiyse) son entry olarak görünür. |
| `models[].cached_path` | str | `huggingface_hub.snapshot_download`'un döndüğü path (operatörün `--output`'u veya env-resolved `HF_HUB_CACHE`). |
| `total_size_mb` | float | Tüm `models[].size_mb`'in toplamı. |
| `cache_dir` | str | Operatörün `--output`'u veya env-resolved (`HF_HUB_CACHE > HF_HOME/hub > ~/.cache/huggingface/hub`). |

**Exit kodu:** `0` = her model cache'lendi; `1` = config hatası (no `--model`, malformed isim); `2` = runtime hatası (Hub failure, disk-full, broken environment / eksik core dep).

## `forgelm cache-tasks`

Wave 2b Phase 35 — lm-evaluation-harness task dataset cache'ini önceden doldurur. `[eval]` extra'sı gerekir.

**Başarı zarfı** (`forgelm cache-tasks --tasks CSV [--output DIR]`):

```json
{
  "success": true,
  "tasks": [
    {"name": "hellaswag", "cached": true, "error": null},
    {"name": "arc_easy", "cached": true, "error": null}
  ],
  "cache_dir": "/work/datasets_cache"
}
```

| Anahtar | Tip | Notlar |
|---|---|---|
| `tasks` | list[object] | Her task için bir entry; `cached: false` + non-null `error` per-task best-effort'tür (batch devam eder). |
| `cache_dir` | str | Operatörün `--output`'u veya env-resolved (`HF_DATASETS_CACHE > HF_HOME/datasets > ~/.cache/huggingface/datasets` — Hub chain'inden ayrı *Datasets* chain'idir). |

**Exit kodu:** `0` = enumeration başarılı (per-task download hataları `tasks[].error`'da raporlanır ama batch'i fail etmez); `1` = config hatası (boş `--tasks`, bilinmeyen task, eksik `[eval]` extra); `2` = runtime hatası (broken environment / mid-batch failure).

## `forgelm safety-eval`

Wave 2b Phase 36 — bir model checkpoint'ine karşı standalone safety evaluation.

**Başarı zarfı** (`forgelm safety-eval --model M {--probes JSONL | --default-probes}`):

```json
{
  "success": true,
  "model": "/work/final_model",
  "classifier": "meta-llama/Llama-Guard-3-8B",
  "probes": "/work/probes.jsonl",
  "output_dir": "/work/eval",
  "passed": true,
  "safety_score": 0.93,
  "safe_ratio": 0.95,
  "category_distribution": {"S1": 0, "S2": 1, "S3": 0},
  "failure_reason": null
}
```

| Anahtar | Tip | Notlar |
|---|---|---|
| `success` | bool | `passed`'i yansıtır. `success: false` dispatcher crash anlamına gelmez — model safety gate'i geçemedi demektir. |
| `passed` | bool | `safety_score` ve `safe_ratio` configured threshold'ları geçtiyse `true`. |
| `safety_score` | float \| null | `forgelm.safety.run_safety_evaluation`'dan agregate skor. |
| `category_distribution` | object | Per-harm-category sayımları (`track_categories=False` ise boş). |
| `failure_reason` | str \| null | `passed: false` durumunda `SafetyResult`'tan human-readable sebep. |

**Exit kodu:** `0` = threshold'lar geçildi; `1` = dispatcher'ın ulaştığı config hatası (GGUF model yolu, probes dosyası eksik/okunamıyor); `2` = argparse kullanım hatası (eksik `--model`, `--probes`/`--default-probes` ikisinin de eksikliği ya da ikisinin birden verilmesi — ikisinden tam olarak biri zorunlu olduğundan argparse dispatcher çalışmadan önce exit 2 ile reddeder) **veya** runtime hatası (model load failure, classifier load failure, broken environment); `3` = `EXIT_EVAL_FAILURE` — evaluation tamamlandı ama safety gate hayır dedi (operator-actionable: re-train veya re-classify).

## `forgelm verify-annex-iv`

Wave 2b Phase 36 — EU AI Act Annex IV §1-9 artefact bütünlük kontrolü.

**Başarı zarfı** (`forgelm verify-annex-iv PATH`):

```json
{
  "success": true,
  "path": "/work/output/compliance/annex_iv_metadata.json",
  "valid": true,
  "missing_fields": [],
  "manifest_hash_actual": "abcd1234...",
  "manifest_hash_expected": "abcd1234...",
  "manifest_hash_present": true,
  "reason": ""
}
```

| Anahtar | Tip | Notlar |
|---|---|---|
| `valid` | bool | Tüm 9 §1-9 field'ı mevcutsa VE (`metadata.manifest_hash` mevcutsa) yeniden hesaplanan hash eşleşirse `true`. |
| `missing_fields` | list[str] | Eksik / boş `_ANNEX_IV_REQUIRED_FIELDS`'ın isimleri. |
| `manifest_hash_actual` | str \| null | Artefact-minus-metadata'nın yeniden hesaplanan canonical SHA-256'sı. |
| `manifest_hash_expected` | str \| null | Artefact'in `metadata.manifest_hash` field'ından çıkarılan değer. |
| `manifest_hash_present` | bool | Artefact hash taşımıyorsa `false` (eski export — verifier warning ile geçer). |
| `reason` | str | `valid: true` ise boş; aksi halde tek-satır failure açıklaması. |

**Exit kodu:** `0` = `valid: true`; `1` = `valid: false` (eksik field veya hash mismatch — auditor-facing rejection); `2` = runtime hatası (file not found, unreadable, malformed JSON).

## `forgelm verify-gguf`

Wave 2b Phase 36 — GGUF model dosyası bütünlük kontrolü.

**Başarı zarfı** (`forgelm verify-gguf PATH`):

```json
{
  "success": true,
  "path": "/work/exports/model.q4_k_m.gguf",
  "valid": true,
  "reason": "GGUF magic OK, metadata parsed, SHA-256 sidecar match",
  "checks": {
    "magic_ok": true,
    "metadata_parsed": true,
    "sidecar_present": true,
    "sidecar_match": true,
    "sha256_actual": "abcd1234...",
    "sha256_expected": "abcd1234..."
  }
}
```

| Anahtar | Tip | Notlar |
|---|---|---|
| `valid` | bool | Magic header geçerli VE *denenen* her check (metadata block, SHA-256 sidecar) başarılıysa `true`. Skipped bir check (örn. opsiyonel `gguf` paketi yoksa `metadata_parsed: false`) `valid: false`'a zorlamaz — sadece *denenip-failed* check zorlar. `tests/test_verification_toolbelt.py` bu sözleşmeyi pin'liyor: `checks.metadata_parsed == false` iken `valid == true` olabilir (paket-eksik yolu). |
| `checks.magic_ok` | bool | İlk 4 byte `b"GGUF"`'ya eşit. |
| `checks.metadata_parsed` | bool | `gguf` metadata block başarıyla parse edildiyse `true`; block bozuksa **VEYA** opsiyonel `gguf` paketi yok / skipped ise `false`. Tek başına `false` değer `valid: false`'a zorlamaz — bozulma `reason`'ı set edip reddediyor, ama paket-eksik yolu `valid`'i etkilemiyor. |
| `checks.sidecar_present` | bool | `<path>.sha256` mevcutsa `true`. |
| `checks.sidecar_match` | bool \| null | Byte-for-byte eşleşmede `true`; mismatch veya malformed sidecar'da `false`; sidecar yoksa `null`. *Malformed* sidecar (empty / non-hex / yanlış uzunluk) fail-closed olur. |
| `reason` | str | Tek-satır özet; `valid: false` durumunda failure detayını taşır. |

**Exit kodu:** `0` = `valid: true`; `1` = `valid: false` (magic mismatch, metadata block *bozuk*, SHA-256 mismatch, malformed sidecar); `2` = runtime hatası (file not found, unreadable). Opsiyonel-`gguf`-paketi-eksik yolu `valid: true` + exit `0` olarak kalır (operatörün "metadata check skipped" durumu — magic header + SHA-256 sidecar checks load-bearing integrity yüzeyi olmaya devam eder).

## `forgelm verify-integrity`

Wave 2b Phase 36 / Madde 15 — model dizini artefakt bütünlük kontrolü.

**Başarı zarfı** (`forgelm verify-integrity MODEL_DIR`):

```json
{
  "success": true,
  "valid": true,
  "reason": "All 12 recorded artifact(s) present and unchanged.",
  "changed": [],
  "removed": [],
  "added": [],
  "verified_count": 12,
  "path": "/work/output/my-model"
}
```

**Uyuşmazlık / operatör-hatası zarfı** (`valid: false`, çıkış 1):

```json
{
  "success": false,
  "valid": false,
  "reason": "Model artifacts do not match model_integrity.json: 1 changed, 1 removed.",
  "changed": ["adapter_model.safetensors"],
  "removed": ["tokenizer.model"],
  "added": [],
  "verified_count": 10,
  "path": "/work/output/my-model"
}
```

**Runtime-hatası zarfı** (çıkış 2):

```json
{
  "success": false,
  "error": "Could not verify model integrity for '/work/output/my-model': [Errno 13] Permission denied: '...'"
}
```

| Anahtar | Tip | Not |
|---|---|---|
| `success` | bool | `valid: true` iken `true`; herhangi bir uyuşmazlık veya hatada `false`. |
| `valid` | bool | Her kayıtlı artefakt mevcut ve SHA-256'sı eşleşiyorsa `true`; aksi hâlde `false`. |
| `reason` | str | Tek-satır özet — başarıda temiz artefakt sayısı, başarısızlıkta uyuşmazlık sayıları. |
| `changed` | list[str] | SHA-256'sı artık manifest ile eşleşmeyen artefaktların göreli yolları. |
| `removed` | list[str] | Manifest'te kayıtlı ama diskte olmayan artefaktların göreli yolları. |
| `added` | list[str] | Diskte olup manifest'te kayıtlı olmayan dosyaların göreli yolları. |
| `verified_count` | int | Manifest ile başarıyla eşleşen artefakt sayısı. |
| `path` | str | Doğrulanan model dizininin mutlak yolu. |

**Exit kodu eşlemesi:** `0` = tüm kayıtlı artefaktlar mevcut ve değişmemiş (`valid: true`); `1` = bütünlük uyuşmazlığı (changed / removed / added dosya) **veya** operatör / girdi hatası (eksik yol, yolun dizin yerine dosya olması, manifest bulunamadı, malformed JSON, list olmayan `artifacts`, manifest girdi yolunun model dizininden kaçması); `2` = erişilebilir bir yolda gerçek runtime I/O hatası (okuma hatası, yürüyüş sırasında izin reddi).

Runtime-hatası zarfı (`çıkış 2`) yalnızca `{"success": false, "error": "…"}` döndürür — `valid`, `changed`, `removed`, `added` veya `path` anahtarları olmadan. Önce `success` üzerinden dallanın, ardından `valid` ve diff listelerini inceleyin.

## `forgelm export`

Eğitim sonrası devir için GGUF / birleştirilmiş-ağırlık export'u.

**Envelope** (`forgelm export MODEL --output OUT.gguf`):

```json
{
  "success": true,
  "output_path": "/work/exports/model.q4_k_m.gguf",
  "format": "gguf",
  "quant": "q4_k_m",
  "sha256": "abcd1234...",
  "size_bytes": 4815162342,
  "error": null
}
```

| Anahtar | Tip | Not |
|---|---|---|
| `output_path` | str \| null | Yazılan artefakt yolu; başarısızlıkta `null`. |
| `format` | str \| null | Export formatı (örn. `gguf`, `merged`). |
| `quant` | str \| null | Kullanılan quantizasyon seviyesi (tek değer — `--quant` çağrı başına tek seviye alır). |
| `sha256` | str \| null | Yazılan artefakt üzerinde SHA-256; başarısızlıkta `null`. |
| `size_bytes` | int \| null | Artefakt boyutu (byte); başarısızlıkta `null`. |
| `error` | str \| null | Başarıda `null`; başarısızlıkta operatöre yönelik mesaj. `error` varlığına değil, `success`'e göre dallanın. |

**Exit kodu:** `0` = export başarılı; `2` = runtime hatası (dönüşüm hatası, eksik `[export]` extra'sı, erişilemeyen model yolu).

## `forgelm deploy`

Ollama / vLLM / TGI / HF Endpoints için deployment-config üretimi.

**Envelope** (`forgelm deploy MODEL --target ollama --output Modelfile`):

```json
{
  "success": true,
  "target": "ollama",
  "output_path": "/work/deploy/Modelfile",
  "error": null
}
```

| Anahtar | Tip | Not |
|---|---|---|
| `target` | str \| null | Deployment hedefi (`ollama`, `vllm`, `tgi`, `hf-endpoints`). |
| `output_path` | str \| null | Üretilen config yolu; başarısızlıkta `null`. |
| `error` | str \| null | Başarıda `null`; başarısızlıkta operatöre yönelik mesaj. `error` varlığına değil, `success`'e göre dallanın. |

**Exit kodu:** `0` = config üretildi; `1` = çağıran-girdisi hatası (desteklenmeyen hedef, `ollama`/`tgi` hedeflerinde `model_path`'in yerel bir dizin olmaması); `2` = runtime hatası (yazılamayan çıktı yolu, şablon render hatası).

## `forgelm quickstart`

Şablon → config → eğitim → chat tek-seferlik akış. Üç JSON şekli.

**Şablon listesi** (`forgelm quickstart --list --output-format json`):

```json
{
  "success": true,
  "templates": [
    {
      "name": "customer-support",
      "title": "Customer support assistant",
      "description": "Fine-tune a helpful support agent.",
      "primary_model": "Qwen/Qwen2.5-7B-Instruct",
      "fallback_model": "HuggingFaceTB/SmolLM2-1.7B-Instruct",
      "trainer_type": "sft",
      "estimated_minutes": 20,
      "min_vram_for_primary_gb": 16,
      "bundled_dataset": "customer_support.jsonl",
      "license_note": null
    }
  ],
  "count": 1
}
```

**Üretim başarısı** (`forgelm quickstart TEMPLATE [--dry-run] --output-format json`):

```json
{
  "success": true,
  "template": "customer-support",
  "config_path": "/work/quickstart/config.yaml",
  "model": "Qwen/Qwen2.5-7B-Instruct",
  "dataset": "/work/quickstart/customer_support.jsonl",
  "selection_reason": "primary model fits available VRAM",
  "dry_run": false,
  "chat_launched": false,
  "notes": []
}
```

**Eğitim başarısızlığı** (eğitim subprocess'i non-zero döndü):

```json
{
  "success": false,
  "error": "Training subprocess failed with exit code 3.",
  "exit_code": 3,
  "template": "customer-support",
  "config_path": "/work/quickstart/config.yaml"
}
```

| Anahtar | Tip | Not |
|---|---|---|
| `templates` | list[object] | Yalnızca `--list`. Kayıtlı her şablon için bir giriş; `count == len(templates)`. |
| `template` | str | Seçilen şablon adı. |
| `config_path` | str | Üretilen YAML config'in yolu. |
| `model` | str | Çözümlenen model id'si (primary veya VRAM kaynaklı fallback). |
| `dataset` | str | Paketlenmiş / override edilmiş veri kümesinin yolu. |
| `selection_reason` | str | `model`'in neden seçildiği. |
| `dry_run` | bool | `--dry-run` olduğunda `true` (config yazılır, eğitim yok). |
| `chat_launched` | bool | JSON modunda her zaman `false` — etkileşimli chat REPL'i bastırılır ki insan metni stdout'taki JSON envelope ile araya girmesin. `forgelm chat <model_path>`'i manuel çalıştırın. |
| `notes` | list[str] | Tavsiye notları (ör. lisans uyarıları). |
| `exit_code` | int | Yalnızca eğitim-başarısızlığı envelope'u — eğitim subprocess'inin döndürdüğü, eşlenmiş public exit kodu. |

**Exit kodu:** `0` = üretildi (ve, `--no-chat` olmadan text modunda, chat REPL'i çalıştı); `1` = çağıran-girdisi hatası (eksik/geçersiz şablon, yazılamayan çıktı dizini); auto-train subprocess'i başarısız olduğunda eğitim-başarısızlığı envelope'unun `exit_code`'u (`2`/`3`/`4`) aynen iletilir.

## Yeni subcommand eklerken

`--output-format json` destekleyen yeni bir subcommand şunlarla landed olmalıdır:

1. Bu sayfada belgelenen envelope (EN + TR mirror).
2. Üst seviye anahtarların tam setini pin'leyen `tests/test_json_envelope_contract.py` (veya per-subcommand test dosyası) testi.
3. Per-collection anahtar "sonuçlar subcommand'in birincil ismine göre adlandırılmış bir anahtar altında yaşar" konvansiyonunu izler (yani `doctor` → `checks`, `approvals --pending` → `pending` vb.).

Merge sonrası bir anahtarı yeniden adlandırmak [GitHub'daki release standardı](https://github.com/HodeTech/ForgeLM/blob/main/docs/standards/release.md) gereği MAJOR-version bump'tır.

## Ayrıca bakın

- [Exit Kodları](#/reference/exit-codes) — `success: bool`'ın aligned olduğu kontrat.
- [`error-handling.md`](https://github.com/HodeTech/ForgeLM/blob/main/docs/standards/error-handling.md) — hata envelope kontratı (GitHub kaynağı).
- [`release.md`](https://github.com/HodeTech/ForgeLM/blob/main/docs/standards/release.md) — JSON yeniden adlandırmaları ne zaman breaking sayılır (GitHub kaynağı).
