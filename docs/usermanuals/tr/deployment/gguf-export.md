---
title: GGUF Export
description: llama.cpp, Ollama veya LM Studio ile yerel inference için GGUF'a kuantize edin.
---

# GGUF Export

GGUF (GPT-Generated Unified Format), `llama.cpp` üzerinden yerel CPU/GPU inference için fiili dosya formatıdır. ForgeLM fine-tuned bir checkpoint'i GGUF'a export eder ve dosyanın yanına bir SHA-256 sidecar yazar. `f16` ve `q8_0` tek komutla çıkar; K-quant seviyeleri (`q2_k`, `q3_k_m`, `q4_k_m`, `q5_k_m`) aşağıda anlatılan bir `llama-quantize` adımı daha ister.

## Hızlı örnek

```shell
$ forgelm export ./checkpoints/customer-support \
    --output model.gguf \
    --quant q8_0
Export complete: model.gguf (quant=q8_0, sha256=3f2a91c0b4d7…)
```

(Log çıktısı, kısaltılmış.) Sonuç `model.gguf` ve yanında `model.gguf.sha256`'dır. Sidecar `sha256sum` formatındadır (`<digest> *model.gguf`); bu yüzden hem `sha256sum -c model.gguf.sha256` hem de [`forgelm verify-gguf`](#/deployment/verify-gguf) onu kabul eder. Checkpoint dizininde zaten bir `model_integrity.json` varsa ForgeLM digest'i oraya da `exported_artifacts` altında yazmayı dener (best-effort: dosya geçerli JSON değilse ya da yazılamıyorsa bir uyarı loglanır ve export yine de başarılı olur; `--no-integrity-update` bunu atlar).

Sidecar bozulmayı ve kazara değişikliği yakalar. Dosyanın yanında durduğu için kurcalama kanıtı **değildir**: GGUF'u değiştirebilen sidecar'ı da değiştirebilir. Buna ihtiyacınız varsa dosyayı imzalayın ya da digest'i dosyanın yazarının erişemediği bir yerde tutun.

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

### K-quant'lar bir adım daha ister

ForgeLM'in çağırdığı dönüştürücü `f16` ve `q8_0`'ı doğrudan yazar. Bir K-quant için (`q2_k`, `q3_k_m`, `q4_k_m` — varsayılan — ve `q5_k_m`) `forgelm export`, kendi `.sha256`'sıyla birlikte, çıktı adındaki `.gguf` uzantısı `.f16.gguf` ile değiştirilerek adlandırılan (`model.gguf` → `model.f16.gguf`; bu uzantıyla bitmeyen bir çıktıya `.f16.gguf` eklenir) ara bir `f16` GGUF yazar. Değişikliği bildirir (`--output-format json`'da `requested_quant`, `manual_step_required` ve tam `followup_command`) ve işi bitiren `llama-quantize` komutunu loglar:

```shell
$ forgelm export ./checkpoints/customer-support --output model.gguf --quant q4_k_m
Export complete: model.f16.gguf (quant=f16, sha256=9c1e47ab02d5…)
Manual quantization step required — run: llama-quantize model.f16.gguf model.gguf Q4_K_M
$ llama-quantize model.f16.gguf model.gguf Q4_K_M
$ sha256sum model.gguf > model.gguf.sha256
```

`llama-quantize` llama.cpp'den gelir; ForgeLM onu kurmaz. `forgelm export` kuantize edilmiş dosya için sidecar yazmaz; son komut onu oluşturur ve `forgelm verify-gguf model.gguf` karşılaştıracak bir digest bulur.

GGUF export her zaman ayrı, açık bir `forgelm export` çağrısıdır — config-driven bir otomatik export toggle'ı yoktur. `forgelm/config.py`'de bir `output:` bloğu ve şemada hiçbir yerde `gguf` alanı yoktur, dolayısıyla bir eğitim YAML'ı eval kapısının geçmesinin yan etkisi olarak export'u tetikleyemez; eğitim bittiğinde `forgelm export`'u kendiniz çalıştırın.

## Çoklu-quant export

`forgelm export --quant` invocation başına tek değer kabul eder
(seçenekler: `{q2_k, q3_k_m, q4_k_m, q5_k_m, q8_0, f16}`). Tek bir
checkpoint'ten birden çok quantisation üretmek için komutu her
quant için bir kez çalıştırın. Dönüştürücünün doğrudan yazdığı iki
seviye için:

```shell
$ for q in q8_0 f16; do
    forgelm export ./checkpoints/run \
        --output "./gguf/model.${q}.gguf" \
        --quant "${q}"
  done
Export complete: ./gguf/model.q8_0.gguf (quant=q8_0, sha256=3f2a91c0b4d7…)
Export complete: ./gguf/model.f16.gguf (quant=f16, sha256=b81d05e6c392…)
```

Her çalıştırma kendi sidecar'ını yazar (`model.q8_0.gguf.sha256`, `model.f16.gguf.sha256`). K-quant'lar için döngüyü K-quant adlarıyla çalıştırın ve her ara dosyayı yukarıdaki gibi `llama-quantize` ile tamamlayın.

Checkpoint dizininde bir `model_integrity.json` varsa her çalıştırma onun `exported_artifacts` listesine (yukarıdaki gibi best-effort) bir kayıt eklemeyi dener:

```json
{
  "exported_artifacts": [
    {"file": "model.q8_0.gguf", "format": "gguf", "quant": "q8_0", "sha256": "…", "size_bytes": 7200000000},
    {"file": "model.f16.gguf", "format": "gguf", "quant": "f16", "sha256": "…", "size_bytes": 13000000000}
  ]
}
```

## GGUF bütünlük doğrulama

```shell
$ forgelm verify-gguf model.q8_0.gguf
OK: model.q8_0.gguf
  GGUF magic OK, metadata parsed, SHA-256 sidecar match
    magic_ok: True
    metadata_parsed: True
    sidecar_present: True
    sidecar_match: True
    tensor_count: 291
    sha256_actual: a4c1f2…
    sha256_expected: a4c1f2…
```

Bu komut GGUF magic header'ını denetler, isteğe bağlı `gguf` paketi kuruluysa meta veri bloğunu ayrıştırır ve varsa `<dosya>.sha256` sidecar'ını karşılaştırır. Kelime dağarcığını ya da tokenizer'ı base modelle karşılaştırmaz. Kötü transferden bozulmaları, sessiz disk hatalarını ve export sonrası düzenlenmiş bir dosyayı yakalar; dosyayı kimin ürettiğini göstermez. Her katmanın neyi kanıtladığı için [Verify GGUF](#/deployment/verify-gguf) sayfasına bakın.

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
**`--adapter`'ı unutmak.** `forgelm export` bir LoRA adapter'ı base modele yalnızca `--adapter <adapter-dir>` verdiğinizde merge eder; vermezseniz model yolu zaten tam bir model dizini olmalıdır. Adapter-only inference istiyorsanız GGUF istemiyorsunuz — adapter'a karşı doğrudan `forgelm chat` kullanın veya PEFT ile yükleyin.
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
- [CLI Referansı](#/reference/cli) — tam `forgelm export` bayrak yüzeyi.
- [Model Birleştirme](#/deployment/model-merging) — export öncesi adapter birleştirme.
