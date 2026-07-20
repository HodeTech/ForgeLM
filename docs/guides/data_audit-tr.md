# Veri Seti Denetimi Rehberi

`forgelm audit PATH` bir JSONL veri setini analiz eder ve kalite,
governance ile PII sinyallerini kapsayan bir `data_audit_report.json`
üretir. Faz 11 altyapıyı, Faz 11.5 (`v0.5.0`'da birleşti)
ise top-level flag'i first-class subcommand'a yükseltti, LSH-banded
near-duplicate tespitini, streaming JSONL okuyucusunu, PII şiddet
katmanlarını, atomik disk yazımı ve verbose-by-default kısaltma
politikasını ekledi. **Faz 12 (`v0.5.0`'da birleşti)** opt-in MinHash LSH dedup
yöntemini (`--dedup-method minhash`), her zaman çalışan
code/credential leakage taramasını (`secrets_summary`) ve heuristic
kalite filtresini (`--quality-filter`) ekledi — başlangıçta opt-in,
**Faz 15 Görev 5 ile v0.6.0'dan itibaren default-AÇIK**; v0.6.0 öncesi
opt-in semantiğini isteyen operatörler yeni `--no-quality-filter`
companion bayrağını kullanır.

Trainer'ın `output_dir`'ünde mevcutsa, rapor EU AI Act Madde 10 veri
governance artifact'ına otomatik olarak beslenir.

---

## Çalıştırma

```bash
# Tek split (`train` olarak değerlendirilir)
forgelm audit data/sft.jsonl --output ./audit/

# Çoklu split: train.jsonl / validation.jsonl / test.jsonl içeren dizin
forgelm audit data/ --output ./audit/

# Bulgu olmayan split'leri de göster
forgelm audit data/ --verbose

# Daha geniş / dar simhash near-duplicate eşiği
forgelm audit data/ --near-dup-threshold 5

# Faz 12: 50K+ satır corpus için MinHash LSH (`[ingestion-scale]` extra)
pip install 'forgelm[ingestion-scale]'
forgelm audit data/large_corpus.jsonl --dedup-method minhash --jaccard-threshold 0.85

# Faz 12 + Faz 15: heuristic kalite filtresi (Gopher/C4 stili).
# v0.6.0'dan itibaren default-AÇIK; explicit flag yedek/zararsızdır.
# v0.6.0 öncesi opt-in davranışına dönmek için --no-quality-filter geçirin.
forgelm audit data/ --quality-filter

# Faz 17: çoklu-split corpora için split-seviyesi paralelizm
forgelm audit data/ --workers 4
```

`--output` varsayılan olarak `./audit/`'tir. Dizin yoksa oluşturulur;
**tam** `data_audit_report.json` her zaman oraya yazılır. Stdout
varsayılan olarak insan-okunabilir özet gösterir; `--output-format json`
geçince stdout'a **özet** JSON zarfı (üst seviye metrikler + rapor yolu
+ notlar) düşer — tam rapor yine `--output` altında diskte kalır. CI/CD
tüketicileri her detayı istediğinde stdout özetini parse etmek yerine
dosyayı `report_path`'tan slurp etmeli.

GPU gerekmiyor. Ağ çağrısı yok. CPU-only.

---

## Ne elde edersin

### Split başına metrikler

```json
{
  "splits": {
    "train": {
      "sample_count": 1240,
      "columns": ["text"],
      "text_length": {"min": 32, "max": 4096, "mean": 1834.2, "p50": 1900, "p95": 3580},
      "null_or_empty_count": 3,
      "null_or_empty_rate": 0.0024,
      "languages_top3": [
        {"code": "tr", "count": 950},
        {"code": "en", "count": 240},
        {"code": "de", "count": 50}
      ],
      "simhash_distinct": 1180,
      "near_duplicate_pairs": 60,
      "pii_counts": {"email": 18, "phone": 4}
    }
  }
}
```

### Cross-split örtüşmesi

```json
{
  "cross_split_overlap": {
    "hamming_threshold": 3,
    "pairs": {
      "train__test": {
        "leaked_rows_in_train": 7,
        "leak_rate_train": 0.0056,
        "leaked_rows_in_test": 7,
        "leak_rate_test": 0.7
      }
    }
  }
}
```

Audit leak rate'i **her iki yönde** de raporlar çünkü birbirinden farklı
hikâyeler anlatırlar. 1240 train + 10 test satırında 7'sinin sızdığı bir
durumda `leak_rate_train = 7/1240 = %0.56` önemsiz görünür ama
`leak_rate_test = 7/10 = %70` benchmark güvenirliğini fiilen yok eden
metriktir. Her zaman küçük tarafın oranını okuyun — test bütünlüğünün
sessiz katili odur.

### PII özeti

```json
{
  "pii_summary": {
    "email": 18,
    "phone": 4,
    "credit_card": 1,
    "tr_id": 2
  }
}
```

Her satırın metin payload'ı regex ile taranır; kredi kartları Luhn
doğrulaması, TR Kimlik No'ları TC Kimlik checksum'ından geçirilir.
Diğer kategoriler regex şekli üzerinden yüzeylenir — false positive'ler
kasıtlıdır. Veri setini paylaşmadan önce `forgelm ingest --pii-mask`
ile (veya kendi ön işlemenizde) maskeleyin.

**Pattern önceliği belgeli.** `_PII_PATTERNS` iter sırası hem tespit
önceliğini hem maskeleme önceliğini yönetir — en spesifik pattern'lar
(`email`, `iban`, `credit_card`, ulusal kimlikler) önce taranır,
ardından gürültülü `phone` pattern'i. Bir span iki kategoriyle
eşleşebileceğinde, ilk / dar olan kazanır ve span sonraki pattern
görmeden değiştirilir. Phone, bare digit run'ları (timestamp, log line
numarası, ISO tarih) flag'lemesin diye kasıtlı olarak `+CC` veya
`(area)` formatına anchored.

### PII şiddet katmanları (Faz 11.5)

Düz `pii_summary` haritası bir compliance reviewer'a *bulgu ne kadar
kötü?* sorusunda sıfır rehberlik veriyor. Faz 11.5 yanına bir
`pii_severity` bloğu ekliyor:

```json
{
  "pii_severity": {
    "total": 25,
    "by_tier": {"critical": 1, "high": 2, "medium": 18, "low": 4},
    "by_type": {
      "credit_card": {"count": 1, "tier": "critical"},
      "tr_id":      {"count": 2, "tier": "high"},
      "email":      {"count": 18, "tier": "medium"},
      "phone":      {"count": 4, "tier": "low"}
    },
    "worst_tier": "critical"
  }
}
```

Tier tablosu konsensüs regülatif ağırlıklandırmadır (finansal
kimlikler için PCI-DSS; devlet kimlikleri için GDPR Md. 9 + ENISA).
`critical` katman artık komutun kendisini kapılar (sonraki bölüm);
`high`'ta da durmak isteyen pipeline'lar `pii_severity.worst_tier`'i
okumalı ve açık bir review olmadan yayınlamayı reddetmelidir.

### PII kapısı — yalnızca kritik katman

`forgelm audit`, tarama `critical` katman PII bulduğunda — `credit_card`
veya `iban` — **`3`** ile çıkar (evaluation-gate hatası; sır kapısının
kullandığı kodun ve `forgelm safety-eval`'in bir eşik tutturulamadığında
kullandığı kodun aynısı). Eğitim corpus'undaki bir kart ya da hesap
numarası model tarafından ezberlenir ve çıkarım zamanında yeniden
yayılır; dolayısıyla böyle bir bulguyu raporlayıp `0` ile çıkan bir
audit, pipeline'a her şeyin yolunda olduğunu söylemiş olur.

```text
[ERROR] PII gate FAILED (critical): 1 critical-tier PII span(s) detected (credit_card=1).
These clear a checksum, so they are real values rather than lookalikes — a card or account
number in training data is memorised and re-emitted at inference time. Mask it with
`forgelm ingest --pii-mask`, or re-run `forgelm audit --allow-pii` to record the findings
without failing the pipeline. Exiting 3.
```

**Neden yalnızca `critical` katman.** `credit_card` ve `iban`
sayılmadan önce bir checksum'dan geçer — kartlar için Luhn, IBAN'lar
için ISO 7064 mod-97 — dolayısıyla bir eşleşme benzer görünen bir dizi
değil, gerçek bir değerdir. Sert bir kapıyı savunulabilir kılan özellik
budur. Kritik-altı aileler (`de_id`, `fr_ssn`, `us_ssn`, `email`,
`phone`) yalnızca regex şekliyle eşleşir ve *kasıtlı olarak* fazla
raporlar; çünkü audit'in işi bir insanın değerlendirmesi için aday
yüzeye çıkarmaktır. Kasıtlı olarak fazla raporlayan bir sinyale kapı
koymak temiz corpus'ları düşürür — meşru örnek adreslerle dolu bir
destek-ticket veri seti her koşuda kırmızıya döner — ve operatörün
"kurt geldi" diye bağıran bir kapıya yaptığı ilk şey onu kapatmaktır;
bu da güvenilir yarısını beraberinde götürür. Bu aileler yine tespit
edilir, sayılır, katmanlanır ve rapora yazılır; yalnızca exit kodunu
belirlemezler.

`tr_id`, checksum'dan geçtiği hâlde (TC Kimlik No) kapılamayan tek
ailedir: kapı, `PII_SEVERITY` içinde ilan edilmiş katmanı okur ve
`tr_id` orada `high` katmanındadır. Checksum doğrulaması kapılamanın
*ön koşuludur*, tetikleyicisi değil — bu invariant
`tests/test_audit_pii_gate.py::TestCriticalTierIsChecksumBacked`
tarafından pinlenir; böylece şekil-eşleşmeli bir aileyi `critical`'a
yükseltmek, kapıyı sessizce gürültülü bir sinyalin üstüne kurmak
yerine test suite'ini düşürür.

`--allow-pii` bulguları başarısız olmadan kaydeder (exit `0` artı bir
`SUPPRESSED` uyarısı). Tespit, yazdırılan özet ve diskteki rapor
etkilenmez — yalnızca exit kodu bastırılır. Bir config alanı ya da env
var değil, komut satırı bayrağıdır; böylece pipeline diff'ini okuyan
bir reviewer onu görebilir. `--allow-secrets`'ten bağımsızdır: birini
bastırmak diğerini kurulu bırakır. Herhangi biri process'i sonlandırmadan
önce her iki kapı da raporlar; dolayısıyla hem sızmış bir anahtar hem
gerçek bir kart numarası taşıyan bir corpus tek koşuda iki hatayı da
yüzeye çıkarır.

`--output-format json` altında hüküm, `secrets_gate` kardeşinin yanında
bir `pii_gate` nesnesidir ve kapılardan herhangi biri başarısız
olduğunda `success` `false` olur:

```json
{
  "pii_gate": {
    "status": "failed",
    "severity": "critical",
    "critical_total": 1,
    "critical_types": {"credit_card": 1},
    "advisory_total": 3,
    "advisory_types": {"email": 1, "phone": 1, "us_ssn": 1},
    "allow_pii": false
  }
}
```

Bulgu varken `--allow-pii` geçildiyse `status` `"passed"` değil
`"suppressed"` olur; böylece bir CI dashboard'u temiz bir corpus ile
feragat edilmiş bir corpus'u ayırt edebilir.

### Near-duplicate tespiti

Case-fold edilmiş kelime token'ları üzerinde 64-bit simhash, Hamming
mesafe ≤ 3 ile (simhash makalesinin canonical web-page-dedup
kurulumunda kullandığı eşik, bu genişlikte ≈%95 benzerlik). Hem
**split-içi** çiftleri (`near_duplicate_pairs` per split) hem de
yukarıdaki **cross-split** sızıntıyı ortaya çıkarır.

Faz 11.5 alttaki taramayı **LSH banding**'e geçirdi: pigeonhole
prensibi `bands = threshold + 1` seçiyor, aday çiftler herhangi bir
band-bucket'ında çakışan satırlardan ibaret oluyor ve Hamming kontrolü
yalnızca adaylar üzerinde çalışıyor. Recall varsayılan eşikte tam
korunuyor; maliyet `O(n²)`'den ~`O(n × k)`'ye düşüyor (cross-split
`_count_leaked_rows` helper'ı da aynı banded şekli kullanıyor).
Brute-force yolu, eşik bantları 4 bitin altına düşürecek kadar yüksek
olduğunda fallback olarak kalıyor — `find_near_duplicates` her iki
yolda da aynı sonucu döndürüyor.

Faz 11.5 simhash backend'ini de değiştirilebilir hale getirdi:

- **xxhash.xxh3_64** opsiyonel `xxhash` bağımlılığı (artık
  `forgelm[ingestion]`'ın parçası) yüklüyse per-token digest'i bu
  sürüyor; ~1.3x ham digest başına, ~1.05x end-to-end (Apple Silicon /
  Python 3.11 ölçümü) hızlandırma sağlıyor (`compute_simhash` içinde
  `lru_cache` tekrar eden token'ları absorbe ettiğinden end-to-end
  kazanç daha az; backend swap'i öncelikle ileriye dönük güvence).
- **BLAKE2b** bare install için fallback olarak kalıyor.
- Modül seviyesinde `lru_cache(maxsize=10_000)` digest'i token
  seviyesinde memoize ediyor — Zipfian token frekansı sayesinde küçük
  bir cache bile bir corpus'un trafiğinin çoğunu kapsıyor.

### MinHash LSH dedup (Faz 12)

50K satırın üzerindeki corpus'larda simhash + LSH banding sınırlarını
hissetmeye başlar: band-bucket fan-out'u büyür ve false-positive
doğrulama duvar saatine hâkim olmaya başlar. Faz 12 opsiyonel
`[ingestion-scale]` extra'sı (`datasketch` paketi) üzerinden opt-in
**MinHash LSH** yolunu ekledi. Yüzey:

```bash
pip install 'forgelm[ingestion-scale]'
forgelm audit data/large_corpus.jsonl \
  --dedup-method minhash --jaccard-threshold 0.85
```

```python
from forgelm.data_audit import audit_dataset
audit_dataset("data/large_corpus.jsonl",
              dedup_method="minhash", minhash_jaccard=0.85)
```

İki yöntem **aynı eşiklerde değiştirilebilir değildir** — pratikte
simhash Hamming ≤ 3 ≈ MinHash Jaccard ≥ 0.85, ama "benzer" tanımı
farklı. MinHash yaklaşıktır (permütasyon gürültüsü; varsayılan
`num_perm=128`), bu yüzden `num_perm` değiştiğinde aynı çift hafifçe
farklı benzerlik skorlarıyla flag'lenebilir. Cross-run determinizm
istiyorsanız `num_perm`'i sabit tutun. Audit JSON'a hangi yolun
çalıştığını kaydeden `near_duplicate_summary.method` alanı ile
per-split sayacı yansıtan `near_duplicate_summary.pairs_per_split`
mapping'i eklendi. Çift sayısını eski yerinden okuyan tüketiciler —
yani per-split `splits.<name>.near_duplicate_pairs` (örn.
`jq '.splits.train.near_duplicate_pairs' data_audit_report.json`) —
değişmeden çalışmaya devam eder; yeni özet bloğu tamamen additive.

### Code / secret leakage tagger (Faz 12, always-on)

Audit artık her satırı, SFT corpus'una hiç girmemesi gereken kimlik
bilgileri ve token'lar için tarıyor — gerçek bir API anahtarı içeren
metin üzerinde fine-tune etmek o anahtarı modelin içine ezberletir.
Detector dar bir prefix-anchored regex seti kullanıyor (false-positive
oranı bilerek düşük tutuldu) ve `pii_summary` yanında bir
`secrets_summary` bloğu yayar:

```json
{
  "secrets_summary": {
    "aws_access_key": 1,
    "github_token": 2,
    "openai_api_key": 1
  }
}
```

Kapsam: AWS access key'leri (`AKIA…` / `ASIA…`), GitHub PAT'ler
(`ghp_`, `gho_`, `ghs_`, `ghu_`, `ghr_`, `github_pat_`), Slack
token'ları (`xox[baprs]-`), OpenAI API anahtarları (`sk-…` ve project
scoped `sk-proj-…`), Google API anahtarları (`AIza…`), JSON Web
Token'lar (`eyJ` ile encode edilmiş JSON başlığına anchored —
sadece `alg`/`typ`/`kid` gibi kanonik header anahtarlarına bağlı, böylece
prosa false-positive üretmiyor), OpenSSH / RSA / DSA / EC / PGP
özel anahtar blokları (tam `BEGIN…END` zarfı — `mask_secrets()`
yalnızca header satırını değil, anahtar bloğunun tamamını redakte
eder) ve Azure storage connection string'leri.

Operatör tarafında, eğitim öncesi temizlik için iki yol:

```bash
# Pre-process: helper API ile JSONL'i sonradan yeniden yaz
python -c "from forgelm.data_audit import mask_secrets; \
  print(mask_secrets(open('data.jsonl').read()))" > data_clean.jsonl

# Veya ingest sırasında temizle (Faz 12; chunk'lar hiç JSONL'e düşmeden)
forgelm ingest ./policies/ --recursive --output data/policies.jsonl --secrets-mask
```

Opsiyonel / forward-compatibility: `[ingestion-secrets]` extra'sı bir
`detect-secrets>=1.5.0` bağımlılığı tanımlar ama bu **bir sonraki sürüm
için ayrılmıştır**. v0.5.0 itibarıyla `forgelm.data_audit.detect_secrets()`
yalnızca yukarıdaki regex setine dayanır; extra'yı kurmak bugün audit
davranışını değiştirmez. Extra, `forgelm[ingestion-secrets]` pin'leyen
operatörlerin entegrasyon geldiğinde forward-compatible olabilmesi
için var.

### Heuristic kalite filtresi (Faz 12, opt-in)

`forgelm audit --quality-filter` her satıra Gopher / C4 / RefinedWeb
tarzı heuristikler uygular ve `quality_summary` bloğu yüzeye çıkarır:

```json
{
  "quality_summary": {
    "samples_flagged": 47,
    "by_check": {
      "low_alpha_ratio": 12,
      "low_punct_endings": 8,
      "abnormal_mean_word_length": 3,
      "short_paragraphs": 27,
      "repeated_lines": 5
    },
    "overall_quality_score": 0.94
  }
}
```

Kontroller (hepsi muhafazakar; hiçbir satır sessizce düşürülmez):

- `low_alpha_ratio` — boşluk dışı karakterlerin < %70'i harf.
- `low_punct_endings` — boş olmayan satırların < %50'si noktalama
  ile bitiyor.
- `abnormal_mean_word_length` — 3-12 karakter penceresinin dışında.
- `short_paragraphs` — `\n\n` ile ayrılmış blokların > %50'si < 5
  kelime.
- `repeated_lines` — gerçekten tekrar eden (count ≥ 2) en sık 3 satır,
  tüm boş olmayan satırların > %30'unu kapsıyor. Boilerplate'i (header,
  footer, tekrarlayan disclaimer) yakalar — kısa, tamamen tekil
  belgelerde false positive üretmemek için sayım filtresi şart.

Heuristikler markdown fenced kod bloklarını **otomatik atlar** — kod
prosa kurallarına uymadığı için (düşük alpha oranı, eksik son
noktalama) flag'lenmemesi için fence'lar değerlendirme öncesi
çıkarılır. Kod yoğun bir satırın tamamı stripping sonrası boş kalırsa
hiçbir flag atılmaz (kod legitimate SFT içeriği; gürültü değil).

ML tabanlı kalite sınıflayıcılar (fastText / DeBERTa tarzı) bilinçli
olarak **scope dışı**; deterministik regex / uzunluk / yapı pipeline'ı
audit'i yeniden üretilebilir tutar (Annex IV gereksinimi) ve bare
install ile uyumlu kalır.

---

### ML-NER PII adaptörü — `--pii-ml` (Faz 12.5, opt-in)

Varsayılan regex detector, EU AI Act Madde 10'un önemsediği yapılandırılmış
identifier'ları (email, telefon, IBAN, kredi kartı, ulusal kimlik) kapsar.
Faz 12.5, regex'in doğal olarak kaçırdığı yapılandırılmamış identifier
kategorilerini ekleyen opt-in **Presidio** ([microsoft/presidio](https://github.com/microsoft/presidio))
adaptörünü ML-NER olarak üstüne ekler: `person`, `organization`,
`location`.

```bash
pip install 'forgelm[ingestion-pii-ml]'
forgelm audit data/ --output ./audit/ --pii-ml
```

Yeni kategoriler mevcut `pii_summary` ve `pii_severity` bloklarına
disjoint isimlerle birleşir, bu yüzden regex baseline ML sinyaliyle
yan yana görünür kalır:

```json
{
  "pii_summary": {
    "email": 12,
    "phone": 3,
    "person": 47,         // ← Presidio
    "organization": 18,   // ← Presidio
    "location": 9         // ← Presidio
  },
  "pii_severity": {
    "by_tier": {"critical": 0, "high": 0, "medium": 59, "low": 30},
    "by_type": {
      "email": {"count": 12, "tier": "medium"},
      "person": {"count": 47, "tier": "medium"},
      "organization": {"count": 18, "tier": "low"}
    },
    "worst_tier": "medium"
  }
}
```

Şiddet ataması `forgelm.data_audit.PII_ML_SEVERITY`'de yaşar:
`person → medium`, `organization → low`, `location → low`. NER
false-positive oranları regex-anchored detection'dan materyal olarak
yüksektir — bu yüzden ML katmanları regex'in `critical` / `high`
katlarının altında bilinçli konumlandırılır. Bir ML bulgusunu sert
bir gate olarak değerlendirmeden önce satır bazında span'leri inceleyin.

**İki adımlı kurulum.** `presidio-analyzer` paketi spaCy NER modelini
transitively içermez — tek seferlik ayrı bir indirme:

```bash
pip install 'forgelm[ingestion-pii-ml]'
python -m spacy download en_core_web_lg   # ~ 50 MB; gerekli
```

Model yoksa `forgelm audit --pii-ml` hiçbir satır taranmadan **önce**
aynı install hint ile `ImportError` raise eder
(`forgelm.data_audit._require_presidio`'daki pre-flight, spaCy'nin
`OSError`'unu yakalayıp typed install error'a dönüştürür). Bu fail-loud
davranış bilinçli — önceki prototiplerde model eksikken sıfır ML
bulgusu sessizce raporlanıyordu, opt-in bir detector için kritik bir
compliance kör noktası.

İngilizce olmayan korpuslar için eşleşen spaCy modelini kurup dil
kodunu geçirin:

```bash
python -m spacy download xx_ent_wiki_sm        # çok dilli, daha küçük
forgelm audit data/ --pii-ml --pii-ml-language xx
```

Adaptör yalnızca opt-in; `--pii-ml` olmadan audit zero-extra-deps
regex yolunda kalır.

---

### Croissant 1.0 dataset card — `--croissant` (Faz 12.5, opt-in)

`--croissant`, `data_audit_report.json` içinde yeni bir top-level
`croissant` bloğunu [Google Croissant 1.0](https://mlcommons.org/croissant/)
dataset card'ı (`@type: sc:Dataset`) ile doldurur. Card, kanonik
`mlcommons.org/croissant/1.0` context'iyle uyumludur — bu yüzden
Croissant farkındalıklı consumer'lar (HuggingFace dataset cards,
MLCommons referans loader'ları, Croissant validator) bloğu hiçbir
modifikasyon olmadan parse edebilir.

```bash
forgelm audit data/ --output ./audit/ --croissant
```

Card şunları taşır:

* dataset seviyesinde kimlik (`name`, `description`, `datePublished`,
  `url`) — `version` bilinçli olarak çıkarılmıştır, audit'in dataset
  sürümü için first-class kanıtı yoktur; card'ı yayınlayan operatörler
  `version`'ı `license` / `citeAs` gibi elle düzenler,
* her JSONL split'i için bir `cr:FileObject` (Croissant consumer'ı
  altta yatan dosyaları bulabilsin diye),
* her split için audit'in column-detection katmanından türetilen
  `cr:Field` girdileriyle bir `cr:RecordSet`.

Flag kapalıyken blok boştur — mevcut consumer'lar byte-eşdeğer çıktı
görür, `secrets_summary` / `quality_summary`'nin koyduğu emsalin
aynısı. Card'ı HuggingFace / MLCommons'a yayınlamak isteyen operatörler
audit'in birinci sınıf delili olmayan ek Croissant alanlarını
(`license`, `citeAs`, `keywords`) audit'i yeniden çalıştırmadan
elle düzenleyebilir.

---

## Layout gereksinimleri

| Girdi şekli | Ne elde edersin |
|---|---|
| `*.jsonl` dosyası | `train` adlı tek split |
| `train.jsonl`, `validation.jsonl`, `test.jsonl`'in herhangi birini içeren `dir/` | Her mevcut dosya kendi split'i olur |
| Yaygın alias'ları (`dev`, `val`, `valid`, `eval`, `holdout`) içeren `dir/` | Canonical split adlarına katlanır — `dev.jsonl` → `validation`, `eval.jsonl` → `test`, vb. |
| Yalnızca canonical olmayan `*.jsonl` içeren `dir/` | Pseudo-split fallback: her `*.jsonl` kendi split'i olur VE cross-split leakage analizi gerçek bir partition olmadan anlamsızdır uyarısı yayılır |

Auditor şu öncelik sırasıyla bulduğu ilk metin-taşıyan sütunu okur:
`text` → `content` → `completion` → `prompt`. `messages` formatlı chat
verisinde rol etiketli içerikler birleştirilir.

**Şema kaymaları yüzeye çıkarılır.** Heterojen JSONL (opsiyonel
alanları olan satırlar) izinlidir — sütun şeması satırlar arası
anahtarların union'ı olarak hesaplanır; satır 0'dan sonra ortaya çıkan
herhangi bir sütun `schema_drift_columns` altında raporlanır, böylece
operatörler kaymanın kasıtlı olup olmadığına karar verebilir.

---

## Madde 10 governance entegrasyonu

`data_audit_report.json` trainer'ın `training.output_dir`'ünde eğitim
zamanında mevcut olduğunda,
[`generate_data_governance_report`](../../forgelm/compliance.py)
bulguları governance artifact'ının `data_audit` anahtarı altında
otomatik olarak inline eder. Compliance bundle'ınız ayrı dosyaya
işaret eden bir pointer yerine tek başına okunabilir bir doküman olur.

Önerilen iş akışı:

```bash
# Önce denetim — uzun bir eğitim koşusuna girmeden sorunları yüzeye çıkar
forgelm audit data/policies.jsonl --output ./checkpoints/policy-run/

# Eğit (governance artifact denetimi inline edecek)
forgelm --config configs/policy-run.yaml
```

---

## CLI referansı

```text
forgelm audit PATH \
  [--output DIR] \
  [--verbose] \
  [--near-dup-threshold N] \
  [--dedup-method {simhash,minhash}] \
  [--jaccard-threshold X] \
  [--quality-filter | --no-quality-filter] \
  [--pii-ml] \
  [--pii-ml-language LANG] \
  [--croissant] \
  [--workers N] \
  [--allow-secrets] \
  [--allow-pii] \
  [--output-format {text,json}] \
  [--quiet | --log-level {DEBUG,INFO,WARNING,ERROR}]
```

`--workers N` (Faz 17) split-seviyesi paralelizmi açar: her split kendi
worker process'inde, bir `multiprocessing.Pool` içinde koşar. Varsayılan
`1` (sıralı, Faz-17-öncesi yol ile byte-identical). Speed-up *split sayısı*
ile ölçeklenir, satır sayısıyla değil — tek-split corpus'lar 1'den büyük
değerleri görmezden gelir. Tipik çoklu-split koşular (`train` /
`validation` / `test`) `--workers 3` ile neredeyse-doğrusal hızlanma görür.

Raporu üreten merge adımı tek-thread'li kalır, dolayısıyla diskteki JSON
worker sayısından bağımsız **byte-identical**'dir (yalnızca
`generated_at` wall-clock zaman damgası değişir). Bu invariant
`tests/test_data_audit_workers.py` tarafından pinlenir; CI gate
audit-report hash'ini karşılaştırırken `--workers 1`'den `--workers 4`'e
geçiş yapan bir operatör için çalışmaya devam eder.

`PATH` bir `.jsonl` dosyası veya bir dizin olabilir. `--output`
varsayılan olarak `./audit/`'tir. `--verbose`, insan-okunabilir özette
bulgu olmayan split'leri de gösterir (varsayılan, çoklu-split
denetimlerini kısa tutmak için tüm temiz split'leri tek bir kuyruk
satırına katlar — diskteki JSON raporu etkilenmez). `--near-dup-threshold N`
varsayılan simhash Hamming eşiğini (3, ≈%95 benzerlik) ezer;
`--dedup-method=minhash` seçildiğinde göz ardı edilir.
`--dedup-method` (Faz 12) near-duplicate motorunu seçer — `simhash`
(varsayılan) veya `minhash` (`[ingestion-scale]` extra'sı şart;
`--jaccard-threshold` cutoff'u kontrol eder, varsayılan 0.85).
`--quality-filter` (Faz 12, **Faz 15 Görev 5 ile v0.6.0'dan itibaren
default-AÇIK**) heuristic kalite skorlamasını çalıştırır;
`--no-quality-filter` v0.6.0 öncesi opt-in semantiklerini isteyen
operatörler için opt-out sağlar. Credential/secrets taraması **her
zaman açık** — kapatma flag'i yoktur. `--allow-secrets` ve
`--allow-pii` iki kapının (credential'lar ve kritik katman PII)
*exit kodunu* bastırır; tespit ve raporlamaya dokunmaz ve birbirlerinden
bağımsızdırlar. Bkz. yukarıdaki
[PII kapısı — yalnızca kritik katman](#pii-kapısı--yalnızca-kritik-katman).

> **Not:** Bu davranış kılavuzun başındaki özetle eşleşir:
> `--output-format json` stdout'a küçük bir zarf (success flag, üst
> seviye metrikler, rapor yolu) yazar. Tam `data_audit_report.json`
> her zaman `--output` altına yazılır. Her detayı istiyorsanız
> diskten okuyun.

---

## Sorun giderme

| Belirti | Sebep | Çözüm |
|---|---|---|
| `Audit failed: ... not found or empty` | Yol mevcut değil veya `.jsonl` yok | Yolu doğrulayın; dosya veya `train.jsonl` dizin layout'u verin |
| Dil istatistiklerinde `"unknown (install forgelm[ingestion])"` | `langdetect` yüklü değil | `pip install 'forgelm[ingestion]'` |
| Cross-split leakage tüm satırların %100'ünü flag'liyor | Tüm split'ler aynı içeriği barındırıyor | Yeniden karıştırın; muhtemelen aynı JSONL'i her split'e kopyaladınız |
| Gerçek bir corpus'ta `near_duplicate_pairs` çok büyük | Gerçekten yüksek near-duplicate oranı (boilerplate / tekrarlayan başlıklar / veri seti kalite sorunu) — Faz 11.5 LSH banding eski O(n²) taramayı O(n × k) ile değiştirdi, dolayısıyla büyük çift sayısı algoritmik gürültü değil sinyaldir | `--near-dup-threshold 1` veya 0 ile yalnızca çok-yakın eşleşmeleri tutun; PDF'lerde header/footer dedup otomatik çalışır (ingestion rehberine bakın); birkaç işaretli çift inceleyip dedupe veya kabul kararı verin |

---

## Programmatic API

```python
from dataclasses import asdict
from forgelm.data_audit import audit_dataset

report = audit_dataset("data/sft.jsonl", output_dir="./audit/")
print(report.total_samples, report.pii_summary)

# Veya manuel serileştir:
import json
json.dump(asdict(report), open("custom_path.json", "w"), indent=2)
```

`AuditReport` düz bir dataclass'tır — `dataclasses.asdict()` size
JSON-hazır bir dict verir. PII regex helper'ları (`detect_pii`,
`mask_pii`) ve simhash fonksiyonu (`compute_simhash`) da public
API'nin parçası.
