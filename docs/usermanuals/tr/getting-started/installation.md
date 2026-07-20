---
title: Kurulum
description: ForgeLM'i PyPI'dan kurun; ingest, ölçek ve export için opsiyonel extra'larla.
---

# Kurulum

ForgeLM, PyPI'da tek bir paket olarak gelir; ağır özellikler için opsiyonel bağımlılık grupları (*extras*) vardır. Çoğu kullanıcı temel kurulumla başlayıp ihtiyaç duydukça extra ekler.

## Gereksinimler

| Gereksinim | Asgari | Önerilen |
|---|---|---|
| Python | 3.10 | 3.11+ |
| OS | Linux, macOS | GPU eğitimi için Linux |
| RAM | 8 GB | 16 GB+ |
| Disk | 5 GB boş | Model cache'leri için 50 GB+ |
| GPU (eğitim) | Ingest/audit için yok | SFT 7B için en az tek 12 GB CUDA GPU |

:::note
**GPU yok mu?** ForgeLM'i ingest, audit, eval hazırlığı ve deployment config üretimi için yine de kullanabilirsiniz — her CPU-only iş akışı aynı `forgelm` komutuyla çalışır.
:::

:::note
**Intel Mac'ler (x86_64) desteklenmiyor.** ForgeLM `torch>=2.4.0` gerektirir (`transformers>=5.3.0` üzerinden gelir) ve PyPI'da x86_64 macOS için `torch>=2.4` wheel'i yok. Apple Silicon, Linux veya Windows kullanın.
:::

## Temel kurulum

```shell
$ pip install forgelm
```

Bu size trainer'ı, altı alignment paradigmasını, değerlendirmeyi, güvenlik skorlamasını, uyumluluk artifact üretimini ve CLI'yı verir. Veriniz zaten JSONL formatındaysa modeli uçtan uca fine-tune etmek için yeterlidir.

Kurulumu doğrulayın:

```shell
$ forgelm --version
$ forgelm --help
```

`python -m forgelm …` eşdeğer bir giriş noktasıdır ve aynı argümanları kabul eder:

```shell
$ python -m forgelm --version
ForgeLM 0.9.1rc1
```

`forgelm` console script'i eksik veya gölgelenmiş olduğunda buna başvurun — conda ortamlarında, CI imajlarında ve editable kurulumlarda sık görülür. Projenin kendi içinde kullandığı biçim de budur.

## Opsiyonel extra'lar

ForgeLM ağır veya nadir gereken bağımlılıkları extra'lara ayırır; ihtiyaç duymadığınız sürece kurulmazlar.

### Doküman ingest'i (`[ingestion]`)

```shell
$ pip install 'forgelm[ingestion]'
```

PDF, DOCX, EPUB, TXT ve Markdown dosyalarını `forgelm ingest` ile SFT-hazır JSONL'a dönüştürmek için. `pypdf`, `python-docx`, `ebooklib` ve birkaç küçük metin işleme kütüphanesini ekler.

### Büyük corpus dedup'ı (`[ingestion-scale]`)

```shell
$ pip install 'forgelm[ingestion-scale]'
```

~50K satırdan büyük corpus'larda near-duplicate tespiti için MinHash LSH (`datasketch` üzerinden) ekler. Varsayılan simhash detector hızlı ve kesin recall sağlar; MinHash milyonlarca satıra ölçeklenir. Sadece ölçek lazımsa kurun.

### GGUF export (`[export]`)

```shell
$ pip install 'forgelm[export]'
```

Yerel inference için (Ollama, llama.cpp) kuantize GGUF export'u ekler. `gguf` writer'ı ve destekleyici kütüphaneleri kurar. Opsiyonel çünkü her iş akışı GGUF ile bitmez — birçok kullanıcı doğrudan vLLM veya TGI'a teslim eder.

### Dağıtık eğitim (`[distributed]`)

```shell
$ pip install 'forgelm[distributed]'
```

Çoklu-GPU eğitim için DeepSpeed ZeRO-2 / ZeRO-3 desteği ekler. Sadece tek GPU'ya sığmayan model eğitiyorsanız gerekli. (Extra adı `distributed`; çektiği gerçek bağımlılık `deepspeed>=0.14.0`.)

### Extra'ları birleştirme

`pyproject.toml` bir `[all]` aggregate tanımlamaz. Gerçekten ihtiyaç duyduğunuz extra'ları virgülle ayırarak listeleyin:

```shell
$ pip install 'forgelm[qlora,eval,tracking,merging,export,ingestion]'
```

Bu kasıtlıdır — DeepSpeed / bitsandbytes / Presidio / sentence-transformers'ı CPU-only bir laptop'a hep birden çekmek nadiren operatörün istediği şey, bu yüzden seçimi explicit tutuyoruz.

## Container kurulumu

Python bağımlılıklarını ana makinenize kurmak istemiyorsanız resmi Docker imajı tüm extra'larla ForgeLM'i paketler:

```shell
$ docker pull ghcr.io/hodetech/forgelm:latest
$ docker run --gpus all -v $PWD:/workspace ghcr.io/hodetech/forgelm:latest \
    forgelm --config /workspace/configs/run.yaml
```

Bir `docker-compose.yml` de yayınlanmıştır; çoklu-servis pattern (eğitim + deney takibi + webhook receiver) için bkz. [Docker Operasyonu](#/operations/docker).

## GPU erişimini doğrulama

GPU eğitimi için kurduysanız CUDA'nın doğru bağlandığını teyit edin:

```shell
$ forgelm doctor
```

`forgelm doctor` 18 probe üzerinde tabular bir pass / warn / fail teşhis raporu üretir:
- `forgelm.install` — kurulumun kendisi import edilebilir ve tutarlı. Gerçek çıktının ilk satırıdır ve bozuk ya da yarım yükseltilmiş bir kurulumu yakalayan probe budur
- Python sürümü (>=3.10 zorunlu, >=3.11 önerilen)
- torch + CUDA varlığı (CPU-only `warn`'dur, `fail` değil)
- `numpy.torch_abi` — NumPy/torch ABI çiftinin uyumlu olması
- GPU envanteri (cihaz başına VRAM, GiB)
- Opsiyonel extras: `qlora`, `unsloth`, `distributed`, `eval`, `tracking`, `export`, `ingestion`, `ingestion-pii-ml`, `ingestion-scale` — eksik extra'lar tam `pip install 'forgelm[<isim>]'` ipucu ile `warn`'lanır
- `pypdf_normalise.turkish` — PDF ingest yolundaki Türkçe metin normalizasyonu
- HuggingFace Hub erişimi (varsa `HF_ENDPOINT` üzerinden; `--offline` ile atlanır)
- Workspace disk alanı (<10 GiB → `fail`, <50 GiB → `warn`)
- `FORGELM_OPERATOR` audit kimliği ipucu (Madde 12)

Bilinçli olarak **`extras.merging` probe'u yoktur**. Model birleştirme, çekirdek `peft` + `torch` bağımlılıkları üzerinde yerel olarak uygulanmıştır; dolayısıyla `merging` extra'sı boş bir no-op'tur (`pyproject.toml` içinde `merging = []`) ve kontrol edecek bir şeyi yoktur. O satırı arıyorduysanız, yokluğu doğrudur ve bozuk bir kurulum işareti değildir.

`--output-format json` yapısal zarf döner (`{"success": bool, "checks": [...], "summary": {pass, warn, fail}}`); `--offline` air-gap modu (ağ probe'unu atlar, yerel HF cache'i inceler).

:::tip
Yeni bir ortamda `forgelm --config ...` koşmadan *önce* `forgelm doctor` çalıştırın. Eksik CUDA kütüphanelerini, sürüm uyumsuzluklarını ve "GPU bulunamadı" hatalarını eğitime saatler kala değil saniyeler içinde yakalar.
:::

## Sık karşılaşılan kurulum sorunları

:::warn
**macOS / Apple Silicon'da `bitsandbytes` import hatası.** `bitsandbytes` şu an Metal/MPS'i desteklemiyor. macOS'ta ForgeLM full precision eğitime düşer. 4-bit kuantize eğitim (QLoRA) için CUDA GPU'lu bir Linux makine gerekir.
:::

:::warn
**`undefined symbol: __cudaRegisterFatBinaryEnd`.** PyTorch ve CUDA toolkit sürümleriniz uyumsuz. PyTorch'u CUDA sürümünüze uyacak şekilde tekrar kurun: `pip install torch --index-url https://download.pytorch.org/whl/cu121` (`cu121`'i kendi CUDA sürümünüzle değiştirin).
:::

:::warn
**`OSError: HuggingFace token not found`.** Bazı modeller (ör. Llama 3, Llama Guard) HuggingFace access token gerektirir. `huggingface-cli login` ile veya `HF_TOKEN` environment variable üzerinden ayarlayın. Tüm auth env var'ları için bkz. [CLI Referansı](#/reference/cli).
:::

## Sonraki adımlar

ForgeLM kurulu olduğuna göre uçtan uca eğitim turu için [İlk Koşunuz](#/getting-started/first-run)'a geçin — yaklaşık 5 dakikalık okuma ve 30 dakikalık GPU süresinde fine-tuned bir modeliniz olacak.
