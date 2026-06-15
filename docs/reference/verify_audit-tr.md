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
| `--require-hmac` | `False` | Sıkı mod. Yapılandırılmış env var set değilse `1`, herhangi bir satırda `_hmac` alanı eksikse yine `1` ile çıkar. Her kaydın HMAC ile imzalı olması gereken regüle CI pipeline'larında kullanın. (Aşağıdaki "Çıkış kodları" tablosuna bakın — `EXIT_CONFIG_ERROR=1` v0.5.5 stabilizasyon dönemi boyunca hem seçenek hatalarını hem de bütünlük arızalarını kapsıyor; ayrı bir `EXIT_INTEGRITY_FAILURE` sabiti v0.6.x'e ertelendi.) |
| `-q`, `--quiet` | _kapalı_ | INFO loglarını bastırır. |
| `--log-level {DEBUG,INFO,WARNING,ERROR}` | `INFO` | Log ayrıntı seviyesi. |
| `-h`, `--help` | — | Argparse yardımını gösterir ve çıkar. |

## Çıkış kodları

| Kod | Anlam |
|---|---|
| `0` | `EXIT_SUCCESS` — SHA-256 zinciri (ve doğrulandığında HMAC etiketleri) uçtan uca bütün. |
| `1` | `EXIT_CONFIG_ERROR` — v0.5.5 stabilizasyon dönemi boyunca **hem** seçenek/kullanım hatalarını (eksik log dosyası, `--require-hmac` setken env var unset, hatalı satırda JSON decode hatası) **hem** de bütünlük arızalarını (zincir kopması, HMAC uyuşmazlığı, manifest uyuşmazlığı, `--require-hmac` altında eksik `_hmac` satırı) kapsar. |

> **Gelecek deprecation notu.** Ayrı bir `EXIT_VALIDATION_ERROR` / `EXIT_INTEGRITY_FAILURE` sabiti v0.6.x backlog'unda. O zamana kadar CI kapılarında `verify-audit`'ten gelen herhangi bir non-zero exit'i "bu yapımı promote etme" olarak kabul edin — dispatcher şu an seçenek hatalarını bütünlük arızalarından kod ile ayırt etmiyor. Yetkili sözleşme `_verify_audit.py` docstring'inde (`_run_verify_audit_cmd` docstring).

Kodlar dispatcher tarafından `_run_verify_audit_cmd` (`forgelm/cli/subcommands/_verify_audit.py`) satırlarından emit edilir.

## Emit edilen audit event'leri

`forgelm verify-audit` **salt-okunur bir doğrulayıcıdır** ve `audit_log.jsonl`'a **hiçbir** kayıt eklemez. Yalnızca zinciri inceler. Doğrulanan log'un *içinde* görünen event'ler [audit_event_catalog.md](audit_event_catalog-tr.md)'de kataloglanmıştır (verify-audit'in yürüdüğü `_hmac`, `prev_hash` ve `run_id` alanları için Ortak zarf satırına bakın).

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
1
```

## Bkz.

- [`audit_event_catalog.md`](audit_event_catalog-tr.md) — bu komutun doğruladığı log'un *içinde* görünen event'ler.
- [`verify_annex_iv_subcommand.md`](verify_annex_iv_subcommand-tr.md) — Annex IV teknik dokümantasyon artifact'ı için kardeş doğrulayıcı.
- [`verify_gguf_subcommand.md`](verify_gguf_subcommand-tr.md) — export edilmiş GGUF model dosyaları için kardeş doğrulayıcı.
- [Audit Log kullanım kılavuzu sayfası](../usermanuals/tr/compliance/audit-log.md) — log'un kendisine dair operatör-odaklı kılavuz.
- `forgelm.compliance.verify_audit_log` — entegratörlerin CLI'dan geçmeden doğrudan çağırdığı kütüphane giriş noktası.
