---
title: LLM-as-Judge
description: Daha güçlü bir judge modeli kullanarak held-out prompt seti üzerinde kalite skorlama — yapılandırılabilir minimum'lu single-rubric ortalama puan.
---

# LLM-as-Judge

Standart benchmark'lar dar yetenekleri ölçer; "bu yanıt gerçekten iyi mi?" sorusunu yakalamaz. LLM-as-judge bu boşluğu doldurur: daha güçlü bir model (veya yerel bir instruction-tuned LLM) eğitilmiş modelin held-out prompt seti üzerindeki çıktısını skorlar. ForgeLM'in judge'ı tek-rubric ortalama-skor kapısıdır — judge her (prompt, completion) çiftine 1-10 skor atar ve ortalama puan yapılandırılan alt sınırın altına düşerse koşum başarısız olur.

## Ne zaman kullanılır

| LLM-as-judge kullanın... | Benchmark kullanın... |
|---|---|
| Çıktı kalitesi öznel (yardımcı, kibar, marka uyumlu). | Görevin doğrulanabilir cevabı var. |
| Ground truth yok. | Ground truth var. |
| Koşumlar arası nitel gerilemeleri izliyorsunuz. | Mutlak yetenek izliyorsunuz. |
| Maliyet kabul edilebilir (GPT-4o ile ~$1-5 / 1K judgement). | Ücretsiz, yerel eval gerekiyor. |

## Hızlı örnek

```yaml
evaluation:
  llm_judge:                            # block name is `llm_judge`, not `judge`
    enabled: true
    judge_model: "gpt-4o-mini"          # or local path, e.g. "./judges/Qwen2.5-72B-Instruct"
    judge_api_key_env: OPENAI_API_KEY   # null = local model (no API call)
    judge_api_base: null                # override for Azure OpenAI / vLLM-compatible gateway
    eval_dataset: "data/eval-prompts.jsonl"
    min_score: 6.5                      # mean score floor (1-10 scale); revert below this
    batch_size: 8                       # (prompt, completion) pairs scored per round; 1 disables batching
```

## Eval-dataset formatı

`eval_dataset` bir JSONL dosyasıdır. Her satır, judge'ın eğitilmiş modelin yanıtına karşı skorladığı tek bir prompt'tur:

```jsonl
{"prompt": "10 yaşında bir çocuğa mitozu açıkla."}
{"prompt": "Bu Python list comprehension'ı for-döngüsüne dönüştür: [x*2 for x in nums]"}
```

ForgeLM her prompt için eğitilmiş modelin completion'ını üretir ve judge'a sorar: "Bu yanıtı yardımcılık ve doğruluk için 1-10 skalasında skorla." Veri seti üzerindeki ortalama, koşumun `judge_score`'udur.

## Çıktı

`<output_dir>/judge_report.json`:

```json
{
  "judge_model": "gpt-4o-mini",
  "eval_dataset": "data/eval-prompts.jsonl",
  "n_prompts": 200,
  "mean_score": 7.4,
  "min_score_threshold": 6.5,
  "passed": true,
  "per_prompt": [
    {"prompt_id": 0, "score": 8, "explanation": "..."},
    {"prompt_id": 1, "score": 6, "explanation": "..."}
  ]
}
```

`mean_score < min_score` olduğunda trainer bunu evaluation gerilemesi olarak ele alır: `auto_revert: true` ise model revert edilir ve koşu `3` (`EXIT_EVAL_FAILURE`) ile çıkar; sevk edilen varsayılan `auto_revert: false` ile failure audit log'a ve JSON `judge` bloğuna kaydedilir ama model yine terfi eder ve koşu `0` ile çıkar. Başarısız bir judge kapısının exit kodunu değiştirmesini istiyorsanız `auto_revert: true` ayarlayın.

## Judge model seçimi

| Judge | Maliyet / 1K judgement | Kalite |
|---|---|---|
| `gpt-4o` (`judge_api_key_env: OPENAI_API_KEY` set edilir) | ~$5 | En yüksek. Production varsayılanı. |
| `gpt-4o-mini` | ~$1 | gpt-4o kalitesinin %90'ı. Önerilen maliyet-dengeli varsayılan. |
| `claude-haiku-4` (`judge_api_base: https://api.anthropic.com/v1` + uygun env var) | ~$1.50 | gpt-4o-mini ile karşılaştırılabilir. |
| Yerel yol (ör. `./judges/Qwen2.5-72B-Instruct`, `judge_api_key_env: null`) | $0 (kendi GPU saatiniz) | Makul; ince yargı çağrılarında daha zayıf. |

Judge tek bir yapılandırılabilir modeldir — yerleşik pairwise / ELO / multi-criteria rubric pipeline yoktur. İki eğitilmiş model arasında pairwise A/B karşılaştırması yapmak için aynı eval dataset'e karşı iki ayrı trainer invocation çalıştırın ve sonuçtaki `mean_score` değerlerini karşılaştırın; ForgeLM pairwise çağrıyı dahili olarak orkestre etmez.

## Maliyet kontrolü

ForgeLM runtime USD bütçesi uygulamaz. Maliyeti dışarıdan yönetin:

- **`eval_dataset` boyutunu sınırlayın.** Her prompt = bir judge API çağrısı. 200 prompt × $0.005 (gpt-4o-mini) ≈ koşum başına $1.
- **İterasyon için yerel judge.** Nightly koşumlar için 70B-sınıfı instruction-tuned bir modeli kendi GPU'nuza pinleyin; API judge'larını release-gate koşumuna saklayın.
- **Sağlayıcı-tarafı rate limiting.** Throughput cap'leri OpenAI/Anthropic dashboard'unuzda set edin, `forgelm` config'inde değil.

## Sık hatalar

:::warn
**Aynı modeli judge ve student olarak kullanmak.** 7B bir model başka 7B'nin çıktılarını skorlarken ince kalite sorunlarını yakalamaz. Daha güçlü bir judge kullanın — 7B eğitilmiş model için, instruction-tuned 70B+ veya API-sınıfı judge.
:::

:::warn
**Çok küçük `eval_dataset`.** 20 prompt skorlamak istatistiksel gürültüdür. Anlamlı ortalama puan için 200+; release gate için 1000+ daha iyidir.
:::

:::warn
**`judge_api_key_env`'i unutmak.** `judge_model` API model adıyken (ör. `gpt-4o-mini`) ve `judge_api_key_env` set edilmemişken, ForgeLM yerel-model yüklemeye fallback yapar ve `gpt-4o-mini`'yi HF Hub'dan indirmeye çalışır, yüksek sesle başarısız olur. Judge bir API ise env-var adını açıkça set edin.
:::

:::tip
**Judge'ı benchmark'larla eşleştirin.** Judge'ta kazanan ama benchmark'larda gerileyen bir model, judge'ın tercih ettiği şeye overfit oluyor demektir. Her iki sinyal de önemli.
:::

## Bkz.

- [Benchmark Entegrasyonu](#/evaluation/benchmarks) — niceliksel eval refakatçisi.
- [Sentetik Veri](#/data/synthetic-data) — teacher model için benzer `api_base` / `api_key_env` zarfını kullanır.
- [Otomatik Geri Alma](#/evaluation/auto-revert) — judge mean-score, dört guard ailesinden biri.
