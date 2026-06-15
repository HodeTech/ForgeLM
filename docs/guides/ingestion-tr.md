# Doküman Yutma Rehberi

Ham kurumsal külliyatı (PDF / DOCX / EPUB / TXT / Markdown) ForgeLM'in
eğittiği SFT-uyumlu JSONL'a dönüştürün. `v0.5.0` ile birlikte gelir
(Faz 11 + 11.5 + 12 + 12.5 birleşmiş): paragraph / sliding /
markdown-aware splitter, token-aware chunking, PDF sayfa header/footer
dedup, code/credential scrubbing (`--secrets-mask`), DOCX tablolarının
markdown syntax'ı ile çıkarılması, `--all-mask` kısayolu.

> Sonrasında [`forgelm audit`](data_audit-tr.md) ile uzunluk dağılımı /
> dil / near-duplicate / PII metriklerini yüzeye çıkarın; chunk'ları Q&A
> `messages` formuna genişletmek için
> [`forgelm --generate-data`](../reference/usage-tr.md) ile zincirleyin.

---

## Kurulum

```bash
pip install 'forgelm[ingestion]'
```

Bu opsiyonel grup `pypdf`, `python-docx`, `ebooklib`, `beautifulsoup4`,
`langdetect` ve (Faz 11.5'ten itibaren) opsiyonel non-cryptographic
hızlı `xxhash` backend'i getirir. Düz metin + Markdown için bu
paketlerin hiçbiri gerekmediği için opsiyonel tutuldu. Modülün kendisi
import edildiğinde ilgili extractor çağrılmadığı sürece bu bağımlılıkları
yüklemez.

OCR **kapsam dışındadır.** Metin katmanı olmayan taranmış PDF'ler bir
uyarı yüzdürür ve sıfır chunk üretir; ingest etmeden önce Tesseract veya
AWS Textract ile ön işleyin.

---

## Tek komut — dosya gir, JSONL çık

```bash
forgelm ingest ./book.epub --output data/sft.jsonl
forgelm ingest ./policies/ --recursive --output data/policies.jsonl
forgelm ingest ./scan.pdf --strategy sliding --chunk-size 1024 --overlap 128 \
  --output data/scan.jsonl

# Faz 12 — heading-aware splitter + secrets/PII maskeleme tek pass
forgelm ingest ./engineering_wiki/ --recursive \
  --strategy markdown --chunk-size 600 \
  --secrets-mask --pii-mask \
  --output data/wiki.jsonl
```

Çıktı satır başına bir chunk:

```json
{"text": "EU AI Act Madde 10, yüksek-riskli AI sağlayıcılarından ..."}
{"text": "Veri kalitesi kriterleri arasında uygunluk, temsil edicilik ..."}
```

Trainer'ın veri yükleyicisi `{"text": "..."}` formatını önceden
formatlanmış SFT sütunu olarak tanır (bkz.
[`forgelm/data.py`](../../forgelm/data.py)) — başka bir ön işleme
gerekmez.

---

## Chunking stratejileri

| Strateji | Ne zaman | Davranış |
|---|---|---|
| `paragraph` (varsayılan) | Düz yazı, politika dokümanları, makaleler | Açgözlü paragraf paketleyici; bir paragrafı asla yarıda bölmez. |
| `sliding` | Uzun teknik dokümanlar, prose ile karışık kod | Sabit boyutlu karakter penceresi + bağlam taşıması için `--overlap`. |
| `markdown` (Faz 12) | Heading hiyerarşili dokümanlar (rehberler, README, wiki sayfaları) | Heading sınırlarında böler; fenced kod bloklarını (` ``` ` veya `~~~`) atomik tutar; her chunk'ın başına heading **breadcrumb**'ını inline eder ki SFT loss doküman bağlamını görsün. Non-overlapping (sections are atomic). |

> Embedding tabanlı semantik chunking takip eden bir faza ayrılmıştır —
> bugün `NotImplementedError` raise eder ve runtime crash'i önlemek için
> CLI `--strategy` choice listesinden bilinçli olarak gizlenmiştir.

`--chunk-size` **karakter olarak** ölçülür, token değil. Kaba bir kural
olarak `--chunk-size 2048` tipik İngilizce / Türkçe metinde ≈500–700
token'a karşılık gelir — değeri `model.max_length` ile uyumlu seçin
(örneğin `max_length: 2048` token'lı bir model `--chunk-size 6000-8000`
ile rahat bir manevra alanı bulur, çünkü formatter sistem prompt + chat
template overhead'ine yer ayırır). Soft cap'i aşan paragraflar tek
başlarına yayılır — yarıda bölmekten daha iyidir.

**Sliding overlap sınırlıdır.** `--overlap` hem `< --chunk-size` hem de
`≤ --chunk-size // 2` olmalıdır — bunun üstündeki değerler chunk
sayısını patlatır (`--overlap 199 --chunk-size 200` kombinasyonu
karakter başına ~bir chunk üretir). Patolojik kombinasyonları CLI
önden reddeder.

**Dosyalar leksikografik sırayla işlenir** (sıralanmış glob sonucu),
böylece aynı girdi + bayraklarla yeniden çalıştırma byte-byte aynı
JSONL'i üretir.

---

## Yazma sırasında PII maskeleme

Tespit edilen e-posta, telefon, Luhn-doğrulanmış kredi kartı, IBAN ve
ulusal kimlik numaralarını (TR Kimlik No, DE Personalausweis, FR INSEE,
US SSN) chunk'lar JSONL'a inmeden önce redact etmek için `--pii-mask`
ekleyin:

```bash
forgelm ingest ./customer_emails/ --output data/anon.jsonl --pii-mask
```

Tespit edilen span'ler `[REDACTED]` ile değiştirilir. Tespit regex
tabanlıdır — false positive'ler kasıtlıdır. Sonrasında
`forgelm audit` ile çıktıyı doğrulayın.

---

## Yazma sırasında secrets/credential maskeleme (Faz 12)

`--secrets-mask` chunk'lar JSONL'a inmeden önce credentials ve token'ları
temizler — gerçek bir API anahtarı içeren metin üzerinde fine-tune
etmek o anahtarı modelin içine ezberletir.

```bash
# Tek başına
forgelm ingest ./engineering_wiki/ --output data/wiki.jsonl --secrets-mask

# PII maskelemesiyle kombine — secrets önce, PII sonra çalışır ki
# birleşik detector'lar örtüşen span'leri çift saymasın
forgelm ingest ./mixed_corpus/ --output data/clean.jsonl --secrets-mask --pii-mask
```

Detector dar bir prefix-anchored regex seti kullanır (false-positive
oranı bilerek düşük): AWS access key'leri, GitHub PAT'ler, Slack
token'ları, OpenAI API key'leri, Google API key'leri, JSON Web
Token'lar (kanonik header alphabet'ine anchored), tam OpenSSH / RSA /
DSA / EC / PGP private-key blokları (BEGIN'den END'e kadar tüm
gövde redact edilir) ve Azure storage connection string'leri. Tespit
edilen span'ler `[REDACTED-SECRET]` ile değiştirilir.

İsteğe bağlı `[ingestion-secrets]` extra'sı ileriki bir release için
ayrılmıştır — mevcut sürüm `detect-secrets` paketini çağırmaz; yalnızca
yukarıdaki regex seti çalışır.

---

## Hepsi bir arada maskeleme — `--all-mask` (Faz 12.5)

`--all-mask`, `--secrets-mask --pii-mask` için tek-flag kısayoludur —
"paylaşılan korpus üzerinde eğitime başlamadan tespit edilebilir her
şeyi temizle" iş akışının yaygın hâlidir. İki detector belgelenen
sıraya göre çalışır (önce secrets — birleşik PII detector'lar örtüşen
span'leri çift saymasın diye); ortaya çıkan JSONL eşleşme bulunan
yerlerde hem `[REDACTED-SECRET]` hem de `[REDACTED]` belirteçlerini
taşır.

```bash
forgelm ingest ./mixed_corpus/ --recursive --all-mask --output data/clean.jsonl
```

Açık flag'lerle additive birleşir — `--all-mask --pii-mask` hata
değildir; iki flag'in boolean unionu çalışır. Kısayol yalnızca
ergonomi içindir; yeni bir detector eklemez.

---

## Recursive dizin yürüyüşü

```text
./policies/
├── 2024_q1.pdf
├── 2024_q2.pdf
└── archive/
    ├── 2023.docx
    └── 2022.epub
```

```bash
forgelm ingest ./policies/ --recursive --output data/all_policies.jsonl
```

Desteklenmeyen uzantıya sahip dosyalar (`.png`, `.zip`, vb.) sessizce
atlanır. Desteklenen uzantısı olup ekstrakte edilebilir metin içermeyen
dosyalar (taranmış PDF'ler, boş DOCX) bir uyarıyla atlanır.

**Şifreli PDF'ler** açıkça yakalanır: önce boş şifreyle decrypt denenir
(owner-encrypted ama hâlâ okunabilen PDF'leri kapsar). Başarısız olursa
dosya başına extractor `ValueError` raise eder — toplu ingestion
döngüsü bunu "extraction failed" olarak yakalar, dosya adını içeren
bir uyarı log'lar, sonuçtaki `files_skipped`'i artırır ve sonraki
dosyaya geçer. Uyarı metni harici decrypt yolunu önerir:

```bash
qpdf --decrypt --password=<pwd> input.pdf out.pdf
# veya
pdftk input.pdf input_pw <pwd> output out.pdf
```

CLI'a şifre flag'i bilinçli olarak eklenmedi — şifreleri shell
geçmişinden uzak tutmak daha güvenli.

**Metin gibi davranan binary içerik** (zip / image yeniden adlandırılmış
`.txt`) dosyanın %1'inden fazlası Unicode replacement karakteri olarak
decode olduğunda bir uyarı yüzdürür. Chunk'lar yine yazılır —
operatörler kalıp kalmayacaklarına karar verir — ama uyarı CI loglarında
görülecek kadar yüksek seslidir.

---

## Uçtan uca örnek

```bash
# 1. Yutma
forgelm ingest ./policies/ --recursive --output data/policies.jsonl

# 2. Denetim (near-duplicate'leri, PII'yi, uzunluk aykırılarını yakalar)
forgelm audit data/policies.jsonl --output ./audit/

# 3. (opsiyonel) Öğretmen modelle Q&A'ya genişletme
forgelm --config configs/synth.yaml --generate-data

# 4. Eğitim
forgelm quickstart domain-expert --dataset data/policies.jsonl
```

---

## CLI referansı

```text
forgelm ingest INPUT_PATH \
  --output FILE \
  [--chunk-size N | --chunk-tokens N --tokenizer MODEL_NAME] \
  [--overlap N] \
  [--overlap-tokens N] \
  [--strategy {sliding,paragraph,markdown}] \
  [--recursive] \
  [--pii-mask] \
  [--secrets-mask] \
  [--all-mask] \
  [--language-hint LANG] \
  [--script-sanity-threshold X] \
  [--normalise-profile {turkish,none} | --no-normalise-unicode] \
  [--no-quality-presignal] \
  [--epub-no-skip-frontmatter] \
  [--keep-md-frontmatter] \
  [--strip-pattern REGEX ...] \
  [--strip-pattern-no-timeout] \
  [--page-range START-END] \
  [--keep-frontmatter] \
  [--strip-urls {keep,mask,strip}] \
  [--output-format {text,json}] \
  [--quiet | --log-level {DEBUG,INFO,WARNING,ERROR}]
```

`--strategy markdown` (Faz 12) heading-aware splitter'ı seçer; her
chunk'ın başına heading breadcrumb'ı inline eder. `--secrets-mask`
(Faz 12) AWS / GitHub / Slack / OpenAI / Google / JWT / OpenSSH /
PGP / Azure credential span'lerini chunk'lar JSONL'a inmeden önce
`[REDACTED-SECRET]` ile değiştirir.

`--output-format json` standart çıktıya makine-okunabilir bir özet
yazar. Zarf Faz 11.5 alanlarını (`notes`, `notes_structured`,
`pdf_header_footer_lines_stripped`), Faz 12 alanlarını
(`secrets_redaction_counts`) ve **Faz 15** ek alanlarını taşır:
`pdf_paragraph_packed_lines_stripped` (ikinci-pas dedup'ın sıyırdığı
sağkalan header sayısı), `script_sanity_triggered` (eşiği aşan dosya
sayısı), `strip_pattern_substitutions` (`--strip-pattern` eşleşme
sayısı), `urls_handled` (`--strip-urls` ile mask/strip edilen URL
sayısı) ve `frontmatter_pages_dropped` (front-matter heuristic'inin
düşürdüğü sayfa sayısı). CI/CD pipeline'larında kullanışlı.

### Token-aware chunking — `--chunk-tokens` (Faz 11.5)

Karakter tabanlı chunking pratik ama yoğun metinde modelinizin
`max_length` token bütçesini aşabilir. `--chunk-tokens N` ile birlikte
`--tokenizer MODEL_NAME` geçirerek chunk'ları aynı tokenizer üzerinden
boyutlandırabilirsiniz:

```bash
forgelm ingest ./policies/ --recursive --output data/policies.jsonl \
  --chunk-tokens 1024 --tokenizer "Qwen/Qwen2.5-7B-Instruct"
```

`--chunk-tokens` set edildiğinde `--chunk-size` görmezden gelinir (bir
uyarı log'lanır). `--overlap-tokens N` token cinsinden sliding overlap
boyutudur ve `--overlap` ile aynı yarım-pencere üst sınırına tabidir.
`--chunk-tokens` ile `--tokenizer` zorunludur — varsayılan vocab
seçmiyoruz çünkü chunk sayısı modelden modele sessizce değişirdi.

### PDF sayfa header/footer dedup (Faz 11.5 + Faz 15 Görev 1)

`forgelm ingest`, bir PDF'in sayfalarının ≥ %70'inde ilk veya son
birkaç boş-olmayan satır olarak tekrarlayan satırları (şirket
filigranı, copyright satırı, sayfa numarası vb.) chunk'lamadan önce
otomatik olarak temizler.

**Faz 15 Görev 1** inceleme penceresini sadece en dıştaki satırdan
sayfa başına **üst-3 / alt-3 satıra** genişletir. Bu sayede, dış
satırı değişken (bölüm başlığı gibi) ama bir alttaki satırı sabit
(yayıncı kimliği gibi) olan bir corpus tamamen temizlenir. Faz 15
öncesi implementasyon, en dış satır sınıra ulaşmadığında pas 1'de
döngüden çıkıyordu ve audit'in 2026-05-11 pilot corpus'unda 74 / 82
chunk'a sızan sabit kimlik satırını gözden kaçırıyordu.

Paragraph paketlemenin ardından ikinci bir pas, chunker'ın orta-bloğa
yapıştırdığı sağ-kalan header'ları temizler — yapılandırılmış notların
`pdf_paragraph_packed_lines_stripped` alanı bu pas'ı raporlar.

Her iki pas da otomatiktir; flag yok, "PDF olmayan dosya gönder"
dışında opt-out yok. 3 sayfadan kısa belgelerde devre dışı kalır.
Yapılandırılmış notlar çıktısı `pdf_header_footer_lines_stripped`
alanını taşır.

### Script-sanity kontrolü + glyph normalizasyonu (Faz 15 Görevler 2 + 3)

Pypdf, custom glyph adları içeren fontlarla bezenmiş PDF'lerde
zaman zaman font-fallback artefaktları üretir (audit, gerçek bir
Türkçe pilotta `ø Õ ú ÷ ࡟` ölçtü). v0.6.0 iki çözüm sunar:

- **`--language-hint LANG`** her dosya ekstraksiyonundan sonra bir
  Unicode-block sanity kontrolü çalıştırır. Out-of-script karakter
  oranı kalibre edilmiş %1.5 eşiğini geçtiğinde (`--script-sanity-threshold`
  ile ayarlanabilir), bir WARNING tetiklenir + yapılandırılmış
  `script_sanity_summary` bloğu `notes_structured` içine düşer.
  Desteklenen diller: `tr`, `en`, `de`, `fr`, `es`, `it`, `pt`
  (CJK / Arapça Faz 16+'ya ertelendi).
- **`--normalise-profile {turkish,none}`** çıkarılmış metne
  dil-spesifik bir glyph normalizasyon tablosu uygular. Modül-düzeyi
  varsayılan **`none`** (Faz 15 round-2 / C-2: dil ipucu olmayan
  korpusların sessizce yeniden yazılmasını önlemek için). Operatör
  `--language-hint tr` geçirdiğinde dispatcher (CLI ve kütüphane API
  her ikisinde de) otomatik olarak `turkish` profiline türetir; her
  diğer ipucu (veya ipuçsuz çalışma) `none` kalır. Açık
  `--normalise-profile turkish` her zaman kazanır.
  `--no-normalise-unicode` veya `--normalise-profile none`
  normalizasyonu tamamen devre dışı bırakır. Tablonun yüklendiğini
  doğrulamak için `forgelm doctor`'ı çalıştırın — profile sağlıklı
  olduğunda `pypdf_normalise.turkish: pass` satırını verir.

### Ingest sırasında kalite ön-sinyali (Faz 15 Görev 4)

Her çalışmanın sonunda `forgelm ingest`, üretilen her chunk'a üç ucuz
satır-seviyesi kontrol (alpha oranı, garip karakter oranı,
tekrarlanan satır oranı) uygular ve bir chunk eşiğin altına düştüğünde
tek satırlık bir nudge yazar:

```text
[WARN] 74/82 chunks below ingestion quality threshold. Run
       `forgelm audit ./out.jsonl` for detail.
```

Tam tanılama `forgelm audit --quality-filter` (v0.6.0'dan itibaren
default-on) altında yaşamaya devam eder; ön-sinyal yalnızca "hey, buna
bakmak isteyebilirsin" mümkün olan en küçük uyarıdır. `--no-quality-presignal`
ile kapatılır. Yapılandırılmış payload `notes_structured.quality_presignal`
altında `samples_evaluated`, `samples_flagged` ve check-bazında
sayıları taşır.

### DOCX header / footer çıkarma (Faz 15 Görev 6)

Word belgeleri her section'ın `<w:hdr>` / `<w:ftr>` parçaları altında
tekrarlayan başlık ve altbilgileri açık-açık bildirir. v0.6.0 bu
parçaları önceden okur ve satırlarını body extraction'ından çıkartır,
böylece 10 sayfa boyunca tekrarlanan 3-satırlık bir header **sıfır**
header satırı JSONL'a düşürür.

### EPUB spine sırası + nav / cover / copyright atlama (Faz 15 Görev 7)

EPUB ekstraksiyonu `book.spine`'ı (okuma sırası) `book.get_items()`
(dosya sırası) yerine iterate eder; bölümler doğru sırayla iner.
Varsayılan skip-list dosya adı veya `epub:type` değeri `nav`, `cover`,
`copyright`, `colophon`, `titlepage` veya `frontmatter` ile eşleşen
item'ları filtreler — TOC boilerplate'inin pure noise olduğu SFT
eğitiminde işe yarar. `--epub-no-skip-frontmatter` ile opt-out.

### TXT BOM + Markdown YAML frontmatter (Faz 15 Görev 8)

TXT dosyaları `encoding="utf-8-sig"` ile okunur, böylece dosyanın
başındaki bir UTF-8 BOM chunk'lamadan önce transparent biçimde
strip edilir. Markdown dosyaları ayrıca `^---\n…\n---\n` YAML
frontmatter'ı tespit edip varsayılan olarak strip eder — metadata
bloğunda eğitim *istediğinizde* `--keep-md-frontmatter` ile geri
açabilirsiniz.

### Operatör strip-pattern'leri — `--strip-pattern REGEX` (Faz 15 Wave 2 Görev 11)

Dedup heuristiği'nin gözden kaçırdığı bilinen boilerplate (değişken
running header, DOI satırı, watermark) için escape hatch. Pattern
başına bir kez `--strip-pattern REGEX` geçirin; eşleşmeler
chunk'lamadan önce çıkarılmış metinden silinir:

```bash
forgelm ingest ./corpus/ --output data/clean.jsonl \
  --strip-pattern '^Confidential — internal use only$' \
  --strip-pattern '^https://example\.com/qr\?KOD=\d+$'
```

Her pattern **önceden yapısal olarak doğrulanır**: iç içe sınırsız
quantifier'lar (`(a+)+b`) ve DOTALL altında `.*?` + geri-referans
(SonarCloud `python:S5852` polinomial-runtime şekli) `EXIT_CONFIG_ERROR`
ile reddedilir. POSIX'te per-pattern 5-saniyelik SIGALRM bütçesi
en kötü durum eşleşme maliyetini sınırlar (pattern'lerinizin lineer
olduğunu bağımsız doğruladığınızda `--strip-pattern-no-timeout` ile
opt-out edebilirsiniz).

### `--page-range START-END` (Faz 15 Wave 2 Görev 12)

PDF ekstraksiyonunu sürekli bir sayfa dilimine sınırlar (1-indeksli,
inclusive). Heuristik front-matter'ı gözden kaçırdığında veya yalnızca
belirli bir bölümü istediğinizde kullanışlıdır:

```bash
forgelm ingest ./book.pdf --output data/ch3.jsonl --page-range 50-90
```

Doğrulama hataları (`start < 1`, `start > end`, `start > page_count`)
çalışmayı `EXIT_CONFIG_ERROR` (1) ile sonlandırır; CI/CD pipeline'ları
herhangi bir operatör-sağlamış parametre hatası için aynı şekilde
branch eder.

### PDF front-matter / back-matter heuristiği (Faz 15 Wave 2 Görev 13, varsayılan AÇIK)

v0.6.0, PDF'in ilk 12 / son 12 sayfasında üç-sinyalli bir heuristic
etkinleştirir: bir sayfanın alpha oranı < 0.30 (round-4'te 0.45'ten
sıkıştırıldı) VE leader oranı > 0.10 (uzunluğu ≥ 3 olan `_` + `.`
çalışmalarının birleşimi) VE ≥ 5 inline `\n<1-3 rakam>\n` sayfa
numarası eşleşmesi varsa, sayfa düşürülür ve indeksleri listeleyen
bir WARNING tetiklenir. ToC / masthead / index / glossary
boilerplate'ini yakalar.

`--keep-frontmatter` ile Faz 15 öncesi "her şeyi tut" davranışına
geri dönülür. Yapılandırılmış notlar `frontmatter_pages_dropped`
raporlar; downstream audit operasyonu spot-check edebilir.

> **Kalibrasyon uyarısı (round-5 bağımsız review).** Heuristic,
> audit'in pilot Türkçe-textbook ToC şekli için kalibre edilmiştir:
> tek-kelimelik bölüm başlıkları + ağır nokta leader'lar + inline
> sayfa numaraları (pilot sayfalarda alpha ≈ 0.15). Tam-cümle bölüm
> başlıkları olan gerçekçi İngilizce ToC'lar (`Chapter 1: Introduction
> to the Subject ……… 14`) alpha ≈ 0.47 ölçer, 0.30 eşiğin üzerinde,
> ve **değişmeden geçer**. Bu tür corpus'lar üzerinde çalışan
> operatörler ya (a) front-matter heuristic'i atlayacağını kabul edip
> `--strip-pattern` ile downstream'de budamalı, ya da (b) front-matter'ı
> manuel olarak atlamak için `--page-range 12-N` geçirmelidir.
> 3-sinyalli AND filtresi (alpha + leader + inline-sayfa-numarası)
> aynı zamanda gerçekçi form template / egzersiz sayfalarını da
> korur — bunlar inline-sayfa-numarası sinyalinden yoksundur ve
> drop'u asla tetiklemez.

### `--strip-urls {keep,mask,strip}` (Faz 15 Wave 2 Görev 14)

QR-kod referansları, DOI altbilgileri veya modelin ezberleyebileceği
sosyal-medya linkleri gömen corpus'lar için URL davranışı:

- `keep` (varsayılan) — URL'leri olduğu gibi geçirir.
- `mask` — her URL'yi `[URL]` placeholder'ı ile değiştirir.
- `strip` — URL'leri tamamen siler.

URL yönetimi bilinçli olarak `--all-mask` (Faz 12.5 PII + secrets
kısayolu) ile **bağımsızdır**. URL stripping bir content-shape
kararıdır, GDPR redaksiyonu değildir, ve iki flag ailesi ortogonal
kalır.

### Çok-kolonlu PDF uyarısı (Faz 15 Wave 2 Görev 15)

İki-kolonlu akademik makaleler, hükümet regülasyon yayınları ve
çok-kolonlu hukuki layoutlar pypdf'in metin-ekstraksiyon okuma
sırasını şaşırtır. v0.6.0 ilk üç sayfanın metin pozisyonlarını
pypdf'in `visitor_text` callback'i üzerinden örnekler ve sayfa
genişliğinin %30'undan büyük iki-küme boşluğu tespit ettiğinde bir
WARNING tetikler:

```text
[WARN] Detected 2-column layout in 'paper.pdf' — reading order may be
       scrambled. Consider --strategy sliding with a larger --chunk-size,
       or pre-process the PDF with a layout-aware tool.
```

Otomatik fix yok — bu, operatörün strateji değiştirmesi gerektiğine
dair "hiç-yoktan-iyi" bir sinyaldir. Camelot-py / pdfplumber entegrasyonu
Wave 3 backlog'undadır.

> **Bilinen sınırlama (Faz 15 round-3 review).** Mevcut dedektör bir
> sayfadaki tüm çıkarılmış Tj glyph'lerinin min / max x-pozisyonlarını
> karşılaştırır, satır-başlangıç pozisyonlarını değil. pypdf'in
> `visitor_text` callback'i glyph başına ateşlendiği için tek-kolonlu
> bir sayfanın glyph'leri doğal olarak geniş bir x-aralığına yayılır
> (tipik gövde satırında ≈ %64 sayfa genişliği), bu nedenle algoritma
> tek-kolonlu corpus'larda false-positive vermemek için göreceli olarak
> yüksek %30 sayfa-genişlik boşluk eşiği kullanır. Yan etki: tipik
> 5–10 mm oluğa sahip (≈ sayfa-genişliğinin %5–8'i) gerçek dünya
> iki-kolonlu sayfası bugün uyarıyı tetiklemez. Dedektör aşırı layout'larda
> (akademik posterler, geniş oluklu regülasyon yayınları) güvenilirdir
> ama yayın-kalite iki-kolonlu makaleleri kaçırır. Histogram-tabanlı
> bimodal-mode refactor Wave 3 takipi olarak izlenmektedir (bkz.
> [Faz 15 arşivi](../roadmap/completed-phases.md#phase-15--ingestion-pipeline-reliability-v060)
> Wave 3 — multi-column layout extraction).

### Markdown-bilen splitter — `--strategy markdown` (Faz 12)

Girdi gerçek bir markdown yapısına sahipse (teknik wiki'ler, README
koleksiyonları, knowledge-base export'ları), heading-bilen chunking
paragraph-greedy chunking'i geçer: her chunk bir heading ile başlayan
tutarlı bir bölümdür ve **parent heading yolu chunk'ın başına
breadcrumb olarak inline edilir** — böylece SFT loss'u doküman
bağlamını görür.

```bash
forgelm ingest ./engineering_wiki/ --recursive --output data/wiki.jsonl \
  --strategy markdown --chunk-size 4000
```

Davranış notları:

- Sınırlar markdown heading'leri (`# H1` … `###### H6`); chunker bir
  bölümün ortasından asla kesmez.
- Code-fenced blok'lar (` ``` `) atomik tutulur — asla blok ortasından
  bölünmez.  Bu, uzun bir kod listing'i içeren tek bölümün soft-cap'i
  aşabileceği anlamına gelir (paragraph stratejisinin "uzun-paragraph
  tek başına" kuralının aynısı).
- Fenced blok **içindeki** heading-şekilli satırlar (`# whoami`,
  `# noqa: E402`) bölüm sınırı olarak yorumlanmaz.
- Token-bilen mod ile birleşir: `--strategy markdown --chunk-tokens
  1024 --tokenizer "Qwen/Qwen2.5-7B-Instruct"`.

### DOCX tablo koruması (Faz 12)

DOCX tabloları artık önceki `" | "`-birleştirilmiş düz satır yerine
**markdown tablo syntax'ı** olarak çıkarılır (header satırı + `---`
ayraç + body satırları).  `--strategy markdown` ile birleştirildiğinde
tablolar chunk'lar arası bütünlüğünü korur.  Eşit-olmayan satırlar
boş hücrelerle sağdan paddinglenir; tüm-boş satırlar düşürülür; ilk
boş-olmayan satır header olur.  Bunun fark yarattığı SFT use-case'leri:
tablo Q&A, finansal asistan, kod-ile-veri prompt'ları.

---

## Sorun giderme

| Belirti | Sebep | Çözüm |
|---|---|---|
| `ImportError: PDF ingestion requires the 'ingestion' extra` | Opsiyonel grup yüklü değil | `pip install 'forgelm[ingestion]'` |
| "No extractable text in '<x>.pdf'" uyarısı + 0 chunk | Taranmış PDF, metin katmanı yok | Önce OCR (Tesseract / AWS Textract) |
| `FileNotFoundError: No supported files found at '<dir>'` | Dizinde sadece desteklenmeyen uzantı | Dosya uzantılarının `.pdf / .docx / .epub / .txt / .md` ile eşleştiğini doğrulayın |
| `ValueError: overlap must be in [0, chunk_size)` | `--overlap >= --chunk-size` | `--overlap`'i azaltın |

---

## Sınırlamalar

- **OCR:** Kapsam dışı. Harici araçlar kullanın — aşağıdaki örneklere bakın.
- **Tablolar / şekiller:** Faz 12'den (`v0.5.0`'da birleşti) itibaren `_extract_docx()`
  DOCX tablolarını **Markdown tablo syntax'ına** (header + `---` ayraç +
  body satırları) **chunking stratejisi çalışmadan önce, extraction
  aşamasında** dönüştürür — yani tüm stratejiler render edilmiş
  Markdown'ı görür, satır-major düzleştirilmiş bir string'i değil.
  Strateji daha sonra chunk sınırlarının nereye düşeceğini seçer:
  `_markdown_sections()` **yalnızca heading satırlarında** ayırma yapar
  (heading-bilen, tablo-bilen değil). `--strategy markdown` altında
  `_chunk_markdown()` (ve token-bilen ikizi `_chunk_markdown_tokens()`)
  her section'ı bölünmez bir birim olarak korur — bütçeyi aşan bir
  section tek parça (tek chunk) olarak yayınlanır, ortadan
  bölünmez; içindeki tablo, ne kadar büyük olursa olsun section ile
  birlikte taşınır. Büyük bir tabloyu bölmek tablo-bilinçli bir chunker
  veya ayrı bir tablo-splitting aşaması gerektirir; bunların ikisi de
  bugün bağlı değildir. `paragraph` / `sliding` stratejilerinde
  chunker tablo yapısından habersizdir ve paragraf / pencere sınırlarına
  göre çalıştığı için hücre ortasından satır kesebilir. PDF tabloları
  her durumda düzleştirilmiş kalır (PDF tarafında extraction-time tablo
  parser'ı bağlı değil).
- **Metadata:** başlık / yazar / sayfa numarası düşürülür — yalnızca gövde
  metni JSONL'a iner.
- **Encoding:** non-UTF-8 girdi `errors="replace"` ile okunur; binary
  noise Unicode replacement karakterlerine dönüşür.
- **Semantik chunking:** embedding desteği bir sonraki fazda gelene kadar
  `NotImplementedError` raise eder.

---

## Taranmış PDF'lerle çalışma (OCR teslim akışı)

`forgelm ingest` OCR yapmaz. Metin katmanı olmayan taranmış PDF'ler
uyarı yüzdürür ve sıfır chunk üretir. Doğru yol önce harici OCR yapıp
sonucu ingestion'dan geçirmektir. İki tarif, maliyet sırasıyla:

### Tarif A — Tesseract (yerel, ücretsiz)

```bash
# Bir kerelik kurulum.
brew install tesseract            # macOS
# veya: sudo apt install tesseract-ocr tesseract-ocr-tur tesseract-ocr-eng

# 1. Her taranmış PDF sayfasını searchable PDF'e dönüştür (metin katmanı eklenir).
ocrmypdf scan_input.pdf scan_with_text_layer.pdf --language eng+tur

# 2. Şimdi normal text-bearing PDF olarak ingest et.
forgelm ingest scan_with_text_layer.pdf --output data/scan.jsonl
```

`ocrmypdf` önerilen sarmalayıcıdır — deskew, sayfa rotasyonunu yönetir
ve OCR'lı karakterleri görsel katmana yakmak yerine gizli bir metin
katmanı yazar. Yalnızca düz metne ihtiyaç varsa pure `tesseract` da
çalışır:

```bash
# Sayfa-başına çıkarım, TXT'e birleştir, TXT'i ingest et.
pdftoppm -r 300 scan_input.pdf scan_page -png
for img in scan_page-*.png; do tesseract "$img" - >> scan_pages.txt; done
forgelm ingest scan_pages.txt --output data/scan.jsonl
```

### Tarif B — AWS Textract (bulut, ücretli; tablo / formlarda daha iyi)

```bash
# 1. Taranmış PDF'leri S3'e yükle.
aws s3 cp scan_input.pdf s3://my-ingest-bucket/scan_input.pdf

# 2. Async Textract işi başlat. (Polling + SNS notification pattern için
#    AWS Textract dokümanına bakın; tam shell loop kapsam dışı.)
aws textract start-document-text-detection \
    --document-location "{\"S3Object\":{\"Bucket\":\"my-ingest-bucket\",\"Name\":\"scan_input.pdf\"}}"

# 3. İş tamamlandığında LINE blok'larını metne dök ve ingest et.
python -c "
import boto3, sys
client = boto3.client('textract')
job = sys.argv[1]
result = client.get_document_text_detection(JobId=job)
for block in result['Blocks']:
    if block['BlockType'] == 'LINE':
        print(block['Text'])
" $JOB_ID > scan_textract.txt
forgelm ingest scan_textract.txt --output data/scan.jsonl
```

Textract çok-sütunlu sayfalarda (akademik makaleler, dergiler), karışık
dilli sayfalarda ve formlar / tablolarda Tesseract'tan kayda değer
şekilde daha iyidir. Karşılığı: sayfa başına maliyet (yazıldığı sırada
text detection için ~$0.0015 / sayfa) ve ağ bağımlılığı.

### PII içeren formlar?

OCR'dan sonra, dataset'i yayımlamadan önce `--pii-mask` ile ön işleyin:

```bash
ocrmypdf medical_scan.pdf medical_with_text.pdf --language tur+eng
forgelm ingest medical_with_text.pdf --output data/medical.jsonl --pii-mask
forgelm audit data/medical.jsonl --output ./audit/
```

Audit adımı redaksiyonun çalıştığını doğrular: `data_audit_report.json`'da
kalan herhangi bir PII flag'i, maskelemeden kaçan bir satırdır.
