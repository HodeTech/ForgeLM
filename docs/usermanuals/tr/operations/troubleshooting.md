---
title: Sorun Giderme
description: Sık karşılaşılan ForgeLM hataları ve çözümleri.
---

# Sorun Giderme

Bu sayfa en sık karşılaşılan ForgeLM hatalarını ve işe yarayan çözümleri listeler. Listede olmayan herhangi bir şey için önce `forgelm doctor` çalıştırın — ortam sorunlarının %80'ini yakalar.

## Kurulum sorunları

### macOS'ta `bitsandbytes` import hatası

```text
ImportError: bitsandbytes/libbitsandbytes_cpu.so: cannot find ...
```

**Sebep:** `bitsandbytes` Metal/MPS desteklemiyor. macOS host'ları 4-bit kuantize eğitim (QLoRA) çalıştıramaz.

**Çözüm:** Ya CUDA'lı bir Linux'ta koşturun ya da full precision'da eğitin (`model.load_in_4bit: false`).

### `undefined symbol: __cudaRegisterFatBinaryEnd`

**Sebep:** PyTorch ve CUDA toolkit sürümleri uyumsuz.

**Çözüm:** PyTorch'u CUDA sürümünüzle eşleşecek şekilde tekrar kurun:

```shell
$ pip uninstall torch torchvision torchaudio
$ pip install torch --index-url https://download.pytorch.org/whl/cu121   # CUDA sürümünüzü yazın
```

### `OSError: HuggingFace token not found`

**Sebep:** Bazı modeller (Llama, Llama Guard) geçit kontrollü ve auth token ister.

**Çözüm:** `huggingface-cli login` çalıştırın veya `HF_TOKEN` environment variable ayarlayın. ForgeLM ikisini de okur.

## Eğitim sorunları

### Loss NaN'a gidiyor

```text
[2026-04-29 14:18:55] training_step_complete loss=nan
```

**Sebepler (en sık başta):**
1. Learning rate çok yüksek (özellikle full FT'de — 1e-5 deneyin).
2. Çok düşük precision'da (fp16) gradient overflow. bfloat16'ya geçin: `model.bnb_4bit_compute_dtype: "bfloat16"`.
3. Aşırı token'lı bozuk veri satırı (`forgelm audit` koşturun, kalite flag'lerine bakın).
4. Inf/NaN döndüren özel reward fonksiyonu (sadece GRPO).

### Train'de loss düşüyor, eval'de artıyor

**Sebep:** Overfitting; özellikle küçük dataset ve çok epoch'la.

**Çözüm:**
- `epochs`'u azaltın (10 değil, 1-3 ile başlayın).
- Embedding regülarizasyonu için `neftune_noise_alpha: 5.0` ekleyin.
- Learning rate'i düşürün.
- Daha çeşitli eğitim verisi ekleyin.

### Eğitim adım 0'da çöküyor

**Sebep:** Neredeyse her zaman `--dry-run`'ın yakaladığı bir konfigürasyon problemi.

**Çözüm:** Eğitimden önce her zaman `forgelm --config X.yaml --dry-run` koşturun. "Eğitim çöktü" raporlarının %90'ı atlanmış dry-run'lara dayanır.

### Eğitim beklenenden çok yavaş

**Sebepler (en sık başta):**
1. Desteklenen modelde Unsloth kullanılmaması (Qwen / Llama / Mistral için `model.use_unsloth: true`).
2. Talimat verisinde `packing: false` (true yapmak %30-50 throughput sağlar).
3. Yanlışlıkla CPU offload açık (`distributed.cpu_offload: false`).
4. Mixed-precision yanlış konfigüre (`bfloat16` kullanın, `float16` değil).
5. Disk I/O bağımlı — dataset'iniz yavaş depolamada.

Eğitim sırasında `nvidia-smi`: GPU kullanımı %85+ olmalı. %50'nin altındaysa GPU değil CPU veya I/O bağımlısınız.

## OOM hataları

### Eğitim ortasında `CUDA out of memory`

**Sebep:** Özellikle uzun bir dizinin yarattığı activation bellek patlaması.

**Çözüm:**
- Peak tahminini doğrulamak için `--fit-check` koşturun.
- Verinizde uzun aykırı değerler varsa `max_length`'i düşürün.
- `packing: true` etkinleştirin (dizi uzunluğunu eşitler).
- `batch_size`'ı düşürüp `gradient_accumulation_steps`'i artırın (aynı etkili batch, daha az peak).

### Eğitimde değil eval'de OOM

**Sebep:** Eval genelde sliding-window veya packing olmadan koşar — peak eğitim peak'ini aşabilir.

**Çözüm:** Ayrı bir `evaluation.max_length` alanı yoktur — `model.max_length` hem eğitim hem eval geçişini yönetir (TRL her ikisi için de aynı trainer config'ini kullanır). Training fit ederken eval OOM oluyorsa, `model.max_length`'i düşürün (eğitim context penceresini de küçültmeyi kabul ederek) veya uzun aykırı değerlerin bir eval batch'ine daha az düşmesi için validation split'inizi küçültün.

## Veri sorunları

### `audit refuses to certify a leaky split`

**Sebep:** Train satırları val veya test'te de görünüyor (`cross_split_overlap > 0`).

**Çözüm:** Verinizi tekrar bölün; tek bir dökümanın birden çok split'e yayılmadığından emin olun. Bkz. [Split-arası Sızıntı](#/data/leakage).

### Audit çok fazla kalite flag'i raporluyor

**Sebep:** Kalite filtresi varsayılan olarak muhafazakar, ama kod veya simge-ağırlıklı veride aşırı flag verebilir.

**Çözüm:** YAML'da belirli kontrolleri devre dışı bırakın:

```yaml
audit:
  quality_filter:
    skip: ["min_alpha_ratio", "max_bullet_ratio"]
```

### Format otomatik algılaması yanlış

**Sebep:** JSONL'in ilk satırı diğer satırlarla eşleşmiyor.

**Çözüm:** Formatı açıkça ayarlayın: `format: "preference"` (ya da hangisiyse).

## Eval / güvenlik sorunları

### Llama Guard her zaman "high severity" raporluyor

**Sebep:** Probe seti, base modelin de zaten geçemediği adversarial girdiler içeriyor. Llama Guard hem base'i hem fine-tuned çıktıları doğru şekilde flagliyor.

**Çözüm:** Bu doğru davranış. Önemli olan kontrol *baseline'a karşı gerileme*, mutlak puan değil. `evaluation.safety.baseline`'ın pre-train baseline'ına işaret ettiğinden emin olun.

### Benchmark puanları kamuya açık sonuçlardan çok farklı

**Sebep:** Muhtemelen yayınlanan leaderboard kuralıyla `num_fewshot` uyuşmazlığı.

**Çözüm:** Her görevin kanonik ayarını kontrol edin (ör. MMLU kanonik olarak 5-shot) ve eşleştirin.

## Compliance sorunları

### `audit_log.jsonl chain hash invalid`

**Sebep:** Audit log değiştirilmiş (veya dosya sistemi bozmuş).

**Çözüm:** Log'u değiştirmeyin. Bozulma olduysa orijinal artifact'lar artık güvenilir değildir — temiz durumdan eğitimi yeniden koşturun.

### Annex IV gerekli alanları eksik

**Sebep:** YAML'da gerekli `compliance:` alanları ayarlanmamış.

**Çözüm:** `forgelm verify-annex-iv path/to/annex_iv_metadata.json` ile eksik alan listesini alın (ForgeLM'in yazdığı kanonik dosya adı — bkz. [Annex IV](#/compliance/annex-iv)).

## Webhook'lar fırlamıyor

**Sebepler:**
- Webhook URL özel IP'ye işaret ediyor ve `webhook.allow_private` false.
- TLS sertifika doğrulaması başarısız.
- Endpoint 4xx döndürüyor (varsayılan log'da sessiz).

**Çözüm:** `audit_log.jsonl` içinde `webhook_failed` olaylarına bakın; yanıt durumu ve gövdeyi içerirler.

## Bug bildirimi nereye

`forgelm doctor` her şeyin iyi olduğunu söylediği halde sorun devam ediyorsa toplayın:

1. Başarısız olan tam `forgelm` komutu.
2. Tam hata çıktısı.
3. `config.yaml`'iniz (sırlar redakte edilmiş).
4. `forgelm doctor` çıktısı.

Issue açın: <https://github.com/HodeTech/ForgeLM/issues>. Maintainer ekibi 48 saat içinde triaj eder.

## Bkz.

- [`forgelm doctor`](#/getting-started/installation) — birinci-hat tanı.
- [VRAM Fit-Check](#/operations/vram-fit-check) — uçuş öncesi bellek kontrolü.
- [Konfigürasyon Referansı](#/reference/configuration) — her YAML alanı.
