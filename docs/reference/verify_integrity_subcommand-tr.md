# `forgelm verify-integrity` — Referans

> **Hedef kitle:** Eğitilmiş bir model dizininin, eğitim sırasında kaydedilen SHA-256 manifestiyle hâlâ eşleştiğini doğrulayan uyumluluk operatörleri ve CI kapıları (AB YZ Yasası Madde 15).
> **Ayna:** [verify_integrity_subcommand.md](verify_integrity_subcommand.md)

`verify-integrity` alt komutu, Madde 15 `model_integrity.json` manifestinin tüketici karşılığıdır. Uyumluluk export'u, model dizinindeki her dosyanın SHA-256 özetini yazar; `verify-integrity` bu manifesti geri okur, her dosyanın SHA-256'sını yeniden hesaplar ve manifest üretildikten sonra **değiştirilen**, **silinen** veya **eklenen** her bir yapıtı raporlar. CLI, kütüphane giriş noktası `forgelm.cli.subcommands._verify_integrity.verify_integrity`'ye devreder ve yapılandırılmış bir `VerifyIntegrityResult` döndürür.

## Söz dizimi

```text
forgelm verify-integrity [--output-format {text,json}]
                         [-q] [--log-level {DEBUG,INFO,WARNING,ERROR}]
                         path
```

`path` (konumsal, zorunlu) — `model_integrity.json` içeren model dizininin yolu.

## Bayraklar

| Bayrak | Varsayılan | Açıklama |
|---|---|---|
| `--output-format {text,json}` | `text` | `text` (varsayılan) `OK:` / `FAIL:` ile birlikte dosya bazlı dökümü yazar; `json` ise tam `VerifyIntegrityResult` zarfını yazar (`{"success", "valid", "reason", "changed", "removed", "added", "verified_count", "path"}`). |
| `-q`, `--quiet` | _kapalı_ | INFO günlüklerini bastırır. |
| `--log-level {DEBUG,INFO,WARNING,ERROR}` | `INFO` | Günlük ayrıntı düzeyini ayarlar. |
| `-h`, `--help` | — | argparse yardımını gösterip çıkar. |

## Çıkış kodları

| Kod | Anlamı |
|---|---|
| `0` | Kaydedilmiş her yapıt mevcut ve SHA-256'sı değişmemiş, dizinde beklenmeyen fazladan dosya yok. |
| `1` | Çağıran / girdi hatası (yol yok, `model_integrity.json` bulunamadı veya normal dosya değil, bozuk JSON) VEYA bir bütünlük uyuşmazlığı: manifest üretildikten sonra en az bir dosya değiştirilmiş, silinmiş veya eklenmiş. Model manifestiyle eşleşmiyor. |
| `2` | Erişilebilir bir yolda gerçek çalışma-zamanı G/Ç hatası — okuma hataları, gezinme sırasında izin reddi vb. Yol erişilebilirdi ancak doğrulama sırasında okunamaz hâle geldi. |

Kodlar `forgelm/cli/subcommands/_verify_integrity.py::_run_verify_integrity_cmd` tarafından emit edilir. Açık-sözleşme semantiği `docs/standards/error-handling.md` içinde sabitlenmiştir.

## Neler denetlenir

| Denetim | Hata durumu |
|---|---|
| **Kaydedilmiş yapıt mevcut** | `model_integrity.json` içinde listelenen ancak diskte artık bulunmayan dosya → `removed`, çıkış `1`. |
| **Kaydedilmiş yapıt değişmemiş** | Yeniden hesaplanan SHA-256'sı manifestten farklı olan dosya → `changed`, çıkış `1`. |
| **Fazladan dosya yok** | Diskte olup manifeste bulunmayan dosya → `added`, çıkış `1`. Manifest dosyasının kendisi (`model_integrity.json`) bu gezinmeden hariç tutulur çünkü model yapıtlarından sonra yazılır. |

## Emit edilen audit event'leri

`forgelm verify-integrity` **salt-okunur bir doğrulayıcıdır** ve `audit_log.jsonl`'a **hiçbir** girdi emit etmez. Bütünlük-manifestinin *üretimini* (doğrulamasını değil) işaret eden olaylar çalıştırma düzeyindeki eğitim olaylarına biner; bkz. [audit_event_catalog.md](audit_event_catalog.md).

## Örnekler

### Metin çıktısı (varsayılan)

```shell
$ forgelm verify-integrity checkpoints/run/final_model
OK: checkpoints/run/final_model
  All 7 recorded artifact(s) present and unchanged.
```

### JSON çıktısı (CI tüketicileri için)

```shell
$ forgelm verify-integrity --output-format json \
    checkpoints/run/final_model
{
  "success": true,
  "valid": true,
  "reason": "All 7 recorded artifact(s) present and unchanged.",
  "changed": [],
  "removed": [],
  "added": [],
  "verified_count": 7,
  "path": "/abs/path/checkpoints/run/final_model"
}
```

### Hata: bir ağırlık dosyası eğitimden sonra değiştirildi

```shell
$ forgelm verify-integrity checkpoints/run/final_model
FAIL: checkpoints/run/final_model
  Model artifacts do not match model_integrity.json: 1 changed.
    changed: model.safetensors
$ echo $?
1
```

### Hata: eksik manifest

```shell
$ forgelm verify-integrity checkpoints/run/final_model
Integrity manifest not found: expected 'checkpoints/run/final_model/model_integrity.json' (FileNotFoundError).
$ echo $?
1
```

## Bkz.

- [`audit_event_catalog.md`](audit_event_catalog.md) — kanonik olay sözcük dağarcığı.
- [`verify_gguf_subcommand.md`](verify_gguf_subcommand.md) — export edilen GGUF dosyaları için eşlik eden doğrulayıcı.
- [`verify_annex_iv_subcommand.md`](verify_annex_iv_subcommand.md) — Annex IV teknik-dokümantasyon yapıtı için eşlik eden doğrulayıcı.
- `forgelm.cli.subcommands._verify_integrity.verify_integrity` — entegratörlerin CLI'den geçmeden doğrudan çağırdığı kütüphane giriş noktası.
