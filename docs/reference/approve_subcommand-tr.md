# `forgelm approve` / `forgelm reject` — Subcommand Referansı

> **Hedef kitle:** EU AI Act Madde 14 insan-gözetim kapısını ifa eden ForgeLM operatörleri ve ortaya çıkan `human_approval.granted` / `human_approval.rejected` audit satırını doğrulayan denetçiler.
> **Ayna:** [approve_subcommand.md](approve_subcommand.md)

`forgelm approve` ve `forgelm reject`, **EU AI Act Madde 14** insan-gözetim terminal-karar subcommand'larıdır (Phase 9). Eğitim koşusu kod 4 (`EXIT_AWAITING_APPROVAL`) ile çıktığında, diskte `final_model.staging/` ve zincirde `human_approval.required` event'iyle duraklar; yetkili reviewer ardından `approve` (terfi için) veya `reject` (atmak için) çalıştırır.

Listeleme tamamlayıcısı için bkz. [`approvals_subcommand.md`](approvals_subcommand-tr.md). Deployer akışı (CI koşusu 4 ile çıkar → reviewer'a haber gider → CLI çağrısı → audit) için bkz. [`../guides/human_approval_gate.md`](../guides/human_approval_gate-tr.md).

## Synopsis

```text
forgelm approve  run_id --output-dir DIR [--comment TEXT]
                        [--output-format {text,json}]

forgelm reject   run_id --output-dir DIR [--comment TEXT]
                        [--output-format {text,json}]
```

İki subcommand da **positional `run_id`** alır (`--run-id` flag'i YOK). Bu, `forgelm/cli/subcommands/_approve.py` içindeki CLI yüzeyiyle ve [`../qms/access_control.md`](../qms/access_control-tr.md) §6'daki `forgelm approve <run-id>` cookbook'uyla eşleşir.

| Argüman / flag | Zorunlu | Açıklama |
|---|---|---|
| `run_id` (positional) | evet | `human_approval.required` event'iyle yayılan run id (örn. `fg-abc123def456`). |
| `--output-dir DIR` | evet | `audit_log.jsonl` ve `final_model.staging/` içeren training output dizini. |
| `--comment TEXT` | hayır | Granted / rejected event'ına yazılan opsiyonel reviewer yorumu. `reject` üzerinde önerilir; auditor gerekçeyi görsün. |
| `--output-format {text,json}` | hayır (varsayılan `text`) | `json`, CI tüketicileri için stdout'a tam olarak bir yapısal nesne yazdırır. |

## `approve` ne yapar

`forgelm.cli.subcommands._approve._run_approve_cmd` içinde uygulanır:

1. `audit_log.jsonl`'in okunabilir olduğunu doğrular.
2. `_find_human_approval_required_event` üzerinden `run_id` için eşleşen `human_approval.required` event'ini bulur.
3. Aynı `run_id` için terminal karar (`granted` / `rejected`) zaten varsa reddeder (`_find_human_approval_decision_event`) — re-approve'a izin yok.
4. Event'teki `staging_path`'in `output_dir` **içinde** çözüldüğünü doğrular (`_staging_path_inside_output_dir` defence-in-depth — HMAC olmadan tampered audit log absolute veya `..`-traversal path planting'i yapabilirdi).
5. Atomik rename'den ÖNCE `AuditLogger(output_dir, run_id=run_id)` kurar; böylece operatör-kimliği çözümlemesinden gelen `EXIT_CONFIG_ERROR`, terfi etmiş bir modeli `granted` event'i olmadan bırakmaz (Madde 12 kayıt-tutma bütünlüğü).
6. `final_model.staging[.<run_id>]/`'i `final_model/`'e atomik olarak rename eder.
7. `gate="final_model"`, `run_id`, `approver` (`_resolve_approver_identity` ile çözüldü), `comment`, `promote_strategy` taşıyan `human_approval.granted`'i yayar.
8. `notify_success` webhook yaşam döngüsü event'ini fırlatır.

## `reject` ne yapar

`forgelm.cli.subcommands._approve._run_reject_cmd` içinde uygulanır:

1. `approve` ile aynı audit-log / required-event / önceki-karar-yok doğrulaması.
2. **Staging dizinini korur**; reddedilen artefact'lar forensik inceleme için erişilebilir kalır.
3. `gate="final_model"`, `run_id`, `approver`, `comment`, `staging_path` taşıyan `human_approval.rejected`'i yayar.
4. `notify_failure` webhook yaşam döngüsü event'ini fırlatır.

Staging dizini **silinmez** — operatörler red kaydı zincire girdikten sonra `forgelm purge --run-id <id> --kind staging` ile explicit olarak temizler.

## Operatör kimliği (`FORGELM_OPERATOR`)

İki subcommand da onaylayanın kimliğini `forgelm.cli.subcommands._approve._resolve_approver_identity` üzerinden çözer:

1. `FORGELM_OPERATOR` env var (en yüksek öncelik — explicit operatör tanımlama).
2. `getpass.getuser()` (OS-raporlu kullanıcı adı).
3. İkisi de başarısızsa `"anonymous"`.

**Madde 14 segregation of duties.** Onaylayanın `FORGELM_OPERATOR`'ı eğiticininkinden farklı OLMALIDIR (ISO 27001:2022 A.5.3, SOC 2 CC1.5). ForgeLM farkı zorunlu kılmaz — bu deployer-tarafı IdP kontrolüdür — ancak audit zinciri her ikisini de kaydeder; auditor [`../qms/access_control.md`](../qms/access_control-tr.md) §6'daki `jq -rs` cookbook'uyla ihlalleri tespit edebilir:

```bash
jq -rs '
    (map(select(.event == "training.started"))) as $trainers |
    map(select(.event == "human_approval.granted"))[] |
    . as $a |
    $trainers[] |
    select(.run_id == $a.run_id and .operator == $a.operator) |
    [.run_id, .operator] | @tsv
' ./outputs/audit_log.jsonl
```

Yazdırılan herhangi bir satır segregation-of-duties ihlalidir.

## Yayılan audit event'leri

İki event de [`audit_event_catalog.md`](audit_event_catalog-tr.md)'deki ortak zarfı taşır. Katalog satırları kolaylık için burada da listelenir.

| Event | Ne zaman yayılır | Anahtar payload |
|---|---|---|
| `human_approval.granted` | Operatör duraklatılan kapıyı `forgelm approve` ile onayladı. | `gate`, `approver`, `comment`, `run_id`, `promote_strategy` |
| `human_approval.rejected` | Operatör duraklatılan kapıyı `forgelm reject` ile reddetti. | `gate`, `approver`, `comment`, `run_id`, `staging_path` |

Eşleşen `human_approval.required` event'i kapı ilk açıldığında trainer tarafından yayılır (`gate`, `reason`, `metrics`, `staging_path`, `run_id` taşır).

## Exit kodları

| Kod | Anlamı |
|---|---|
| 0 | Karar kaydedildi; `approve` üzerinde staging dizini `final_model/`'e terfi etti. |
| 1 | Config hatası: `audit_log.jsonl` okunamaz veya bozuk, `run_id` için eşleşen `human_approval.required` event'i yok, önceki terminal karar zaten mevcut (re-approve / re-reject blok'lu), `staging_path` `output_dir` dışına kaçıyor, `final_model/` zaten var (terfi edilemez), staging dizini eksik, `FORGELM_OPERATOR` çözülemiyor (`AuditLogger`'dan `ConfigError`). |
| 2 | Runtime hatası: atomic-rename başarısızlığı (`os.replace` sırasında `OSError`). |

Kod 3 (`EXIT_EVAL_FAILURE`) ve 4 (`EXIT_AWAITING_APPROVAL`) bu subcommand'ın yüzeyinin parçası değildir — kod 4 zaten operatörü buraya getiren input sinyalidir.

## JSON çıktı zarfı

`approve` (başarı) — `_run_approve_cmd`'den yayılır:

```json
{
  "success": true,
  "run_id": "fg-abc123def456",
  "approver": "alice@acme.example",
  "final_model_path": "outputs/run42/final_model",
  "promote_strategy": "rename"
}
```

`reject` (başarı) — `_run_reject_cmd`'den yayılır:

```json
{
  "success": true,
  "run_id": "fg-abc123def456",
  "approver": "alice@acme.example",
  "staging_path": "outputs/run42/final_model.staging.fg-abc123def456",
  "comment": "S5'te eşik kayması; daha sıkı regresyon toleransıyla yeniden eğitin."
}
```

Hata (her ikisi, `_output_error_and_exit` tarafından):

```json
{
  "success": false,
  "error": "Run 'fg-abc123def456' already has a terminal decision ('human_approval.granted'). Refusing to record another decision — re-approve is not allowed."
}
```

> **Alan-düzeyi notlar.** `approve`, `comment`'ı JSON zarfında **yansıtmaz** (yorum, stdout'a değil, `human_approval.granted` audit event payload'ına yazılır). `reject`, `--comment` verilmediğinde boş string yansıtır. `promote_strategy` aynı-cihaz promote'ta `"rename"`, cross-device fallback'te (`shutil.move`) `"move"`; audit event payload'ı bu değeri aynalar.

## Bkz.

- [`approvals_subcommand.md`](approvals_subcommand-tr.md) — keşif tamamlayıcısı (`--pending` / `--show RUN_ID`).
- [`../guides/human_approval_gate.md`](../guides/human_approval_gate-tr.md) — deployer akışı.
- [`audit_event_catalog.md`](audit_event_catalog-tr.md) — zarf spec'i ile birlikte tam event sözlüğü.
- [`../qms/access_control.md`](../qms/access_control-tr.md) §6 — segregation-of-duties cookbook.
- [`../usermanuals/tr/compliance/human-oversight.md`](../usermanuals/tr/compliance/human-oversight.md) — operatör-yüzlü kullanıcı kılavuzu sayfası.
