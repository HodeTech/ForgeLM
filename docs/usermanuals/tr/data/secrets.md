---
title: Sırların Temizlenmesi
description: Eğitim verisinden AWS anahtarları, GitHub PAT'leri, JWT'leri, PEM blokları ve diğer kimlik bilgilerini redakte edin.
---

# Sırların Temizlenmesi

Kod repo'ları, destek ticket'ları ve operasyonel log'lar kimlik bilgileri sızdırır. Bu kimlik bilgileri eğitim setine girip model deploy edildikten sonra modelle sohbet eden herkes onları çıkarabilir. Sırların temizlenmesi bunu ingest'te önler.

## Tespit edilenler

Bundled detector `_SECRET_PATTERNS` (`forgelm/data_audit/_secrets.py::_SECRET_PATTERNS`) altında **9 secret ailesi** ship eder:

| Pattern anahtarı | Anchor |
|---|---|
| `aws_access_key` | `AKIA` / `ASIA` + 16 büyük harf alphanum |
| `github_token` | `ghp_*`, `gho_*`, `ghu_*`, `ghs_*`, `ghr_*`, `github_pat_*` (tek birleşik aile) |
| `slack_token` | `xox[baprs]-*` |
| `openai_api_key` | `sk-*` ve `sk-proj-*` |
| `google_api_key` | `AIza` + 35 karakter |
| `jwt` | Kanonik JWT header anahtarlarıyla üç-segment base64url (`eyJ.eyJ.X`-şekilli prose false-positive'lerine karşı savunma) |
| `openssh_private_key` | `BEGIN OPENSSH/RSA/DSA/EC PRIVATE KEY` … `END …` (tam PEM zarfı) |
| `pgp_private_key` | `BEGIN PGP PRIVATE KEY BLOCK` … `END …` |
| `azure_storage_key` | `DefaultEndpointsProtocol=…AccountKey=…` |

Tüm eşleşmeler `mask_secrets()` (`forgelm/data_audit/_secrets.py::mask_secrets`) tarafından literal `[REDACTED-SECRET]` string'i ile değiştirilir. Detector bugün Anthropic, Stripe, SendGrid ya da Twilio için per-vendor pattern ship **etmez** — bu trafik tipleri olan operatörler regex setini out-of-tree genişletir (Phase 28+ backlog'u bunları opt-in extras olarak ship etmeyi takip ediyor).

## Hızlı örnek

```shell
$ forgelm ingest ./support-tickets/ \
    --recursive \
    --secrets-mask \
    --output data/tickets.jsonl
✓ 47 sır maskelendi:
    aws_access_key:       12
    github_token:          8
    jwt:                  18
    openssh_private_key:   2
    openai_api_key:        7
```

## "PEM block" ne demek

PEM özel anahtarlar birden çok satıra yayılır:

```text
-----BEGIN RSA PRIVATE KEY-----
MIIEpAIBAAKCAQEA1+...
...
-----END RSA PRIVATE KEY-----
```

ForgeLM'in PEM detector'ı (`openssh_private_key` ailesi — RSA / DSA / EC envelope'larını da kapsar) tüm bloğu (BEGIN'den END'e) eşleştirir, sadece marker satırını değil. Diğer her aile gibi, tüm blok `[REDACTED-SECRET]` ile değiştirilir — per-family token yoktur (`mask_secrets()` tek bir `replacement="[REDACTED-SECRET]"` sabiti ship eder; `forgelm/data_audit/_secrets.py::mask_secrets`). Bu, BEGIN satırını tespit edip key body'sini JSONL'da bırakan yaygın bug'ı önler.

## Sadece-audit modu

```shell
$ forgelm audit data/tickets.jsonl
Data audit summary
  Source        : /srv/corpora/tickets.jsonl
  Total samples : 8400
  Splits        : train
  └─ train: n=8400
     length  min=44 max=2317 mean=311.5 p95=980
     languages (top-3): en=8400
     secrets         : aws_access_key=12, jwt=18
  Secrets        : CRITICAL — 30 flagged (aws_access_key=12, jwt=18)

Report written to: audit/data_audit_report.json
```

Sırlar taraması her zaman açıktır — CLI yüzeyinden devre dışı bırakılamaz (eğitim verisinde credential sızıntısı, operatörün asla kapatabilmesi gereken bir şey değildir).

Kritik ciddiyetteki bir bulgu **`3` ile çıkar**, böylece CI pipeline'ı hızlı başarısız olur:

```text
[ERROR] Secrets gate FAILED (critical): 1 credential/secret span(s) detected (aws_access_key=1).
Do not train on this corpus — a credential in training data is memorised and re-emitted at
inference time. Scrub it with `forgelm ingest --secrets-mask`, or re-run
`forgelm audit --allow-secrets` to record the findings without failing the pipeline. Exiting 3.
```

Doğrulandı: `AKIAIOSFODNN7EXAMPLE` içeren bir corpus `3` ile çıkar; aynı corpus `--allow-secrets` ile `0` ile çıkar ve bunun yerine bir `SUPPRESSED` uyarısı log'lar.

:::warn
**Bu kapı her zaman tetiklenmiyordu.** Yakın zamana kadar `forgelm audit`, `Secrets : CRITICAL — N flagged` yazdırıp `0` ile çıkıyordu; dolayısıyla bu sayfanın eski "non-zero exit verir" vaadine dayanarak kurulan her credential-sızıntı kapısı sessizce ölüydü. Bu sürümden önce böyle bir kapı kurduysanız, bilinen bir dummy credential taşıyan bir corpus'a karşı yeniden koşturun ve artık exit `3` aldığınızı doğrulayın — ayrıca ölü kapıdan geçmiş her corpus'u yeniden denetleyin.
:::

Kapılayan **tek** bulgu sırlardır. PII, split-arası sızıntı, near-duplicate'ler ve kalite flag'leri ne kadar ciddi olursa olsun `0` ile raporlanır — bunlara JSON zarfı üzerinde `jq` ile kapı koyun. Bkz. [Dataset Audit](#/data/audit) sayfasındaki exit kodu tablosu.

## Programatik API

Her iki fonksiyon da tek bir string alır ve `forgelm.data_audit` üzerinden yeniden dışa aktarılır. `detect_secrets` bir **sayım haritası** döndürür, span değil — satır seviyesinde span veya değer yüzeyi yoktur.

```python
from forgelm.data_audit import detect_secrets, mask_secrets

text = "Use this key: AKIAIOSFODNN7EXAMPLE for the bucket."
print(detect_secrets(text))
# {'aws_access_key': 1}

print(mask_secrets(text))
# Use this key: [REDACTED-SECRET] for the bucket.
```

İmzalar: `detect_secrets(text) -> Dict[str, int]` ve `mask_secrets(text, replacement='[REDACTED-SECRET]', *, return_counts=False)`. `(maskelenmiş_metin, sayımlar)` tuple'ı almak için `return_counts=True` geçin. Dönüş bir sayım haritası olduğu için bir credential'ın satırın *neresinde* geçtiğini geri elde edemezsiniz — incelemeleri kendi kodunuzda satır başına yineleme etrafında planlayın.

## Tespit gerçekte nasıl çalışır

`detect_secrets`, dokuz adet ön-ek sabitli (prefix-anchored) regex üzerinde dönen düz bir `pattern.findall(text)` döngüsüdür ve family başına bir sayım döndürür (`forgelm/data_audit/_secrets.py`). Hassasiyet tamamen bu sabitlerin ne kadar dar olduğundan gelir — `aws_access_key` düz `AKIA` ön-ekini, `jwt` `eyJ` ön-ekli üç parçalı bir yapıyı, `github_token` ise `ghp_`/`gho_` gibi ön-ekleri şart koşar. Modül, "git-secrets" tarzı araçlardaki başlıca gürültü kaynağı olan genel yüksek-entropili string'leri bilinçli olarak eşleştirmez.

Dokuz family: `aws_access_key`, `azure_storage_key`, `github_token`, `google_api_key`, `jwt`, `openai_api_key`, `openssh_private_key`, `pgp_private_key`, `slack_token`. Anthropic / Stripe / SendGrid / Twilio pattern'ları gönderilmez; bu trafik profiline sahip operatörler `_SECRET_PATTERNS`'i out-of-tree genişletir.

:::warn
**Entropi kontrolü, bağlam penceresi ve test/örnek dışlama listesi yoktur.** Bu sayfanın önceki sürümleri üçünü de tarif ediyordu. Hiçbiri kodda mevcut değil ve pratikteki sonuç, o metnin ima ettiğinin tam tersi yönde işliyor: dummy değerler **tespit edilir**. `detect_secrets("Use this key: AKIAIOSFODNN7EXAMPLE")` `{'aws_access_key': 1}` döndürür — AWS'in kanonik dokümantasyon placeholder'ı, yakınında hiçbir `aws` sözcüğü olmadan tetiklenir. Fixture'larınızın, test verinizin ve dokümantasyon örneklerinizin işaretlenmesini bekleyin ve bunları elle triyaj edin.
:::

Yüksek-stake bir audit (ör. yasal açıklama taraması) için `forgelm audit` bulguları `secrets_summary` altında kaydeder (pattern family'si başına bir sayım). Sayımı > 0 olan her family için bu haritayı dolaşın; hangi eşleşmelerin canlı credential, hangilerinin placeholder olduğunu bir insan teyit etsin — araç bu ayrımı sizin yerinize yapamaz.

## Konfigürasyon

Secrets scanner **`forgelm audit` içinde her zaman açıktır** — enable/disable knob'u ve per-family allow/deny listesi yoktur. Mask-on-emit `audit_dataset()` üzerindeki `secrets_mask: bool` argümanıyla (ve `forgelm ingest` üzerindeki `--secrets-mask` flag'iyle) kontrol edilir; replacement string'i `mask_secrets()` içindeki tek sabit `[REDACTED-SECRET]` constant'ıdır. `ingestion.secrets_mask:` YAML bloğu, `enabled` / `tag_by_category` / `strict` / `categories` alt-alanları **yoktur** — bu adlar eski doc taslaklarında geçiyordu ama hiç ship olmadı. Family setini genişletmek/kısıtlamak için `forgelm/data_audit/_secrets.py::_SECRET_PATTERNS`'i fork edin.

## Sık hatalar

:::warn
**"Güvenilen iç" veride secrets-mask'i kapatmak.** İç log'lar kimlik bilgisi sızıntılarının en sık kaynağıdır. Maskeleyiciyi koşturmanın maliyeti neredeyse sıfır; deploy edilen modelde sızdırılmış bir AWS key'in maliyeti sınırsız.
:::

:::warn
**Entropi kontrolsüz özel regex.** Sırlar tespitinde false positive'in en büyük sebebi sadece-regex pattern'lerin dokümantasyon örneklerini eşlemesi. Regex'i her zaman entropi veya bağlam kontrolüyle eşleştirin.
:::

:::tip
Sertifika / token meşru içeren corpus'lar için (güvenlik eğitim dataset'leri, CTF içeriği) CLI escape hatch yoktur — sırlar taraması bilinçli olarak her zaman açıktır (`--no-secrets` / `--skip-secrets` flag'i yoktur ve `forgelm audit` taramayı her çağrıda koşulsuz koşturur; temel scan-mode semantiği için yukarıdaki [Sadece-audit modu](#sadece-audit-modu) bölümüne bkz.). Corpus'unuzun data-governance manifest'inde ilgili satırları `legitimate_secret_content: true` olarak işaretleyin, böylece downstream reviewer rationale'ı görür; `forgelm audit` yine de flag'ler ama reviewer manifest satırını kanıt olarak dismiss eder.
:::

## Bkz.

- [PII Maskeleme](#/data/pii-masking) — kişisel veri için kardeş özellik.
- [Veri Seti Denetimi](#/data/audit) — sadece-audit modunda sırlar tespitini kapsar.
- [Doküman Ingest'i](#/data/ingestion) — secrets-mask'in çağrıldığı yer.
