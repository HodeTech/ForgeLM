---
title: Doküman Ingest'i
description: PDF, DOCX, EPUB, TXT ve Markdown'ı tek komutla SFT-hazır JSONL'a dönüştürün.
---

# Doküman Ingest'i

Çoğu fine-tuning veri seti JSONL olarak başlamaz — PDF'ler, sözleşmeler, EPUB'lar veya dağınık Markdown notları olarak başlar. `forgelm ingest` girdi dizininizi gezer, metni format-bilinçli şekilde çıkarır ve SFT-hazır JSONL üretir.

```mermaid
flowchart LR
    A[Girdi dizini<br/>PDF/DOCX/EPUB/MD] --> B[Format algıla]
    B --> C[Metin çıkar<br/>yapıyı koru]
    C --> D[Token bazlı veya<br/>Markdown bazlı parçala]
    D --> E[Maskeleme uygula<br/>PII + sırlar]
    E --> F[JSONL çıktı]
    classDef io fill:#1c2030,stroke:#0ea5e9,color:#e6e7ec
    classDef step fill:#161a24,stroke:#f97316,color:#e6e7ec
    class A,F io
    class B,C,D,E step
```

## Hızlı örnek

```shell
$ forgelm ingest ./policies/ \
    --recursive \
    --strategy markdown \
    --chunk-tokens 1024 \
    --all-mask \
    --output data/policies.jsonl
✓ 47 dosya tarandı (12 PDF, 8 DOCX, 27 MD)
✓ 12,240 chunk çıkarıldı (ortalama 743 token)
✓ 18 PII eşleşmesi maskelendi, 0 sır
✓ data/policies.jsonl yazıldı (8.2 MB)
```

`--all-mask`, `--secrets-mask --pii-mask`'in doğru sıradaki belgelenen
kısayoludur. Tam davranış ve set-union semantiği için
[Birleşik Maskeleme](#/data/all-mask)'ye bakın.

## Desteklenen formatlar

| Format | Çıkarıcı | Notlar |
|---|---|---|
| **PDF** | `pypdf` | Header/footer dedup, tablo çıkarma (best-effort). |
| **DOCX** | `python-docx` | Tablolar Markdown tablo olarak; başlık hiyerarşisini korur. |
| **EPUB** | `ebooklib` | Navigasyon/içindekileri çıkarır; bölüm yapısını korur. |
| **TXT** | yerleşik | Tek doküman olarak işlenir; `--chunk-tokens` ile parçalanır. |
| **Markdown** | yerleşik | Markdown-bilen splitter başlık hiyerarşisine saygı duyar. |

Ingestion extra'larını kurun: `pip install 'forgelm[ingestion]'`. Bkz. [Kurulum](#/getting-started/installation).

## Parçalama stratejileri

Sunulan `--strategy` seçenekleri: `sliding`, `paragraph`, `markdown`.
(`tokens` ve `sentence` daha önce tasarımdaki adlardı, parser'a
hiç inmedi.)

| Strateji | Davranış | En iyi |
|---|---|---|
| `sliding` | `--chunk-tokens` sınırı + `--overlap-tokens` örtüşmesi ile kayar pencere; tokenizer yoksa karakter moduna düşer. | Düz metin, karışık içerik. |
| `markdown` | `#`/`##`/`###` sınırlarına saygı duyan ve fenced code bloklarını atomik tutan başlık-bilen splitter. | Dokümantasyon, yapılandırılmış corpus. |
| `paragraph` | Paragraf başına bir chunk; tasarımdan örtüşmesizdir (`--overlap 0` veya bayrağı vermeyin). | Kitaplar, prose. |

`semantic` follow-up faz için ayrılmıştır — bugün
`NotImplementedError` raise eder ve runtime crash'ı önlemek için
CLI yüzeyinden gizlidir.

## Çıktı formatı

`forgelm ingest` ham chunk'ları emit eder (`{"text": "..."}` JSONL). v0.7.0'da `--format` flag'i yoktur — tek seçenek **özet raporu** (chunk sayısı, format dağılımı, atılan-satır sebepleri) için `--output-format {text,json}`'dur, chunk kayıtlarının kendileri için değil — onlar her zaman ham `text` JSONL olarak yazılır. Sentetik-prompt veya Q&A datasetleri isteyen operatörler bu adımı bu komutun ürettiği ham JSONL üzerinden downstream bir adım olarak katmanlar (bkz. [Sentetik Veri](#/data/synthetic-data)):

```json
{"text": "Bölüm 4.2: Tüm ödeme işlemleri PCI-DSS standartlarına uymalıdır...", "metadata": {"source": "policy.pdf", "chunk": 17}}
```

## CLI bayrakları

Yetkili liste için `forgelm ingest --help`. En sık görülenler:

| Bayrak | Açıklama |
|---|---|
| `--output FILE` | Hedef JSONL dosyası (parent dizinler oluşturulur). Zorunlu. |
| `--recursive` | Alt dizinlere yürü. Varsayılan sığ (yalnız üst düzey dosyalar). |
| `--strategy {sliding,paragraph,markdown}` | Parçalama stratejisi (varsayılan: `paragraph`). |
| `--chunk-tokens N` | Chunk başına token sınırı (`--tokenizer` kullanır). `sliding` için `--overlap-tokens` ile eşleştirin. |
| `--chunk-size N` | Chunk başına yumuşak karakter sınırı (kütüphane varsayılanı 2048). Ya bunu **YA DA** `--chunk-tokens`, ikisini birden değil. |
| `--overlap N` | `--strategy sliding` karakter-modu örtüşmesi (varsayılan 200; `paragraph` veya `markdown` için 0/unset olmalı — tasarımdan örtüşmesizler). |
| `--overlap-tokens N` | `--strategy sliding` + `--chunk-tokens` ile eşli token-modu örtüşmesi. |
| `--tokenizer MODEL_NAME` | `--chunk-tokens` / `--overlap-tokens` tarafından kullanılan HF tokenizer'ı. |
| `--pii-mask` | E-posta, telefon, ID, IBAN'ı yazmadan önce maskele. Bkz. [PII Maskeleme](#/data/pii-masking). |
| `--secrets-mask` | AWS anahtarları, GitHub PAT'ler, JWT'leri vb. redakte et. Bkz. [Sırlar](#/data/secrets). |
| `--all-mask` | `--secrets-mask --pii-mask`'in birleşik kısayolu. |

### Faz 15 (v0.6.0) eklemeleri

| Bayrak | Açıklama |
|---|---|
| `--language-hint LANG` | Her dosya ekstraksiyonundan sonra Unicode-block sanity kontrolü çalıştırır (`tr` / `en` / `de` / `fr` / `es` / `it` / `pt`). Out-of-script karakter oranı kalibre edilmiş %1.5 eşiğini aştığında WARNING tetikler. Kapalıyken no-op. |
| `--script-sanity-threshold X` | Varsayılan %1.5 sanity eşiğini geçersiz kıl (aralık `[0.0, 1.0]`). |
| `--normalise-profile {turkish,none}` | Çıkarılan metne dil-spesifik glyph normalizasyon tablosu uygular. `--language-hint tr` set edilince otomatik `turkish` türetilir; aksi halde `none`. Açık değer kazanır. |
| `--no-normalise-unicode` | `--normalise-profile none` için kısayol. |
| `--no-quality-presignal` | Çalışma sonu kalite ön-sinyalini (alpha / weird-char / repeated-line ucuz kontrolleri) atla. Default AÇIK. |
| `--epub-no-skip-frontmatter` | EPUB nav / cover / copyright / colophon / titlepage / frontmatter öğelerini JSONL'da tut. Default bunları atlar. |
| `--keep-md-frontmatter` | Markdown dosyalarının başındaki `---\n…\n---\n` YAML frontmatter'ı koru. Default strip eder. |
| `--strip-pattern REGEX` | Operatör kontrolünde regex stripping (tekrarlanabilir). Pattern'lar önceden ReDoS-doğrulamadan geçer (iç içe sınırsız quantifier + DOTALL altında `.*?` + back-ref şekilleri reddedilir) ve POSIX'te pattern başına 5-saniyelik SIGALRM bütçesinde çalışır. |
| `--strip-pattern-no-timeout` | Pattern başına SIGALRM timeout'u devre dışı bırak. |
| `--page-range START-END` | PDF ekstraksiyonunu sürekli 1-indeksli sayfa aralığına sınırla. Doğrulama hatası `EXIT_CONFIG_ERROR (1)` ile durur. |
| `--keep-frontmatter` | Default-AÇIK PDF front-matter / back-matter heuristic'inden opt-out (alpha < 0.45 + underscore > 0.10 + ≥ 5 sayfa numarası → ilk + son 12 sayfaya kadar düşür). |
| `--strip-urls {keep,mask,strip}` | Inline URL davranışı: `keep` (varsayılan), `mask` (`[URL]` placeholder), `strip` (sil). `--all-mask`'ten bağımsız. |

## Sık hatalar

:::warn
**`--pii-mask`'i unutmak.** Varsayılan *maskelememe*; "verinizi sessizce değiştirmeyiz" prensibinden. Gerçek corpus için açıkça etkinleştirin. Audit aşaması ([Veri Seti Denetimi](#/data/audit)) PII'yi her halükarda flagler ama ingest'te maskelemek daha iyidir.
:::

:::warn
**Saf-resim PDF'ler.** ForgeLM OCR yayınlamıyor. PDF'leriniz taranmış görüntüyse önce Tesseract veya ticari bir OCR'dan geçirin.
:::

:::warn
**`--chunk-tokens`'i modelin context'inden büyük yapmak.** `model.max_length`'ten uzun chunk'lar eğitim zamanında kuyruktan kesilir. `--chunk-tokens`'i eğitim context'iyle eşleştirin.
:::

:::tip
**Ingest'ten sonra her zaman audit yapın.** Temiz ingest temiz dataset anlamına gelmez. Split-arası sızıntı, near-duplicate ve maskelemenin kaçırdığı PII için `forgelm audit data/output.jsonl` koşturun.
:::

## Bkz.

- [Veri Seti Denetimi](#/data/audit) — ingest'ten sonraki adım.
- [PII Maskeleme](#/data/pii-masking) — maskelemenin nasıl çalıştığı.
- [Dataset Formatları](#/concepts/data-formats) — her çıktı formatı.
