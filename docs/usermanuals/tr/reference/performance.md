---
title: Performans
description: ForgeLM throughput'u için önemli düğmeler — safety/judge batch_size, paragraph chunker, GaLore/4-bit/packing trade-off'ları.
---

# Performans

ForgeLM'in hot path'leri model forward pass'inde, safety classifier'da, LLM-as-judge generation'da ve (ingestion için) corpus chunker'dadır. Bu sayfa o path'leri hareket ettiren düğmeleri listeler; derin rehber her trade-off'u dürüstçe yürür.

## Aslında ne yavaştır

Yavaş olmayanı ayarlamayın. İşlem sırası:

1. **Önce profile edin.** `forgelm` aşama zamanlamalarını yazdırır; yavaş aşama ayarladığınız yerdir.
2. **Lazy import'lar süreç başlangıcına yardımcı olur, eğitime değil.** `forgelm --help` yavaşsa lazy import yardımcı olur. GPU adımınız yavaşsa trainer'ı profile edin.
3. **Model boyutu + sequence length × batch hâkimdir.** Buradaki hiçbir düğme sığmayan bir modelden sizi kurtarmaz; önce daha küçük bir model veya daha kısa bir context seçin.

## En önemli dört düğme

### `evaluation.safety.batch_size`

Llama Guard classifier için pad-longest batching. Varsayılan `8`. Yukarı: daha hızlı eval, daha fazla VRAM. Aşağı: daha yavaş eval, daha küçük kartlara sığar. Her ayardaki VRAM için [trade-off tablosu](#/reference/configuration)'na bakın.

### `evaluation.llm_judge.batch_size`

Local-model judging için safety eval ile aynı şekil. API üzerinden judging yaparken alakasızdır (API bağımsız olarak rate-limit yapar).

### `ingestion.strategy`

Ortada başlamaması gereken SFT corpus'ları için `paragraph`. Overlap'in önemli olduğu retrieval corpus'ları için `sliding`. Yapılandırılmış dokümanlar için `markdown`. `semantic` yol haritalı, yayında değil.

### Bellek kolları (4-bit, GaLore, sample packing)

Aksi takdirde sığmayacak bir modeli sığdırmak için üç ortogonal araç. Bunlar **sığdırmak** içindir, **hız** için değil — hız, model sığdıktan sonra daha büyük bir batch veya daha uzun bir context'ten gelir.

## Daha fazla okumak için nereye

- VRAM-vs-throughput tabloları, her stratejinin ne zaman kullanılacağı ve yaygın tuzaklarla derin rehber:
  [`performance-tr.md`](https://github.com/HodeTech/ForgeLM/blob/main/docs/guides/performance-tr.md) (GitHub kaynağı).
- Tam konfigürasyon referansı (her düğme, her varsayılan) — bu manual içinde [Konfigürasyon](#/reference/configuration)'da da bulunur.
- Projenin dayattığı lazy-import katkıda bulunan standardı:
  [`coding.md`](https://github.com/HodeTech/ForgeLM/blob/main/docs/standards/coding.md) (GitHub kaynağı).

## Ayrıca bakınız

- [Konfigürasyon](#/reference/configuration) — her düğme, her varsayılan.
- [Library API](#/reference/library-api) — bu düğmeleri Python'dan çağırma.
- [Air-gap Ön-cache](#/operations/air-gap) — model ön-cache'lemenin performans etkileri.
- [VRAM Fit Check](#/operations/vram-fit-check) — eğitim öncesi OOM tahmini.
