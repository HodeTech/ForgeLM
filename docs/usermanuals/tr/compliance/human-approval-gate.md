---
title: İnsan Onay Kapısı (Deployer)
description: İnsan Gözetimi'nin deployer-yüzlü tamamlayıcısı — Madde 14 için `forgelm approve` / `reject` / `approvals` CLI kapısı.
---

# İnsan Onay Kapısı (Deployer)

Bu sayfa [İnsan Gözetimi](#/compliance/human-oversight)'nin deployer-yüzlü tamamlayıcısıdır. Daha kısa olan İnsan Gözetimi sayfası operatör hızlı-referansıdır; bu sayfa kapıyı uçtan uca ayağa kaldırırken bir deployer'ın ihtiyaç duyduğu wiring detaylarını — CI entegrasyonu, görev ayrılığı, audit-kanıt doğrulama — toplar.

Tam walkthrough için bkz. [GitHub'daki insan onay kapısı deployer kılavuzu](https://github.com/HodeTech/ForgeLM/blob/main/docs/guides/human_approval_gate-tr.md). Flag-başına referanslar için bkz. GitHub'daki [`approve_subcommand-tr.md`](https://github.com/HodeTech/ForgeLM/blob/main/docs/reference/approve_subcommand-tr.md) ve [`approvals_subcommand-tr.md`](https://github.com/HodeTech/ForgeLM/blob/main/docs/reference/approvals_subcommand-tr.md).

## Kapı ne zaman ateşlenir

```yaml
evaluation:
  require_human_approval: true
```

> **Phantom anahtar notu.** Eski taslaklar bunu `compliance.human_approval` olarak adlandırıyordu. O anahtar hiç gönderilmedi — `ComplianceMetadataConfig` `extra="forbid"` ile yapılandırıldığı için onu reddeder. Kanonik aktivasyon anahtarı `EvaluationConfig` üzerindedir. Tam phantom-anahtar listesi için bkz. [İnsan Gözetimi](#/compliance/human-oversight).

Bu flag ile, bu config'i tüketen her koşum eval başarılı olduktan **sonra** ve `final_model.staging/` `final_model/`'e terfi etmeden **önce** duraklar. Trainer:

- Diske `final_model.staging.<run_id>/` yazar.
- `audit_log.jsonl`'a `human_approval.required` ekler.
- Yapılandırılmış webhook'ta `notify_awaiting_approval`'ı fırlatır.
- Kod 4 (`EXIT_AWAITING_APPROVAL`) ile çıkar.

Başarısız bir eval yine 3 (`EXIT_EVAL_FAILURE`) ile çıkar ve kapıya asla ulaşmaz.

> **Not:** Operatör strict-tier config'i `forgelm --wizard` ile üretirken iptal ederse (Ctrl-C, non-tty reddi, kaydetmeyi reddetme), wizard 5 (`EXIT_WIZARD_CANCELLED`) ile çıkar — bkz. [Çıkış Kodları](#/reference/exit-codes) — ve trainer pipeline'a hiç ulaşmaz. Yalnızca exit 0'ı "wizard temiz tamamlandı" olarak ele alan CI pipeline'ları, exit 5'i ayrı olarak ele alıp "config üretilmedi" mesajını yüzeylemeli, generic başarısızlık olarak işlememelidir.

## CI wiring

Exit kodu 4 bir **duraklamadır**, **başarısızlık** değil. CI'a bu açıkça söylenmeli:

```yaml
# .github/workflows/train.yml (excerpt)
env:
  FORGELM_OPERATOR: gha:${{ github.repository }}:${{ github.workflow }}:run-${{ github.run_id }}
  FORGELM_AUDIT_SECRET: ${{ secrets.FORGELM_AUDIT_SECRET }}
steps:
  - id: train
    run: forgelm --config run.yaml
    continue-on-error: true     # exit 4 build'i fail etmemeli
  - if: ${{ steps.train.outcome == 'success' || steps.train.exit_code == 4 }}
    run: echo "::notice::Run insan onayı bekliyor"
```

Ayrı bir kapı-keşif job'ı (veya zamanlanmış cron) `forgelm approvals --pending` çağırarak kuyruğu yüzeyselleştirir:

```bash
pending=$(forgelm approvals --pending --output-dir ./outputs --output-format json | jq '.count')
if [ "$pending" -gt 0 ]; then
    echo "::warning::$pending onay bekliyor"
fi
```

## Reviewer'ın CLI yüzeyi

```bash
forgelm approvals --pending --output-dir <dir>           # karar bekleyen koşumları listele
forgelm approvals --show <run_id> --output-dir <dir>     # tam zincir + staging içeriği
forgelm approve  <run_id> --output-dir <dir> --comment "..."  # staging → final_model'e promote
forgelm reject   <run_id> --output-dir <dir> --comment "..."  # staged modeli at
```

`approve` ve `reject` **positional `run_id`** alır (`--run-id` DEĞİL). `--comment` metni zincire kaydedilir — auditor'lar okuyacak. `--output-dir` `audit_log.jsonl` ve `final_model.staging/` içeren training output dizinini gösterir.

## Görev ayrılığı (Madde 14 + ISO A.5.3 + SOC 2 CC1.5)

Onaylayanın `FORGELM_OPERATOR`'ı trainer'ınkinden FARKLI OLMALIDIR. ForgeLM bunu zorunlu kılmaz — bu deployer-tarafı IdP kontrolüdür — ancak audit zinciri her ikisini kaydeder; ihlal [GitHub'daki `access_control.md` QMS şablonunun](https://github.com/HodeTech/ForgeLM/blob/main/docs/qms/access_control.md) §6'sındaki kanonik `jq -rs` cookbook'uyla post-hoc tespit edilebilir:

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

Yazdırılan herhangi bir satır ihlaldir. Temiz bir koşumda hiçbir şey yazdırılmaz.

Desen: CI runner'ları makine-okunabilir kimlik kullanır (`gha:Acme/pipelines:training:run-42`); insan reviewer'lar kendi kimliklerini kullanır (e-posta veya LDAP user). `FORGELM_OPERATOR`'ı reviewer'ın shell profiline veya IdP'nizin environment-injection katmanına bake edin; manuel `export`'a güvenmeyin.

## Yayılan audit event'leri

Üç event kapının tam yaşam döngüsünü tarif eder (bkz. [GitHub'daki Audit Event Kataloğu](https://github.com/HodeTech/ForgeLM/blob/main/docs/reference/audit_event_catalog.md)):

| Event | Kim yayar | Ne zaman |
|---|---|---|
| `human_approval.required` | trainer | Eval başarılı; kapı duraklatıldı. |
| `human_approval.granted` | `forgelm approve` | Reviewer onayladı; staging `final_model/`'e terfi etti. |
| `human_approval.rejected` | `forgelm reject` | Reviewer reddetti; staging forensik inceleme için korundu. |

Her satır `prev_hash` (önceki satırın SHA-256'sı) ve `FORGELM_AUDIT_SECRET` set olduğunda `_hmac` taşır. `forgelm verify-audit ./outputs/audit_log.jsonl --require-hmac` zincirin tamamını doğrular.

## Kapının kanıtını doğrulama

Auditor'lar ve self-reviewer'lar kapıyı üç adımda yürür:

```bash
# 1. Zincir bütünlüğü (HMAC-strict).
forgelm verify-audit ./outputs/audit_log.jsonl --require-hmac

# 2. Onay eşleşmesi — her required event'in eşleşen bir terminal kararı var.
jq -rs '
    (map(select(.event == "human_approval.required")) | map(.run_id)) as $req |
    (map(select(.event | startswith("human_approval.")) | select(.event != "human_approval.required")) | map(.run_id)) as $dec |
    ($req - $dec) as $unmatched |
    if ($unmatched | length) == 0 then "OK: her required event'in bir kararı var."
    else "PENDING:\n" + ($unmatched | join("\n")) end
' ./outputs/audit_log.jsonl

# 3. Görev ayrılığı (yukarıdaki §6 cookbook'u).
```

## Exit kodları

| Kod | Subcommand | Anlamı |
|---|---|---|
| 0 | hepsi | Karar kaydedildi / listeleme başarılı / kuyruk boş. |
| 1 | `approve` / `reject` | Audit log bozuk, eşleşen `required` event yok, önceki terminal karar var, `staging_path` `output_dir` dışına kaçıyor, `final_model/` zaten var, staging eksik, operatör kimliği çözülemiyor. |
| 1 | `approvals` | Audit log bozuk, ne `--pending` ne `--show` verilmiş, `--show` üzerinde bilinmeyen `run_id`. |
| 2 | hepsi | Runtime hatası (I/O, atomic-rename başarısızlığı). |
| 4 | trainer | Operatörü buraya getiren duraklama sinyali — approve/reject/approvals yüzeyinin parçası değil. |

## Sık hatalar

:::warn
**Exit 4'ü başarısızlık olarak ele almak.** Bu kontrollü bir duraklamadır. CI eğitim adımında `continue-on-error: true` (veya runner'ınızdaki eşdeğeri) kullanmalıdır.
:::

:::warn
**`approve` / `reject` için `--run-id` kullanmak.** İki subcommand da **positional** `run_id` alır. `--run-id` trainer-tarafı flag'idir (ve `forgelm purge --run-id` flag'idir); approve / reject onu adopt etmedi.
:::

:::warn
**Trainer ve onaylayan arasında `FORGELM_OPERATOR`'ı paylaşmak.** Audit zinciri her ikisini kaydeder, dolayısıyla ihlal tespit edilebilir, ama yine de ihlaldir. Audit gözlem dönemi öncesinde her audit log'a karşı §6 cookbook'unu çalıştırın.
:::

:::tip
**`forgelm approvals --pending --output-format json`'ı CI gate'i olarak kullanın.** Deploy adımını eğitim adımının exit kodu yerine boş-kuyruk check'iyle gate'leyin. Böylece ayrı bir reviewer makinesi kararı CI runner'a reviewer kimlik bilgisi gerekmeden işler.
:::

## Bkz.

- [İnsan Gözetimi](#/compliance/human-oversight) — operatör hızlı-referans tamamlayıcısı.
- [Audit Log](#/compliance/audit-log) — `human_approval.*` event'lerinin kaydedildiği yer.
- [İnsan onay kapısı deployer kılavuzu](https://github.com/HodeTech/ForgeLM/blob/main/docs/guides/human_approval_gate-tr.md) — tam deployer akışı (GitHub kaynağı).
- [`approve_subcommand-tr.md`](https://github.com/HodeTech/ForgeLM/blob/main/docs/reference/approve_subcommand-tr.md) — `approve` / `reject` flag-başına referans (GitHub kaynağı).
- [`approvals_subcommand-tr.md`](https://github.com/HodeTech/ForgeLM/blob/main/docs/reference/approvals_subcommand-tr.md) — `approvals` flag-başına referans (GitHub kaynağı).
- [`access_control.md`](https://github.com/HodeTech/ForgeLM/blob/main/docs/qms/access_control.md) §6 — kanonik segregation-of-duties cookbook (GitHub QMS şablonu).
