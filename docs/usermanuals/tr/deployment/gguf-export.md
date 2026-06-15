---
title: GGUF Export
description: llama.cpp, Ollama veya LM Studio ile yerel inference için GGUF'a kuantize edin.
---

# GGUF Export

GGUF (GPT-Generated Unified Format), `llama.cpp` üzerinden yerel CPU/GPU inference için fiili dosya formatıdır. ForgeLM herhangi bir fine-tuned checkpoint'i altı kuantizasyon seviyesi ve SHA-256 manifest ile GGUF'a export eder.

## Hızlı örnek

```shell
$ forgelm export ./checkpoints/customer-support \
    --output model.gguf \
    --quant q4_k_m
✓ LoRA base'e merge edildi
✓ GGUF'a dönüştürüldü
✓ q4_k_m'a kuantize edildi (4.1 GB → 4.1 GB)
✓ model.gguf ve model.gguf.sha256 yazıldı
```

Çıktı tek `.gguf` dosyası artı `.sha256` manifest.

## Kuantizasyon seviyeleri

| Seviye | Boyut (7B base) | Kalite | Kullanım |
|---|---|---|---|
| `f16` | 13 GB | Kayıpsız | Kalite benchmark; tam-precision arşiv. |
| `q8_0` | 7.2 GB | En yüksek | Bellek bol üretim. |
| `q5_k_m` | 4.8 GB | Yüksek | Mantıklı denge. |
| `q4_k_m` | 4.1 GB | İyi | **Yerel inference için varsayılan.** |
| `q3_k_m` | 3.3 GB | Kabul edilebilir | Dar bellek; biraz kalite kaybı. |
| `q2_k` | 2.6 GB | Daha düşük | Edge cihazlar için son çare; belirgin kalite kaybı. |

`q4_k_m` tatlı nokta — consumer donanıma rahat sığar, full precision'a karşı minimum kalite kaybı.

## Konfigürasyon

```yaml
output:
  gguf:
    enabled: true                       # eğitimden sonra otomatik export
    quant_levels: ["q4_k_m", "q5_k_m"] # tek seferde birden çok seviye
    output_dir: "${output.dir}/gguf/"
    manifest: true
```

`enabled: true` iken ForgeLM `forgelm` koşularının eval'i geçmesinin ardından otomatik export eder. `false` (varsayılan) ise `forgelm export`'u ad hoc kullanın.

## Çoklu-quant export

`forgelm export --quant` invocation başına tek değer kabul eder
(seçenekler: `{q2_k, q3_k_m, q4_k_m, q5_k_m, q8_0, f16}`). Tek bir
checkpoint'ten birden çok quantisation üretmek için komutu her
quant için bir kez çalıştırın:

```shell
$ for q in q4_k_m q5_k_m q8_0; do
    forgelm export ./checkpoints/run \
        --output "./gguf/model.${q}.gguf" \
        --quant "${q}"
  done
✓ gguf/model.q4_k_m.gguf yazıldı  (4.1 GB)
✓ gguf/model.q5_k_m.gguf yazıldı  (4.8 GB)
✓ gguf/model.q8_0.gguf yazıldı    (7.2 GB)
```

Manifest:

```json
{
  "base_model": "Qwen/Qwen2.5-7B-Instruct",
  "fine_tune_run_id": "abc123",
  "quants": {
    "q4_k_m": {"size_bytes": 4100000000, "sha256": "..."},
    "q5_k_m": {"size_bytes": 4800000000, "sha256": "..."},
    "q8_0":   {"size_bytes": 7200000000, "sha256": "..."}
  },
  "exported_at": "2026-04-29T15:00:00Z"
}
```

## GGUF bütünlük doğrulama

```shell
$ forgelm verify-gguf model.q4_k_m.gguf
✓ valid GGUF magic
✓ vocab boyutu base ile eşleşiyor
✓ sha256 manifest ile eşleşiyor
✓ tokenizer round-trip OK
```

Bu kötü transferden bozulmaları, sessiz disk hatalarını ve ara sıra `llama.cpp` upstream uyumsuzluklarını yakalar.

## Yaygın araçlarda GGUF yükleme

### Ollama

```shell
$ cat > Modelfile <<'EOF'
FROM ./model.q4_k_m.gguf
SYSTEM "Kibar bir müşteri-destek temsilcisisin."
PARAMETER temperature 0.7
EOF
$ ollama create my-bot -f Modelfile
$ ollama run my-bot
```

### LM Studio

`.gguf` dosyasını LM Studio'nun model dizinine bırakın; picker'da görünür.

### llama.cpp doğrudan

```shell
$ ./main -m model.q4_k_m.gguf -p "Merhaba, nasılsın?" -n 256
```

## Doğrudan dönüştürme (kuantizasyon yok)

Nadir olarak full-precision GGUF isterseniz (kuantizasyon-duyarlı bir inference engine için):

```shell
$ forgelm export ./checkpoints/run --output model.gguf --quant f16
```

Sonuç full-precision GGUF, 7B model için ~13 GB.

## Sık hatalar

:::warn
**LoRA'yı merge etmeden export.** `forgelm export` her zaman LoRA adapter'ı kuantizasyondan önce base'e merge eder. Adapter-only inference istiyorsanız GGUF istemiyorsunuz — adapter'a karşı doğrudan `forgelm chat` kullanın veya PEFT ile yükleyin.
:::

:::warn
**Tokenizer sürüm uyuşmazlığı.** GGUF tokenizer'ı gömer. Eğitim sonrası tokenizer'ı değiştirirseniz (nadir) GGUF `llama.cpp`'de doğru yüklenmez. Her zaman gerçekten eğitilen checkpoint'ten export edin.
:::

:::warn
**Orijinale karşı kalite gerilemesi.** Agresif quant'lar (q3, q2) Llama Guard puanlarını kaydırabilir. `safety-eval` henüz GGUF dosyalarını yüklemez; bu yüzden quant'lamadan önce güvenlik eval'ini **export öncesi HuggingFace checkpoint'ine karşı** yeniden koşturun — ve GGUF safety-eval gelene kadar quant sonrası güvenlik sapmasını bilinen bir kısıtlama olarak değerlendirin:

```shell
$ forgelm safety-eval --model ./output/final_model --probes data/safety-probes.jsonl
```

:::

:::tip
HuggingFace Hub yüklemesi için ForgeLM'in model card'ı GGUF dosyanıza referansla "Ollama ile kullanım" snippet'ı içerir — kopyala-yapıştır hazır.
:::

## Bkz.

- [Deploy Hedefleri](#/deployment/deploy-targets) — GGUF dışı deployment seçenekleri.
- [Konfigürasyon Referansı](#/reference/configuration) — `output.gguf` bloğu.
- [Model Birleştirme](#/deployment/model-merging) — export öncesi adapter birleştirme.
