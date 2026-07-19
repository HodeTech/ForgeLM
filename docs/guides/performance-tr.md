# Performans Ayarlama Rehberi

> **Hedef kitle:** Yavaş bir ForgeLM pipeline'ına sahip olan ve hangi düğmelerin önemli olduğunu — ve hangilerinin olmadığını — bilmek isteyen operatörler. Bu rehber *neyin* hızlandırdığı vs *neye mal olduğu* konusunda dürüsttür; tek satırlık bir config değişikliğinden 10× vaat etmez.
>
> **Eşlik eden referans:** [`../reference/configuration-tr.md`](../reference/configuration-tr.md) — aşağıda belgelenen batch-size düğmeleri dahil her config alanı.

ForgeLM'in hot path'leri YAML parser'da, audit logger'da veya CLI dispatch'te değildir — model forward pass'inde, safety classifier'da, LLM-as-judge generation'da ve (ingestion-ağır pipeline'lar için) corpus chunker'dadır. Bu rehber bunların her birini, hangi düğmenin var olduğunu ve eğer arttırırsanız compute / bellek / kalite olarak neye mal olduğunu yürür.

## Lazy torch import (kırmamanın maliyeti)

`forgelm`'i import etmek `torch`, `transformers`, `trl`, `datasets` veya başka herhangi bir ağır ML bağımlılığını import etmez — kontrat gereği, `tests/test_library_api.py::test_lazy_import_no_torch` ile pinlenir.

Bu **import zamanıdır**, eğitim throughput'u değil. Kazanç şuralarda gelir:

- `python -m forgelm.cli --help`'in birkaç saniye yerine onlarca milisaniyede dönmesi.
- `forgelm doctor` (Faz 34)'ün torch'un henüz kurulu olmadığı bir host'ta çalışması.
- Hafif CI runner'larının (lint, dry-run, audit) ihtiyaç duymadıkları 1-2 saniyelik torch yüklemesini atlaması.
- Tek seferlik bir PII tarama için `from forgelm import detect_pii` yapan ve trainer'a hiç dokunmayan notebook yazarları.

**Eğitimi hızlandırmaz.** `ForgeTrainer.train()` çağrıldıktan sonra torch yüklenir, model yüklenir ve GPU başlatılır — import eager olsaydı olduğu gibi.

Standart `docs/standards/coding.md` "Lazy import discipline" tarafından dayatılır: `forgelm/` altındaki hiçbir dosyada modül-üstü `torch` import yok. İhlaller CI'yı kırar:

```python
# DOĞRU — ağır bağımlılıklar fonksiyon gövdelerine ertelendi
def get_model_and_tokenizer(config):
    import torch                      # fonksiyon içinde lokal import
    from transformers import AutoModelForCausalLM, AutoTokenizer
    ...

# YANLIŞ — modül-üstü ağır import; CI başarısız olur
import torch
from transformers import AutoModelForCausalLM
```

Yeni bir modül eklerken kendinizi dosyanın üstünde `import torch` yazmaya çalışırken bulursanız, modülün *amacının* gerçekten torch yüklenmesini gerektirip gerektirmediğini sorun (çoğu yardımcı program gerektirmez).

## Safety-classifier batch_size

Llama Guard safety değerlendiricisi `evaluation.safety.test_prompts` içindeki her test prompt için classification üretir. Generation, pad-longest kullanılarak `evaluation.safety.batch_size` prompt'u bir seferde batch'lenir (böylece bir batch'teki kısa prompt'lar uzun olanların arkasında stall olmaz).

Canlı imzalar: `forgelm/safety/_generate.py::_generate_safety_responses` ve `forgelm/safety/_orchestrator.py::run_safety_evaluation`.

```yaml
evaluation:
  safety:
    enabled: true
    classifier: "meta-llama/Llama-Guard-3-8B"
    test_prompts: "configs/safety_prompts/general_safety.jsonl"
    batch_size: 8                      # varsayılan
    max_safety_regression: 0.05
```

### Arttırmanın maliyeti

| `batch_size` | VRAM (Llama Guard 3 8B, fp16) | Throughput | Risk |
|---|---|---|---|
| 1 | ~16 GB | 1× (baseline) | Yok — en güvenli fallback |
| 4 | ~17 GB | ~3.2× | Heterojen prompt uzunluklarında pad israfı |
| 8 (varsayılan) | ~18 GB | ~5.5× | Çoğu tüketici + workstation GPU için kabul edilebilir |
| 16 | ~21 GB | ~9× | Prompt'lar > ~1.5 k token ise 24 GB kartlarda OOM |
| 32 | ~28 GB | ~14× | A100 80 GB veya H100'ünüz olmadıkça OOM |

Yukarıdaki sayılar açıklayıcıdır — Llama Guard 3 8B üzerinde gömülü `general_safety.jsonl` ile ölçülmüştür; prompt dağılımınız + donanımınız farklı olacaktır. Önce tail-prompt uzunluğunuzu ölçün.

### Ne zaman düşürmeli

- GPU'yu trainer ile paylaşıyorsunuz (eşzamanlı eval) → 1 veya 2 kullanın.
- Prompt dağılımınızda long-tail prompt'lar var (bazıları 2 k token'da, çoğu 200'de) → pad-longest maliyeti paralellik kazancından ağır basar; 4 kullanın.
- 12 GB kart üzerindesiniz → 1'de başlayın, yalnızca `nvidia-smi` headroom gösterirse arttırın.

### Ne zaman arttırmalı

- Safety eval için adanmış 24 GB+ kartınız var → 16 genellikle uygundur.
- Prompt'larınız uzunluk-homojen (hepsi < 512 token) → 32, 24 GB üzerine sığabilir.

Library API sınır kontrolü pozitif olmayan tamsayıları açıkça reddeder:

```python
# forgelm/safety/_inputs.py::_validate_batch_size
if not isinstance(batch_size, int) or batch_size < 1:
    raise ValueError(f"batch_size must be a positive integer (got {batch_size!r})")
```

Bu kasıtlıdır — `batch_size: 0` veya `batch_size: -1` config typo'larıdır, sıfır-değerlendirme modları değil.

## LLM-as-judge batch_size

LLM-as-judge değerlendiricisi (`forgelm/judge.py`), local-model judging'i safety eval ile aynı şekilde batch'ler. API-arkalı judging için (OpenAI, Anthropic), parametre alakasızdır çünkü API bağımsız olarak rate-limit yapar.

```yaml
evaluation:
  llm_judge:
    enabled: true
    judge_model: "Qwen/Qwen2.5-7B-Instruct"   # local model
    eval_dataset: "eval_prompts.jsonl"
    min_score: 7.0
    batch_size: 8                              # varsayılan
```

Safety eval ile aynı VRAM trade-off'ları; aynı library-API sınır kontrolü (`forgelm/judge.py`):

```python
if not isinstance(batch_size, int) or batch_size < 1:
    raise ValueError(f"batch_size must be a positive integer (got {batch_size!r})")
```

Judge modeli safety classifier ile aynı aile ise, genellikle aynı `batch_size`'ı kullanabilirsiniz. Judge daha büyükse (ör. yüksek-riskli eval'ler için Qwen2.5-72B), 1-2'ye düşürün.

## Paragraph chunker (sliding'e karşı ne zaman kullanmalı)

ForgeLM ingestion (`forgelm/ingestion.py`) dört chunking stratejisi destekler. `paragraph` stratejisi, çoğu operatörün `sliding`'i çalıştırıp chunk'ların ortada bölündüğünü fark ettikten sonra başvurduğudur.

Ingestion YAML bloğu ile değil, CLI bayrakları ile yapılandırılır — `ForgeConfig`'te üst-düzey `ingestion:` anahtarı yoktur. Chunker seçimi `forgelm ingest` çağrısında yapılır:

```shell
$ forgelm ingest INPUT_PATH \
    --output data/policies.jsonl \
    --strategy paragraph        # sliding | paragraph | markdown | semantic
    --chunk-size 1024           # paragraph için yumuşak cap; sliding için sert cap
    --overlap 128               # yalnızca sliding; paragraph yok sayar
```

### Performans karakteristikleri

| Strateji | Geçiş sayısı | Bellek | Çıktı sayısı | Ne zaman kullanılır |
|---|---|---|---|---|
| `sliding` | 1 | O(chunk_size) | yüksek (üst üste binen pencereler) | Pencere-seviyesi aramanın anlamsal sınırlardan daha önemli olduğu uzun-context retrieval |
| `paragraph` | 1 | O(longest_paragraph) | orta (paragraf cluster başına bir chunk) | Örneklerin ortada başlamaması gereken SFT corpus'ları |
| `markdown` | 1 | O(longest_section) | düşük (heading başına bir chunk) | Heading-breadcrumb'ların önemli olduğu yapılandırılmış teknik dokümanlar |
| `semantic` | n/a — `NotImplementedError` | n/a | n/a | Yol haritalı follow-up faz; embedding-model maliyeti henüz haklı değil |

### Paragraph-chunker invariant'ı

Greedy paragraph packer'ı (`forgelm/ingestion.py` içinde `_chunk_paragraph`), **bir paragrafı asla cümle ortasında bölmez**. `chunk_size`'dan uzun paragraflar bütün halinde yayınlanır — `chunk_size` sert cap değil, yumuşak cap olur. Bu tasarımdır: ortada başlayan bir SFT örneği modeli ortada başlamaya eğitir.

Maliyet: chunk'lar uniform uzunlukta değildir. Downstream pipeline'ınız "her chunk tam olarak 1024 token" varsayıyorsa, `sliding` kullanın.

### Ne zaman `paragraph`, `sliding`'den hızlıdır

- Girdiniz iyi-ayrılmış paragraflara sahiptir (`\n\n` ayraçlar). `paragraph` bir greedy pass çalıştırır; `sliding`, `len(text) / (chunk_size - overlap)` pencere yayını çalıştırır.
- Üst üste binen context'e ihtiyacınız yok. Overlap, bilgiyi iki katına çıkarmadan çıktı sayınızı iki katına çıkarır.

### Ne zaman `sliding`, `paragraph`'tan hızlıdır

- Girdiniz tek bir uzun paragraftır (`\n\n` yok). `paragraph` tüm girdiye eşit bir chunk yayınlar; `sliding` yönetilebilir bir liste yayınlar.
- Pencere-seviyesi recall'un önemli olduğu ve overlap'in amaç olduğu bir retrieval index'i besliyorsunuz.

## GaLore + 4-bit + sample packing trade-off'ları

Operatörlerin model + sequence length sığmadığında başvurduğu üç ortogonal bellek kolu. Dürüst trade-off tablosu:

| Kol | Tasarruf edilen bellek | Hız değişimi | Kalite değişimi | NE ZAMAN kullanmamalı |
|---|---|---|---|---|
| **4-bit quant** (`bitsandbytes` üzerinden NF4) | Ağırlıklarda ~%75 | ~%5-15 daha yavaş forward | Çoğu görevde küçük doğruluk düşüşü; matematik / kod'da görünür | Matematik + kod SFT — önce ölçün |
| **GaLore** (düşük rütbeli gradyan projeksiyonu) | Optimizer state'inde ~%30-50 | Adım başına ~%10-20 daha yavaş | Çoğu görevde rank ≥ 256 ile kabaca korunur | Projeksiyon matrisinin gradyana hakim olduğu küçük modeller |
| **Sample packing** (kısa örnekleri birleştirme) | ~0 (bellek-nötr) | Kısa-örnek corpus'larında ~%30-50 daha hızlı | Attention mask'leri doğruysa yok; eksikse veri sızıntısı | Attention masking güvenilir değilken; `forgelm audit` ile doğrulayın |

**Bunları birleştirmek.** 4-bit + GaLore workstation-builder yığınıdır: 24 GB kartta 8B model + 32k context. Sample packing, hangi quant + optimizer seçimini yaptıysanız onun üzerine yerleşir — bir model değişikliği değil, input-pipeline değişikliğidir.

**Kaçınılması gereken sahtekâr iddia.** "4-bit'i etkinleştirin, eğitiminiz 4× daha hızlı koşacak." 4-bit modelleri *sığdırır*; *hızlı* yapmaz. Hız, model sığdıktan sonra daha büyük bir batch veya daha uzun bir context'ten gelir.

## Yaygın tuzaklar

### "Lazy import'ları etkinleştirdim ve eğitim aynı hızda"

Lazy import'lar süreç başlangıcını etkiler, eğitim throughput'unu değil. CI veya notebook başlangıcınız yavaş hissediyorsa lazy import yardımcı olur; eğitim adımı yavaş hissediyorsa onun yerine trainer'ı (gradient accumulation, dataloader worker'ları, tensor core kullanımı) profile edin.

### "`safety.batch_size`'ı 64'e çıkardım ve şimdi eval OOM"

Safety classifier tüm batch için aktivasyonları tutar. 64 × ~2 k token × ~32 layer × hidden-size çok fazla aktivasyon belleğidir. 8'e geri düşürün veya CI'da bir config pinlemeden önce donanımınızda gerçek OOM eşiğini ölçün.

### "`paragraph` chunking kullandım ve chunk'larım çok büyük"

Bu kontrattır. `chunk_size`'dan uzun paragraflar bütün halinde yayınlanır. Sert bir cap'e ihtiyacınız varsa, `sliding`'e geçin (ve ortada-cümle bölmelerini kabul edin) veya paragrafları cümle sınırlarında bölen bir pre-pass çalıştırın.

### "GaLore eğitimimi yavaşlattı"

GaLore belleği compute ile takas eder. Projeksiyon matris çarpımı gerçek overhead'dir. Kazanç, normalde sığmayacak bir modeli sığdırmaktır; modeliniz zaten sığıyorsa GaLore bir gerilemedir.

### "audit / verify / library çağrılarını sıkı bir döngüde batch'liyorum ve CPU pegged"

`audit_dataset` bir `workers` parametresi kabul eder — birden fazla corpus üzerinde paralel I/O için kullanın. `verify_audit_log` tasarım gereği tek-thread'lidir (SHA-256 zinciri doğası gereği sıralı); doğrulanacak çok log'unuz varsa, bir verify çağrısı içinde değil, process'ler arasında paralelleştirin.

### "Profiling hot path'imin dataloader olduğunu söylüyor"

`dataloader_num_workers`'ı (eğitim config'inizde) ve `prepare_dataset`'in format-detection maliyetini kontrol edin. JSONL büyük corpus'lar için parquet'ten yavaştır; v0.5.5 yalnızca `forgelm audit --output-format {text,json}` üzerinden text/JSON yayar, dolayısıyla tek seferlik dönüşümü `python -c "import pandas as pd; pd.read_json('audit.jsonl', lines=True).to_parquet('audit.parquet')"` (veya eşdeğer `pandas`/`pyarrow` adımı) ile yapın.

## Ayrıca bakınız

- [`../reference/configuration-tr.md`](../reference/configuration-tr.md) — `evaluation.safety.batch_size`, `evaluation.llm_judge.batch_size`, `ingestion.strategy` ve diğer her düğme.
- [`library_api-tr.md`](library_api-tr.md) — bu düğmeleri Python'dan çağırma.
- [`../standards/coding.md`](../standards/coding.md) — ForgeLM'in dayattığı lazy-import standardı.
- [`ingestion-tr.md`](ingestion-tr.md) — chunker kullanıcı-bakışlı derinlik.
- [`alignment-tr.md`](alignment-tr.md) — GaLore + 4-bit + packing'in ne zaman uygun olduğu.
