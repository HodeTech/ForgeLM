# `forgelm verify-audit` — Referans

> **Hedef kitle:** `forgelm verify-audit`'i yayın kapılarına bağlayan operatörler ve CI/CD pipeline'ları.
> **Ayna:** [verify_audit.md](verify_audit.md)

`verify-audit` alt-komutu, EU AI Act Madde 12 kayıt-tutma kapsamında üretilen bir ForgeLM `audit_log.jsonl` dosyasının SHA-256 hash zincirini doğrular. Operatörün `FORGELM_AUDIT_SECRET`'i ortamda set edilmişse satır başına HMAC etiketleri de doğrulanır. CLI, kütüphane giriş noktası `forgelm.compliance.verify_audit_log` etrafında ince bir dispatcher'dır (sonuç: `forgelm.compliance.VerifyResult`).

## Söz dizimi

```text
forgelm verify-audit [--hmac-secret-env VAR] [--require-hmac]
                     [-q] [--log-level {DEBUG,INFO,WARNING,ERROR}]
                     log_path
```

`log_path` (pozisyonel, zorunlu) — `audit_log.jsonl` yolu. Genesis manifest sidecar'ı (`<log_path>.manifest.json`) varsa otomatik bulunur ve çapraz kontrol edilir.

## Bayraklar

| Bayrak | Varsayılan | Açıklama |
|---|---|---|
| `--hmac-secret-env VAR` | `FORGELM_AUDIT_SECRET` | Log yazımı sırasında kullanılan HMAC sırrını taşıyan ortam değişkeninin adı. Değişken set edildiğinde satır başına `_hmac` etiketleri doğrulanır; aksi halde sadece SHA-256 zinciri kontrol edilir. |
| `--require-hmac` | `False` | Sıkı mod. Yapılandırılmış env var set değilse `1` ile çıkar (bir pre-flight seçenek hatası — doğrulayıcı hiç çalışmadı). Herhangi bir satırda `_hmac` alanı eksikse `6` ile çıkar (log okundu ve sıkı-mod doğrulamasını geçemedi — bir zincir-bütünlüğü arızası). Her kaydın HMAC ile imzalı olması gereken regüle CI pipeline'larında kullanın. |
| `-q`, `--quiet` | _kapalı_ | INFO loglarını bastırır. |
| `--log-level {DEBUG,INFO,WARNING,ERROR}` | `INFO` | Log ayrıntı seviyesi. |
| `-h`, `--help` | — | Argparse yardımını gösterir ve çıkar. |

## Çıkış kodları

| Kod | Anlam |
|---|---|
| `0` | `EXIT_SUCCESS` — SHA-256 zinciri (ve doğrulandığında HMAC etiketleri) uçtan uca bütün. |
| `1` | `EXIT_CONFIG_ERROR` — seçenek/kullanım hatası: `--require-hmac` setken yapılandırılmış env var unset, veya log yolu yok / bir dizin / çağıran-girdisi nedeniyle okunamıyor. Doğrulama hiç çalışmadı, dolayısıyla bir bütünlük kararı yok. |
| `2` | `EXIT_TRAINING_ERROR` — log var ama okunamadı (izin reddi, okuma-ortası G/Ç hatası). Tekrar denenebilir. |
| `6` | `EXIT_INTEGRITY_FAILURE` — log okundu ve zincir doğrulanmıyor: zincir kopması, HMAC uyuşmazlığı, genesis-manifest uyuşmazlığı, çözülemeyen bir satır, log içinde geçerli-olmayan UTF-8 baytları, veya (`--require-hmac` altında) `_hmac` alanı eksik bir satır. Bu tahrifat sinyalidir ve kodun var olma nedenidir — önceden kırık bir hash zinciri ile yanlış yazılmış bir yol ikisi de `1` ile çıkardı, bu yüzden bir CI pipeline'ı bir güvenlik olayını operatör yazım hatasından ayırt edemezdi. |

Kodlar dispatcher tarafından `_run_verify_audit_cmd` (`forgelm/cli/subcommands/_verify_audit.py`) satırlarından emit edilir; bu, dosyayı önce prob'lar (`_probe_log_readable`) — böylece bir *okuma* hatası doğrulayıcı çalışmadan önce 1 veya 2'ye yönlenir; bu prob başarılı olduktan sonra kütüphane giriş noktasının döndürdüğü her `valid=False` gerçek bir bütünlük kararıdır (6). `forgelm.compliance.verify_audit_log`'un kendisi zincir-seviyesi arızalar için asla exception fırlatmaz; her zaman bir `VerifyResult(valid=False, reason=...)` döndürür ve yalnızca doğrulama sırasında okunamaz hâle gelen bir dosya için `OSError` fırlatır.

## Emit edilen audit event'leri

`forgelm verify-audit` **salt-okunur bir doğrulayıcıdır** ve `audit_log.jsonl`'a **hiçbir** kayıt eklemez. Yalnızca zinciri inceler. Doğrulanan log'un *içinde* görünen event'ler [audit_event_catalog-tr.md](audit_event_catalog-tr.md)'de kataloglanmıştır (verify-audit'in yürüdüğü `_hmac`, `prev_hash` ve `run_id` alanları için Ortak zarf satırına bakın).

## Örnekler

### Yalnızca zincir doğrulama (ortamda sır yok)

```shell
$ forgelm verify-audit checkpoints/run/compliance/audit_log.jsonl
OK: 87 entries verified
```

### HMAC ile yetkilendirilmiş doğrulama

```shell
$ export FORGELM_AUDIT_SECRET="$(cat /run/secrets/audit-secret)"
$ forgelm verify-audit checkpoints/run/compliance/audit_log.jsonl
OK: 87 entries verified (HMAC validated)
```

### Sıkı CI kapısı (kurumsal denetim profili)

```shell
$ FORGELM_AUDIT_SECRET="$(cat /run/secrets/audit-secret)" \
    forgelm verify-audit --require-hmac \
        checkpoints/run/compliance/audit_log.jsonl
OK: 87 entries verified (HMAC validated)
$ echo $?
0
```

`--require-hmac` altında sır env var'ı set değilse komut `1` ile çıkar:

```shell
$ forgelm verify-audit --require-hmac checkpoints/run/compliance/audit_log.jsonl
ERROR: --require-hmac specified but $FORGELM_AUDIT_SECRET is unset.
$ echo $?
1
```

### Özel sır-env adı

Her kiracının kendi sır değişkenini taşıdığı çok-kiracılı ortamlar için:

```shell
$ TENANT_ACME_AUDIT_KEY="$(cat /run/secrets/acme-audit)" \
    forgelm verify-audit --hmac-secret-env TENANT_ACME_AUDIT_KEY \
        artifacts/acme/audit_log.jsonl
OK: 412 entries verified (HMAC validated)
```

### Tahrifat tespit hatası

```shell
$ forgelm verify-audit checkpoints/run/compliance/audit_log.jsonl
FAIL at line 53: prev_hash mismatch — chain break suggests entry was inserted, removed, or reordered
$ echo $?
6
```

## Bkz.

- [`audit_event_catalog-tr.md`](audit_event_catalog-tr.md) — bu komutun doğruladığı log'un *içinde* görünen event'ler.
- [`verify_annex_iv_subcommand.md`](verify_annex_iv_subcommand-tr.md) — Annex IV teknik dokümantasyon artifact'ı için kardeş doğrulayıcı.
- [`verify_gguf_subcommand.md`](verify_gguf_subcommand-tr.md) — export edilmiş GGUF model dosyaları için kardeş doğrulayıcı.
- [Audit Log kullanım kılavuzu sayfası](../usermanuals/tr/compliance/audit-log.md) — log'un kendisine dair operatör-odaklı kılavuz.
- `forgelm.compliance.verify_audit_log` — entegratörlerin CLI'dan geçmeden doğrudan çağırdığı kütüphane giriş noktası.
