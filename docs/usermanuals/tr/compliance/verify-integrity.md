---
title: Model Bütünlüğünü Doğrula
description: Eğitilmiş bir model dizinini SHA-256 bütünlük manifest'ine (model_integrity.json) karşı yeniden hash'leyerek eğitimden bu yana değişen, kaldırılan veya eklenen artefaktları tespit edin.
---

# Model Bütünlüğünü Doğrula

`forgelm verify-integrity`, Madde 15 model-bütünlük manifest'iyle eşleşen salt-okunur doğrulayıcıdır. Compliance export, eğitim sırasında final modelin yanına `model_integrity.json` yazar ve her artefaktın SHA-256'sını ve byte boyutunu kaydeder. `verify-integrity` dizini yeniden gezer, her hash'i yeniden hesaplar ve manifest oluşturulduğundan beri **değişen**, **kaldırılan** veya **eklenen** her dosyayı raporlar. Bu komut, audit *log*'unu doğrulayan [`verify-audit`](#/compliance/verify-audit)'in model *ağırlıkları* karşılığıdır.

## Ne zaman kullanılır

- **Eğitilmiş bir modeli deploy etmeden veya göndermeden önce.** Temiz bir çıkış, kayıtlı her artefaktın manifest'in kaydettiğiyle byte-birebir aynı olduğu anlamına gelir — yarım inen bir dosyayı ya da eksik senkronlanmış bir dizini üretime ulaşmadan yakalayan kontrol budur.
- **Modeli makineler veya depolama katmanları arasında taşıdıktan sonra.** Aktarım sırasındaki sessiz bozulma, değişen/kaldırılan artefakt olarak ortaya çıkar.
- **CI/CD release kapılarında.** Compliance export'tan sonra çalıştırın; sıfır-dışı çıkışta release'i başarısız kılın.
- **Periyodik bir uyumluluk taramasının parçası olarak.** Arşivlenen modellerin zamanlanmış yeniden-doğrulaması, bit-rot'u ve kazara üzerine yazmaları erken yakalar.

:::warn
**`model_integrity.json` anahtarsızdır — bu bir bozulma kontrolüdür, tahrifat kanıtı değil.** Manifest, imzası ve HMAC'i olmayan düz bir SHA-256 listesidir. Model dizinine yazabilen bir saldırgan ağırlıkları değiştirip manifest'i yeniden üretebilir; `verify-integrity` bunun üzerine `OK: All 1 recorded artifact(s) present and unchanged.` raporlar ve `0` ile çıkar. Doğrulayıcının kendi kaynağı tehdit modelini açıkça belirtir: adversarial değildir.

Temiz bir çıkışın ortaya koyduğu: manifest yazıldığından beri kazara bozulma, bit-rot, aktarım hasarı ya da sıradan bir düzenleme yaşanmadığı. Ortaya koymadığı: yazma erişimi olan bir tarafın ağırlıkları kasten değiştirmemiş olduğu.

Gerçek tamper-evidence için manifest'in, model dizinine yazan tarafın erişemeyeceği bir yerde durması gerekir — ayrık bir imza, write-once bir depo (S3 Object Lock, ledger DB) ya da ayrı bir artefakt kayıt defteri. Karşılaştırın: audit log *anahtarlıdır*; `FORGELM_AUDIT_SECRET` altında her satır bir HMAC etiketi taşır, dolayısıyla `verify-audit` gizli anahtarı olmayan birinin yaptığı düzenlemeyi yakalar (exit `6`, `HMAC mismatch`). `model_integrity.json`'u koruyan eşdeğer bir anahtar yoktur.
:::

## Nasıl çalışır

```mermaid
sequenceDiagram
    participant CI as CI / operatör
    participant Verify as forgelm verify-integrity
    participant Manifest as model_integrity.json
    participant Dir as model dizini

    CI->>Verify: verify-integrity MODEL_DIR
    Verify->>Manifest: kayıtlı {file, sha256, size_bytes} yükle
    Verify->>Dir: artefaktları gez (manifest dosyasının kendisi hariç)
    loop her artefakt için
        Verify->>Verify: sha256 yeniden hesapla, manifest ile karşılaştır
    end
    Verify->>Verify: changed / removed / added sınıflandır
    Verify-->>CI: çıkış 0 (hepsi eşleşti) / 1 (girdi hatası, hiçbir şey karşılaştırılmadı) / 2 (runtime I/O hatası) / 6 (uyuşmazlık — karşılaştırıldı ve uyuşmadı)
```

## Hızlı başlangıç

```shell
$ forgelm verify-integrity ./checkpoints/final_model
OK: all 12 recorded artifact(s) present and unchanged.
```

CI için makine-okunur çıktı:

```shell
$ forgelm verify-integrity ./checkpoints/final_model --output-format json
```

```json
{
  "success": true,
  "valid": true,
  "reason": "All 12 recorded artifact(s) present and unchanged.",
  "changed": [],
  "removed": [],
  "added": [],
  "verified_count": 12,
  "path": "/work/checkpoints/final_model"
}
```

## Ayrıntılı kullanım

### Bir uyuşmazlığı okumak

Bir artefakt artık manifest ile eşleşmediğinde, diff listeleri dolar ve komut `6` ile çıkar — manifest ayrıştırıldı ve yürüyüş çalıştı, dolayısıyla bu bir bütünlük kararıdır, config hatası değil:

```json
{
  "success": false,
  "valid": false,
  "reason": "Model artifacts do not match model_integrity.json: 1 changed, 1 removed.",
  "changed": ["adapter_model.safetensors"],
  "removed": ["tokenizer.model"],
  "added": [],
  "verified_count": 10,
  "path": "/work/checkpoints/final_model"
}
```

- `changed` — SHA-256'sı artık manifest ile eşleşmeyen artefaktlar.
- `removed` — manifest'te kayıtlı ama diskte olmayan artefaktlar.
- `added` — diskte olup manifest'te kayıtlı olmayan dosyalar (manifest dosyasının kendisi yürüyüşten her zaman hariç tutulur).

### Çıkış kodu özeti

| Kod | Anlamı |
|---|---|
| `0` | Her kayıtlı artefakt mevcut ve değişmemiş, fazladan dosya yok. |
| `1` | Operatör / girdi hatası — eksik yol, yolun dizin yerine dosya olması, manifest bulunamadı, malformed JSON, JSON nesnesi olmayan bir manifest kökü, eksik `artifacts` anahtarı, list olmayan `artifacts`, **boş** `artifacts` listesi, dict olmayan bir girdi, string olmayan bir `file` değeri ya da model dizininden kaçan bir manifest girdi yolu. Bunların her biri herhangi bir artefakt hash'lenmeden döner, dolayısıyla hiçbir şey karşılaştırılmadı. |
| `2` | Erişilebilir bir yolda gerçek runtime I/O hatası (okuma hatası, yürüyüş sırasında izin reddi). |
| `6` | Bütünlük uyuşmazlığı — manifest ayrıştırıldı ve yürüyüş çalıştı, ama manifest üretildikten sonra en az bir dosya değişmiş, kaldırılmış veya eklenmiş. |

Runtime-hatası zarfı (çıkış `2`) yalnızca `{"success": false, "error": "…"}` döndürür — önce `success` üzerinden dallanın, ardından `valid` ve diff listelerini inceleyin.

**Bilinçli karar:** model dizininden kaçan bir yola sahip manifest girdisi, bir saldırı şekli olsa bile `6`'da değil `1`'de kalır — doğrulayıcı hiçbir şey okumadan *önce* ağaç-dışı bir yolu hash'lemeyi reddeder, dolayısıyla hiçbir şey karşılaştırılmadı.

**Boş** bir `artifacts` listesi aynı nedenle `1`'dedir ve kendi mesajını taşır: *"Manifest records 0 artifacts, so nothing was verified. An empty manifest cannot attest to anything; regenerate it from a populated model directory."* Bu durum kesintiye uğramış bir export ya da yanlış yazılmış bir `final_path`'ten, yani adversarial olmayan biçimde doğar — ve sessiz bir `0`'ın en yanıltıcı olacağı tam da bu durumdur.

## Sık yapılan hatalar

:::warn
**Eksik bir `model_integrity.json`'ı zararsız saymak.** Manifest olmadan karşılaştırılacak bir şey yoktur — `verify-integrity` `0` değil `1` ile çıkar. Bu kapıya güvenmeden önce compliance export'un manifest'i yazdığını doğrulayın.
:::

:::warn
**Yeniden-quantization veya yeniden-export sonrası doğrulamak.** Bir GGUF veya merge edilmiş varyant üretmek byte'ları değiştirir; o modelin kendi taze üretilmiş manifest'ine ihtiyacı vardır. Dönüştürülmüş bir artefaktı orijinal eğitim manifest'ine karşı doğrulamayın.
:::

:::tip
**Herhangi bir deploy adımından önce doğrulayıcıyı CI'da sabitleyin.** Compliance export'tan sonra `forgelm verify-integrity --output-format json`'ı sıkı bir kapı olarak bağlayın. Sıfır-dışı çıkış release pipeline'ını başarısız kılmalıdır.
:::

## Ayrıca bakın

- [Audit Zincirini Doğrula](#/compliance/verify-audit) — Madde 12 audit *log*'u için eşlenik doğrulayıcı (bu komut model *ağırlıklarını* kapsar).
- [Annex IV](#/compliance/annex-iv) — bütünlük manifest'iyle birlikte export edilen teknik-dokümantasyon artefaktı.
- [Verify GGUF](#/deployment/verify-gguf) — export edilmiş bir GGUF model dosyası için bütünlük doğrulayıcısı.
- [`verify_integrity_subcommand-tr.md`](https://github.com/HodeTech/ForgeLM/blob/main/docs/reference/verify_integrity_subcommand-tr.md) — tam bayrak-seviyesi referans (GitHub kaynağı).
