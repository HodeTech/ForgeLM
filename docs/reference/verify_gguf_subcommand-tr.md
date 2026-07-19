# `forgelm verify-gguf` — Referans

> **Hedef kitle:** `llama.cpp`, Ollama, vLLM ya da LM Studio üzerinden servis etmeden önce export edilmiş GGUF model dosyalarını doğrulayan deployment operatörleri ve CI kapıları.
> **Ayna:** [verify_gguf_subcommand.md](verify_gguf_subcommand.md)

`verify-gguf` alt-komutu bir GGUF model dosyası üzerinde üç katmanlı bütünlük kontrolü yapar: 4-baytlık `GGUF` magic header'ını doğrular, isteğe bağlı `gguf` Python paketi yüklüyse meta veri bloğunu ayrıştırır ve mevcutsa `<path>.sha256` sidecar'ına karşı SHA-256 karşılaştırması yapar. CLI, kütüphane giriş noktası `forgelm.verify.verify_gguf`'a delegasyon yapar (paket kökünde `forgelm.verify_gguf` olarak da erişilebilir) ve yapılandırılmış bir `VerifyGgufResult` döndürür.

## Söz dizimi

```text
forgelm verify-gguf [--output-format {text,json}]
                    [-q] [--log-level {DEBUG,INFO,WARNING,ERROR}]
                    path
```

`path` (pozisyonel, zorunlu) — GGUF model dosyasının yolu. İsteğe bağlı `<path>.sha256` sidecar'ı otomatik bulunur.

## Bayraklar

| Bayrak | Varsayılan | Açıklama |
|---|---|---|
| `--output-format {text,json}` | `text` | `text` (varsayılan) `OK:` / `FAIL:` ile birlikte kontrol kırılımını yazar; `json` tüm `VerifyGgufResult` zarfını yazar (`{"success", "valid", "reason", "checks", "path"}`); `checks` `magic_ok`, `metadata_parsed`, `sidecar_present`, `sidecar_match` ve uygun olduğunda `tensor_count`, `sha256_actual`, `sha256_expected` taşır. |
| `-q`, `--quiet` | _kapalı_ | INFO loglarını bastırır. |
| `--log-level {DEBUG,INFO,WARNING,ERROR}` | `INFO` | Log ayrıntı seviyesi. |
| `-h`, `--help` | — | Argparse yardımını gösterir ve çıkar. |

## Çıkış kodları

| Kod | Anlam |
|---|---|
| `0` | Magic header `GGUF` VE (`gguf` yüklüyse) meta veri bloğu ayrıştırılıyor VE (sidecar mevcutsa) SHA-256 eşleşiyor. |
| `1` | Çağıran/girdi hatası: yol eksik veya normal bir dosya değil; magic uyuşmuyor (dosya hiç GGUF değil — bu bir tahrifat kararı değil, dosya-tipi kararıdır); bozuk sidecar (hex değil / yanlış uzunluk). Ayrıca SHA-256 sidecar'ı **eşleşen** bir dosyada ayrıştırılamayan meta veri bloğu: checksum, baytların tam olarak exporter'ın yazdığı şey olduğunu kanıtlar; dolayısıyla ayrıştırma hatası bir tahrifat olayı değil, `gguf` kütüphane-sürümü sorunudur. Ya hiçbir şey karşılaştırılmadı ya da karşılaştırılan şey temiz çıktı; her hâlükârda operatör kendi tarafını düzeltir. |
| `2` | Mevcut bir dosyada gerçek runtime I/O hatası — okuma hatası, ayrıştırma sırasında izin reddi vb. Yol `os.path.isfile`'a erişilebilirdi ama doğrulama sırasında okunamaz hâle geldi. |
| `6` | Bütünlük arızası: dosya *gerçekten* bir GGUF (magic OK) ve bütünlük kontrolünü geçemedi — uyuşmayan iyi-biçimli bir digest'e sahip SHA-256 sidecar (export sonrası değiştirilmiş) veya bozulmayı eleyecek **eşleşen bir sidecar olmadan** ayrıştırılamayan bir meta veri bloğu (kesilmiş / bozuk akış). Artefakt servis edilmek için güvenli değil. |

Kodlar `forgelm/cli/subcommands/_verify_gguf.py::_run_verify_gguf_cmd` tarafından emit edilir; bu, yapısal (asla string-eşleşmeli değil) predicate `forgelm.verify.is_gguf_integrity_failure` üzerinden yönlenir. Kamuya açık sözleşme semantiği `docs/standards/error-handling.md`'de sabitlenmiştir.

## Üç katman

| Katman | Gerekli mi? | Hata modu |
|---|---|---|
| **Magic header** | Her zaman. İlk 4 bayt `b"GGUF"` olmalı. | Aksi → çıkış `1` (dosya GGUF değil ya da indirme bozuk — tahrifat değil dosya-tipi kararı, en yaygın kapı tetiklemesi olsa bile girdi-hatası kodunda kalır). |
| **Meta veri bloğu** | İsteğe bağlı `gguf` paketi yüklüyse. Üst kaynak okuyucu ile meta veri + tensor tanımlarını ayrıştırır. | Okuyucu ayrıştırma sırasında istisna fırlatır → çıkış kodu sidecar'ın ne kanıtladığına bağlıdır; çünkü ayrıştırma hatası tek başına bozuk bir dosyayı, bu dosyanın format revizyonunu okuyamayacak kadar eski bir `gguf` paketinden ayırt edemez. **Sidecar yok** → çıkış `6` (bozulmayı eleyecek hiçbir şey yok; dosya kesilmiş / hasarlı sayılmalı). **Eşleşen sidecar** → çıkış `1` (dosya export edilenle bayt-bayt aynı — `gguf`'u yükseltip yeniden çalıştırın; artefakt sahibini çağırmayın). **Uyuşmayan sidecar** → çıkış `6`, sidecar uyuşmazlığı olarak raporlanır (checksum daha güçlü kanıttır ve baskın gelir). Paket yoksa → kontrol atlanır (magic + sidecar kontrolleri yük taşır). |
| **SHA-256 sidecar** | `<path>.sha256` mevcutsa. Dosyanın SHA-256'sını yeniden hesaplar ve sidecar'ın ilk boşluk-ayrılı token'ı ile karşılaştırır (sha256sum formatı `<hex> *<filename>` desteklenir). | İyi-biçimli ama uyuşmayan digest → çıkış `6` (dosya export sonrası değişmiş). Sidecar mevcut ama içeriği 64 karakterlik hex digest değilse → çıkış `1` (bozuk-sidecar maskelenmesine karşı kapalı başarısızlık — hiçbir şey karşılaştırılmadı). Sidecar yoksa → kontrol sessizce atlanır. |

Exporter sidecar'ı varsayılan olarak yazar (bkz. [`docs/usermanuals/tr/deployment/gguf-export.md`](../usermanuals/tr/deployment/gguf-export.md)); GGUF dosyalarını üçüncü taraflardan alan operatörler sidecar'ı da talep etmelidir.

## Emit edilen audit event'leri

`forgelm verify-gguf` **salt-okunur bir doğrulayıcıdır** ve `audit_log.jsonl`'a **hiçbir** kayıt eklemez. GGUF *üretimini* (doğrulamayı değil) işaretleyen event'ler export adımına aittir ve şu anda koşu seviyesindeki `pipeline.completed` zarfı içinde gider; bkz. [audit_event_catalog.md](audit_event_catalog-tr.md).

## Örnekler

### Metin çıktısı (varsayılan)

```shell
$ forgelm verify-gguf checkpoints/run/exports/model-q4_k_m.gguf
OK: checkpoints/run/exports/model-q4_k_m.gguf
  GGUF magic OK, metadata parsed, SHA-256 sidecar match
    magic_ok: True
    metadata_parsed: True
    sidecar_present: True
    sidecar_match: True
    tensor_count: 291
    sha256_actual: a4c1f2…
    sha256_expected: a4c1f2…
```

### JSON çıktısı (CI tüketicileri için)

```shell
$ forgelm verify-gguf --output-format json \
    checkpoints/run/exports/model-q4_k_m.gguf
{
  "success": true,
  "valid": true,
  "reason": "GGUF magic OK, metadata parsed, SHA-256 sidecar match",
  "checks": {
    "magic_ok": true,
    "metadata_parsed": true,
    "sidecar_present": true,
    "sidecar_match": true,
    "tensor_count": 291,
    "sha256_actual": "a4c1f2…",
    "sha256_expected": "a4c1f2…"
  },
  "path": "/abs/path/checkpoints/run/exports/model-q4_k_m.gguf"
}
```

### Hata: magic uyuşmazlığı

```shell
$ forgelm verify-gguf checkpoints/run/exports/wrong-file.bin
FAIL: checkpoints/run/exports/wrong-file.bin
  Magic header mismatch: expected b'GGUF', got b'PK\x03\x04'.  Not a GGUF file or corrupted.
    magic_ok: False
$ echo $?
1
```

### Hata: SHA-256 sidecar uyuşmazlığı (export-sonrası tahrifat)

```shell
$ forgelm verify-gguf checkpoints/run/exports/model-q4_k_m.gguf
FAIL: checkpoints/run/exports/model-q4_k_m.gguf
  SHA-256 sidecar mismatch — file modified after export.  Expected a4c1f2cb1d0a8e91…, got 91e2bf03c4a1c1ab….
$ echo $?
6
```

### Hata: bozuk sidecar

```shell
$ forgelm verify-gguf checkpoints/run/exports/model-q4_k_m.gguf
FAIL: checkpoints/run/exports/model-q4_k_m.gguf
  Malformed SHA-256 sidecar: expected a 64-character hex digest, got 'TODO: regenerate'.  Regenerate the sidecar (e.g. `sha256sum model.gguf > model.gguf.sha256`) or remove it to skip the check.
$ echo $?
1
```

### Meta veri ayrıştırma hatası, sidecar eşleşiyor (tahrifat değil, kütüphane-sürümü sorunu)

Meta veri ayrıştırması sidecar karşılaştırmasını bilerek kısa devre yaptırmaz: sidecar daha güçlü kanıttır ve ayrıştırma hatasının açık bıraktığı "bozulma mı, okuyucu uyumsuzluğu mu" ikilemini çözebilir. Baytların tam olarak export edilen şey olduğunu kanıtladığında karar `1`'e düşer:

```shell
$ forgelm verify-gguf checkpoints/run/exports/model-q4_k_m.gguf
FAIL: checkpoints/run/exports/model-q4_k_m.gguf
  GGUF metadata block could not be parsed: ValueError: Sorry, file appears to be version 4294967295 which we cannot handle.  The SHA-256 sidecar matches, so the file is byte-identical to what was exported — this is almost certainly a `gguf` package version that cannot read this file's format revision, not a corrupted artifact.  Upgrade `gguf` and re-run before treating it as a tampering event.
    magic_ok: True
    metadata_parsed: False
    metadata_error: ValueError: Sorry, file appears to be version 4294967295 which we cannot handle
    sidecar_present: True
    sidecar_match: True
    sha256_actual: a4c1f2…
    sha256_expected: a4c1f2…
$ echo $?
1
```

Aynı dosyanın yanında **hiç** sidecar yoksa karar bunun yerine `6` olur — bozulmayı eleyecek hiçbir şey bulunmadığından artefakt kesilmiş ya da hasarlı sayılmalıdır. **Uyuşmayan** bir sidecar ile de `6` olur; ancak ayrıştırma hatası olarak değil, yukarıdaki sidecar uyuşmazlığı olarak raporlanır.

### İsteğe bağlı bağımlılık yok

`gguf` paketi yüklü değilse meta veri ayrıştırma katmanı sessizce atlanır — magic + sidecar kontrolleri yük taşımaya devam eder:

```shell
$ pip uninstall -y gguf
$ forgelm verify-gguf checkpoints/run/exports/model-q4_k_m.gguf
OK: checkpoints/run/exports/model-q4_k_m.gguf
  GGUF magic OK, SHA-256 sidecar match
    magic_ok: True
    metadata_parsed: False
    sidecar_present: True
    sidecar_match: True
```

Meta veri katmanını geri eklemek için isteğe bağlı extra'yı yükleyin: `pip install gguf`.

## Bkz.

- [`audit_event_catalog.md`](audit_event_catalog-tr.md) — kanonik event sözlüğü.
- [`verify_audit.md`](verify_audit-tr.md) — `audit_log.jsonl` için kardeş doğrulayıcı.
- [`verify_annex_iv_subcommand.md`](verify_annex_iv_subcommand-tr.md) — Annex IV teknik dokümantasyon artifact'ı için kardeş doğrulayıcı.
- [GGUF Export kullanım kılavuzu sayfası](../usermanuals/tr/deployment/gguf-export.md) — bu doğrulayıcının tükettiği sidecar'ı yazan üretim tarafına dair operatör-odaklı kılavuz.
- `forgelm.verify.verify_gguf` (`forgelm.verify_gguf` olarak da) — entegratörlerin CLI'dan geçmeden doğrudan çağırdığı kütüphane giriş noktası.
