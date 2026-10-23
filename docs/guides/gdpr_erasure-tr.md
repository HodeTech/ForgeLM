# GDPR Silinme Hakkı Rehberi (`forgelm purge`)

> **AB Genel Veri Koruma Tüzüğü Madde 17** ("unutulma hakkı") veri
> sahibine, sorumlunun kendisi hakkında tuttuğu kişisel verilerin
> silinmesini isteme hakkı verir. ForgeLM'in `forgelm purge`
> subcommand'ı böyle bir talebi yanıtlamanın bir adımıdır: bir ForgeLM
> dağıtımının ürettiği veya tükettiği eğitim corpus'larından ve
> koşum-kapsamlı artefact'lardan satır kaldırır. Kişinin kimliğini doğrulamak, bir
> Madde 17(1)'deki nedenlerden birinin geçerli olup olmadığına karar vermek, Madde 12
> süresi içinde yanıtlamak ve eğitilmiş ağırlıklarla ilgili her şey sizin
> sürecinizde kalır.

## `forgelm purge` neyi yapar (ve neyi yapmaz)

**Yapar:**

- JSONL eğitim corpus'undan tek bir satırı siler (`--row-id <id> --corpus <path>`).
- Bir koşumun staging dizinini ve adı run id'yi embed eden compliance dosyalarını siler (`--run-id <id> --kind {staging,artefacts}`; `artefacts` standart paketi bulamaz, aşağıya bakın).
- Yüklü config'in `retention:` block'una karşı retention-policy ihlallerini raporlar (`--check-policy`).
- Her aksiyonu tamper-evident bir audit-event zinciriyle kaydeder — `request → completed` (veya `request → failed`) — böylece adli inceleyici tam olarak ne olduğunu yeniden inşa edebilir.
- Operatör tarafından sağlanan satır identifier'larını audit log'a girmeden önce per-output-dir salt ile hash'ler; böylece log identifier'ı açık halde tutmaz (Madde 5(1)(c) veri minimizasyonu). Digest anonim değil, kişisel veridir (Gerekçe 26); ek bilgi ayrı tutulup kendi kontrolleri altında korunuyorsa takma adlandırma (pseudonymisation, Madde 4(5)) sayılır. Varsayılan olarak `.forgelm_audit_salt` dosyası log'un yanında durur; ikisini de okuyabilen herkes tahmin ettiği bir identifier'dan digest'i yeniden hesaplayabilir. `FORGELM_AUDIT_SECRET` ayarlayın (salt'a XOR'lanır, dosya tek başına yetmez) ve log'u ile salt'ı kişisel veri gibi koruyun.

**Yapmaz:**

- Modelleri yeniden eğitmez. Bir satırı corpus'tan kaldırmak onu zaten eğitilmiş ağırlıklardan unutturmaz — silinmiş satır olmadan tam yeniden eğitim tek doğru çözüm. Tool, bir model dizini, bir `<final_model_dir>.staging.<run_id>` staging dizini (insan onayını bekleyen veya reddedilen bir koşum) ya da bir `checkpoint-*` dizini bulduğunda `data.erasure_warning_memorisation` yayar — `--output-dir` altında (varsayılan olarak corpus'un dizinidir; bu yüzden eğitim çıktı dizininizi verin) ve `--config` ile config'in adlandırdığı eğitim dizinlerinde (pipeline stage'leri ve `merge.output_dir` dahil) — böylece operatör boşluğu görür. Corpus'u hangi koşumların tükettiğini ayırt edemez ve başka yere kopyaladığınız ya da export ettiğiniz modelleri görmez.
- Audit log'un kendisinden entry silmez. Tool, silmeyi geçmişi yeniden yazmak yerine yeni bir event olarak kaydeder ve audit log'dan hiçbir girdiyi silmez. Veri sahibini adlandıran girdilerin tutulup tutulamayacağı veri sorumlusunun kararıdır ve belgelenmelidir. Madde 17(1)'deki silme nedenlerinden biri geçerliyse girdiler yalnızca bir Madde 17(3) istisnasının kapsadığı yerde kalır: 17(3)(b), yalnızca işlemenin veri sorumlusunun tabi olduğu AB veya üye devlet hukukundan doğan bir yasal yükümlülüğe uyum için (veya kamu yararına ya da kamu otoritesinin kullanılmasına ilişkin bir görev için) gerekli olduğu ölçüde uygulanır; 17(3)(e) ise hukuki taleplerin ileri sürülmesini, kullanılmasını veya savunulmasını kapsar. Madde 17(1)'deki nedenlerin hiçbiri geçerli değilse (girdilerin toplandıkları amaç için hâlâ gerekli olması yalnızca 17(1)(a)'yı dışlar; rızanın geri çekilmesini, Madde 21 itirazını ve hukuka aykırı işlemeyi de kontrol edin) silme gerekmez; ancak talebe yine de gecikmeksizin ve en geç talebin alınmasından itibaren bir ay içinde yanıt verilir; yanıtta gerekçe, bir denetim otoritesine şikâyet etme ve yargı yoluna başvurma imkânı bildirilir (Madde 12(4)).
- Downstream tüketicilere silme bildirimi göndermez. Webhook receiver'ları, dataset mirror'ları, deploy edilmiş model endpoint'leri ve yayınlanmış checkpoint'ten türetilmiş üçüncü-taraf fine-tune'lar operatörün runtime-katmanı sorumluluğudur (GDPR Madde 19, verinin ifşa edildiği her alıcıya silmeyi bildirmeyi ister; bu, imkânsız olmadıkça veya orantısız çaba gerektirmedikçe geçerlidir ve veri sahibi isterse bu alıcıları ona da bildirmek gerekir; Madde 17(2) ise veri sorumlusu veriyi kamuya açtıysa makul adımlar ekler). Tool, row modunda `--config` ile verdiğiniz config webhook block'u içerdiğinde `data.erasure_warning_external_copies` yayar; böylece audit log'u sorgulayan downstream tüketici açık hatırlatmayı görür.
- Yedekleri silmez. Operatörün `<output_dir>` sınırı dışındaki replikalar, snapshot'lar ve storage backup'lar altyapı tarafı endişelerdir.
- Yanıtın tamamını vermez. Eksiksiz bir yanıt, ForgeLM'in yapmadığı adımları da gerektirir: talep edenin kimliğini makul önlemlerle doğrulamak (Gerekçe 64; Madde 12(6), makul şüphe varsa ek bilgi istemenize izin verir); talebin alınmasından itibaren bir ay içinde yanıtlamak (Madde 12(3); karmaşık veya çok sayıda talep için iki ay daha uzatılabilir, uzatma ilk ay içinde bildirilir); bir erişim talebinde, veri sahibine verinin bir kopyasını ve Madde 15(1)(a)-(h) bilgilerini vermek (Madde 15(3)) ve veri üçüncü bir ülkeye aktarıldıysa Madde 46 güvencelerini (Madde 15(2)) vermek; bunu başkalarının hak ve özgürlüklerini olumsuz etkilemeden yapmak (Madde 15(4)); bir silme talebinde, Madde 17(1)'deki nedenlerden birinin geçerli olup olmadığına ve tuttuğunuz şeyi hangi Madde 17(3) istisnasının (varsa) kapsadığına karar vermek; alıcıları bilgilendirmek (Madde 19); satırların gittiğini doğrulamak için purge sonrasında erişim aramasını yeniden çalıştırmak; ve bir talebi reddederseniz gecikmeksizin ve en geç bir ay içinde gerekçeyi, şikâyet etme ve yargı yoluna başvurma imkânını bildirmek (Madde 12(4)). Eğitilmiş modelin kişisel veri sayılıp sayılmayacağı ayrı bir sorudur (EDPB Görüş 28/2024).

## Üç mod

### 1. Corpus satır silme

```shell
$ forgelm purge \
    --row-id "ali@example.com" \
    --corpus train.jsonl \
    --justification "GDPR Mad.17 ticket #1234" \
    --output-dir ./outputs
```

Tool şunları yapar:

1. Per-output-dir salt'ı `<output_dir>/.forgelm_audit_salt`'ta çözer (ilk kullanımda oluşturur; mode `0600`; `FORGELM_AUDIT_SECRET[:16]` set'liyse XOR'lar). **Not:** bu XOR yalnızca **identifier hashing**'i besler — audit-chain HMAC anahtarı bağımsız olarak `SHA-256(FORGELM_AUDIT_SECRET ‖ run_id)` şeklinde türetilir (bkz. [`docs/qms/access_control.md`](../qms/access_control-tr.md) §3.4 / `forgelm/compliance.py` içindeki `AuditLogger.__init__`). İki primitif kasıtlı olarak ayrıdır.
2. Audit log'a `data.erasure_requested` yazar — **hash'lenmiş** target_id (`SHA-256(salt + value)`) ve operatör-sağlanan justification ile.
3. Eşleşen JSONL satırını `id` (veya `row_id`) field'ı ile bulur — re-order edilmiş bir dosyada sessiz yanlış-satır silmesini engellemek için satır-numarası fallback'i **reddedilir**. ForgeLM şu an bir id-doldurma yardımcısı sunmuyor (Phase 28 backlog'unda `forgelm audit --add-row-ids` flag'i bekliyor); id'siz corpus'lara sahip operatörler purge'den önce id'leri operatör-tarafı bir script ile pre-populate etmeli (GDPR / KVKK kullanıcı kılavuzu sayfası, id'si olmayan her satıra bir UUID veren ve yinelenen id'leri reddeden test edilmiş bir Python script'i içerir).
4. Corpus'u atomic olarak yeniden yazar (kardeş bir temp dosyaya yazar + `os.replace`); operatörler ya tam silme-öncesi dosyayı ya da tam silme-sonrası dosyayı görür, asla kısmi state'i değil.
5. Uyarı koşullarını tespit eder ve eşleşen event'leri yayar (memorization, synthetic-data varlığı, dış kopyalar). Dış-kopya kontrolü `--config` ile verdiğiniz config'in `webhook` bloğunu okur (literal bir `url` ya da purge ortamında export edilmiş bir `url_env` değişkeni); `--config` olmadan ya da `url_env` ayarsızsa tetiklenemez.
6. Disk operasyonu raise ederse `data.erasure_failed`, başarılıysa `data.erasure_completed` yazar; zincir nihai state'i yansıtır.

Çoklu-satır eşleşmeleri varsayılan olarak reddedilir (`--row-matches one`); id'yi paylaşan her satırı silmek için `--row-matches all` geç (operatör niyeti onaylar).

Disk'e dokunmadan silmeyi önizlemek + audit zincirini (dry_run=true ile) yaymak için `--dry-run` kullan.

### 2. Koşum-kapsamlı artefact silme

```shell
$ forgelm purge --run-id fg-abc123def456 --kind staging --output-dir ./outputs
$ forgelm purge --run-id fg-abc123def456 --kind artefacts --output-dir ./outputs
```

- `--kind staging` — `<output_dir>/<final_model_dir>.staging.<run_id>/`'i siler (`--config` başka bir `training.final_model_dir` adlandırmadıkça `final_model`); legacy `<final_model_dir>.staging/`'i yalnızca içeriği `<run_id>`'ye eşit bir `staging_run_id` işaretçisi taşıyorsa siler (ForgeLM bu işaretçiyi yazmaz; aksi halde legacy dizini kendiniz silin).
- `--kind artefacts` — `<output_dir>/compliance/` altında doğrudan bulunan, adı `<run_id>`'yi `_`, `-`, `.` ya da adın başı/sonu ile sınırlı olarak içeren her dosyayı siler (yalnızca `.json` değil). **Standart paketi bulamaz:** trainer run id içermeyen sabit dosya adları yazar (`compliance_report.json`, `training_manifest.yaml`, ...), bu yüzden normal bir çıktı dizininde komut hiçbir şey bulmaz, `data.erasure_failed` (`NoMatchingArtefacts`) yayar, 1 ile çıkar ve hiçbir şey silmez. Bu, gerçek bir run id için (`fg-…`) geçerlidir: sabit bir adda geçen genel bir token (örneğin `json` veya `report`) o dosyalarla eşleşir ve onları siler; bu yüzden her zaman tam run id'yi verin. Resolver düzeltilene kadar paketi depolama katmanınız üzerinden kaldırın.
- `--kind logs` **bilinçli olarak yok**: audit log append-only'dir ve tool onu asla silmez (girdilerin tutulup tutulamayacağına kimin karar verdiği için yukarıdaki "Yapmaz" listesine bakın).

### 3. Retention policy raporu

```shell
$ forgelm purge --check-policy --config configs/run.yaml --output-dir ./outputs
```

Salt-okunur tarama. Output dizinini gezer, her artefact'ın yaşını (canonical: `<final_model_dir>.staging.<run_id>/` dizini için koşumun ilk audit event'i, yoksa audit-log genesis `timestamp`'i; fallback: filesystem `mtime` + `age_source=mtime` flag'i ile) yüklü config'in `retention:` horizon'larına karşı kıyaslar ve structured violation list yayar. `--config`, eğitim config'i ya da yalnızca `retention:` bloğunu içeren bir dosya olabilir; başka bir üst düzey anahtar içeren dosya tam config olarak doğrulanır. `final_model_dir`, tam bir config'in `training` bloğundan gelir; yalnızca `retention:` içeren dosya varsayılan `final_model`'i bırakır.

**Başarılı policy raporları 0 ile çıkar** (rapor-değil-gate semantiği — design §10 Q5). Config-load başarısızlıkları `EXIT_CONFIG_ERROR` (sıfır olmayan) ile çıkar: eksik / okunamayan / Pydantic validation'ı geçemeyen explicit `--config`, "loader failed" durumunu "ihlal yok" sanmamak için `EXIT_CONFIG_ERROR` olarak yüzeye çıkar. CI gate isteyen operatörler `--output-format json` kullanır ve raporu kendileri sınar, örneğin `set -o pipefail` altında `jq -e '.success == true and has("count") and .count == 0'`: `count`, hiçbir `retention:` bloğu yüklenmediğinde yoktur; böylece gate hiç kontrol edilmemiş bir dizinde geçmek yerine başarısız olur, ve `--output-dir` gerçek eğitim çıktı dizini olmalıdır (olmayan bir dizin `count: 0` raporlar). Bu, public exit-code kontratını her ForgeLM subcommand'ında tutarlı tutar.

## `retention:` config block'u

```yaml
retention:
  audit_log_retention_days: 1825          # 5 yıl (ForgeLM varsayılanı, yasal süre değildir)
  staging_ttl_days: 7                     # purge --check-policy'nin staging dizinini süresi geçmiş saydığı yaş
  ephemeral_artefact_retention_days: 90   # compliance/ ve data_audit_report.json, audit log'un ilk olayından itibaren yaşlandırılır
  raw_documents_retention_days: 90        # yalnızca <output_dir>/raw_documents/ ve ingestion_output/ (ForgeLM bunları oluşturmaz)
  enforce: log_only                       # ayrılmış: henüz hiçbir komut onu dikkate almaz (log_only / warn_on_excess / block_on_excess)
```

Herhangi bir horizon'u `0` olarak ayarlamak o artefact kind'ı için raporu kapatır (her iki durumda da hiçbir şey silinmez). Horizon'lar `forgelm purge --check-policy` ile raporlanır (salt-okunur; kaç tane bulursa bulsun 0 ile çıkar); hiçbir şey otomatik silinmez ya da engellenmez. ForgeLM `raw_documents/` veya `ingestion_output/` dizinlerini oluşturmaz; `raw_documents_retention_days` yalnızca kaynak dokümanları orada tutuyorsanız etkili olur. Hiçbir horizon `checkpoint-*` dizinlerini kapsamaz: bir koşum bunları `training.output_dir` altına kaydeder (`save_total_limit`'in tuttuğu kadarını) ve rapor bunları hiç listelemez; bu yüzden depolama katmanınız üzerinden kendiniz silin. `enforce` ayrılmıştır: henüz hiçbir komut onu dikkate almaz, bu yüzden üç değer de `log_only` gibi davranır.

## Kaldırıldı: `evaluation.staging_ttl_days`

`evaluation.staging_ttl_days` field'ı (başlangıçta v0.5.5'te shipped) **v0.8.0'da kaldırıldı**. Bunun yerine kanonik field olan `retention.staging_ttl_days` kullanın. `evaluation.staging_ttl_days` hâlâ set edilmiş YAML dosyaları v0.8.0 ve sonrasında `EXIT_CONFIG_ERROR` ile config-load başarısızlığına yol açar.

## Audit-event sözlüğü

`forgelm purge` ile altı yeni event ship eder (`docs/reference/audit_event_catalog.md`'de katalog'lanır):

| Event | Ne zaman | Anahtar field'lar |
|---|---|---|
| `data.erasure_requested` | Herhangi bir `forgelm purge --row-id` / `--run-id` çağrısının ilk adımı, herhangi bir silmeden önce (`--check-policy` salt-okunur; audit event yaymaz) | `target_kind` ∈ `{row, staging, artefacts}`, `target_id` (row mode'da hash'lenmiş), `salt_source` (row mode), `corpus_path` (row), `output_dir` (run), `justification`, `dry_run` |
| `data.erasure_completed` | Başarılı silme bittiğinde | Tüm `requested` field'ları + `bytes_freed`, `files_modified`, `pre_erasure_line_number` (row mode), `match_count` (row mode) |
| `data.erasure_failed` | Disk operasyonu raise etti VEYA eşleşen satır/koşum bulunamadı VEYA çoklu-satır policy belirsizliği reddetti | Tüm `requested` field'ları + `error_class`, `error_message` |
| `data.erasure_warning_memorisation` | **Yalnız row modunda.** Row erasure × `--output-dir` altında ya da `--config` ile config'in adlandırdığı eğitim dizinlerinde bir model dizini, bir staging dizini veya bir `checkpoint-*` dizini mevcut. (Run modu memorisation uyarısı emit etmez — run-scoped `staging` / `artefacts` türleri tanım gereği promote-öncesi.) | Tüm `completed` field'ları + `affected_run_ids`, `memorisation_artefact_paths` |
| `data.erasure_warning_synthetic_data_present` | **Yalnız row modunda.** Row erasure × `output_dir`'de `synthetic_data*.jsonl` mevcut. (Run modu bu uyarıyı yüzeylemez — synthetic veri corpus-scoped'dur, run-scoped değil.) | Tüm `completed` field'ları + `synthetic_files` |
| `data.erasure_warning_external_copies` | **Yalnız row modunda, `--config` verildiğinde.** Yüklü config webhook block'u içeriyor. (Run modu `--config`'i yalnızca staging dizinini adlandırmak için `training.final_model_dir` değerini okumak üzere yükler.) | Tüm `completed` field'ları + `webhook_targets` |

## Silme sonrası zincir doğrulama

`forgelm verify-audit <output_dir>/audit_log.jsonl` herhangi sayıda erasure event'inden sonra zinciri doğrulamaya devam eder — tool yeni event'ler ekler, eski olanları yeniden yazmaz; SHA-256 hash zinciri intact kalır.

## Exit kodları

| Kod | Anlamı |
|---|---|
| 0 | Başarı veya başarılı `--check-policy` raporu (rapor-değil-gate semantiği). |
| 1 | Config hatası: bilinmeyen `--row-id`, `--run-id` ile eşleşen staging dizini / compliance dosyası yok (standart sabit-adlı pakete karşı `--kind artefacts` dahil), eksik `--corpus` ya da `--corpus`'suz `--row-id`, eksik `--kind`, seçilmemiş mod veya eksik / okunamayan / Pydantic validation'ı geçemeyen `--check-policy --config <path>`. |
| 2 | Runtime hatası: I/O failure, permission denied, atomic-rename failure; ayrıca argüman-parser kullanım hatası (`--row-id` ile `--run-id` gibi birlikte verilen iki mod, `--kind logs`), JSON zarfı yerine usage basar. |

`--check-policy` asla 3 kodu döndürmez. Başarılı bir rapor 0 ile çıkar; verilen `--config`'i yükleme başarısızlığı ise yanıltıcı bir "ihlal yok" raporuna düşmek yerine `EXIT_CONFIG_ERROR` (sıfır olmayan) ile çıkar. CI gate isteyen operatörler ihlal sayısını JSON çıktıdan kendileri hesaplar (design §10 Q5).

## Madde 15 erişim hakkı (`forgelm reverse-pii`)

GDPR'nin *diğer* veri-sahibi hakkı — "verim corpus'ta var mı?" —
için companion subcommand `forgelm reverse-pii`.  `purge` "satırımı
sil" sorusuna yanıt verirken, `reverse-pii` "kimlik bilgim hangi
satırlarda görünüyor" sorusuna yanıt verir.

```shell
# Plaintext residual scan: mask leak'leri tespit eder (operatör
# corpus'un maskelendiğine inanıyordu ama bir residual span maskeleme
# pass'ında kaçmış).
$ forgelm reverse-pii --query "ali@example.com" --type email \
    --output-dir ./outputs data/*.jsonl

# Hash-mask scan: corpus DIŞSAL bir pipeline ile maskelenmiş —
# `purge`'ün `target_id` audit-event hash'lemesinde kullandığı
# per-output-dir salt'ını paylaşan SHA256(salt + identifier)
# digest'leri corpus'a gömülmüş.  ForgeLM'in kendisi bir
# hash-replacement ingest stratejisi YOLLAMAZ; bu mod, purge'ün
# salt'ını ortak gizli olarak kullanan harici bir pipeline kuran
# operatörler içindir.
$ forgelm reverse-pii --query "ali@example.com" --type email \
    --salt-source per_dir --output-dir ./outputs data/*.jsonl
```

Audit chain `data.access_request_query`'yi identifier *salt'lı
hash'lenmiş* olarak kaydeder — `forgelm purge`'ün `target_id` field'ı
için kullandığı aynı per-output-dir salt'ı yeniden kullanılır.
Madde 15 erişim talepleri kendisi de subject'in verisini audit log'a
sızdırmamalıdır.  Salt'lı form, compliance reviewer'ın aynı veri sahibi
için Madde 17 (purge) ve Madde 15 (reverse-pii) olaylarını cleartext
görmeden ilişkilendirmesini sağlar.  Digest'ler yalnızca `--row-id` ve
`--query` değerleri aynı metinse, iki komut da aynı `--output-dir`'i
kullanıyorsa ve `FORGELM_AUDIT_SECRET` ikisinde de aynı durumdaysa (ayarsız ya
da aynı değer) eşleşir; erişim talebini varsayılan düz-metin modunda çalıştırın:
`--salt-source` digest'lerin eşleşmesi için gerekmez; onunla hash-mask taraması
corpus içinde digest arar, bu yüzden düz metin içeren bir corpus'ta kişi orada
olsa bile sıfır eşleşme alırsınız.
Id'ler farklıysa erişim talebini ilgili silme işlemine,
`forgelm purge --justification`'a verdiğiniz ticket referansı üzerinden
bağlayın.

**Identifier tipleri** (`--type`): `literal` (varsayılan), `email`,
`phone`, `tr_id`, `us_ssn`, `iban`, `credit_card`, `custom`.
`custom` dışındaki tüm tipler query'i literal substring olarak ele
alır (regex şekil-match yapmaz — o iş audit-time detector'ın görevi,
erişim talebinin değil).  `custom` query'i Python regex olarak
yorumlar; POSIX **ana iş parçacığında** çalışırken 30s'lik per-file
SIGALRM bütçesi ReDoS hang'lerine karşı koruma sağlar.  Windows VE
POSIX worker thread'lerinde SIGALRM guard no-op olur (sinyal
handler'ları yalnızca ana iş parçacığından kurulabilir); worker
thread'den veya Windows üzerinde `forgelm reverse-pii --type custom`
çalıştıran operatörler regex'lerini kendileri doğrulamalıdır.

**Audit-dir varsayılanı**: audit chain varsayılan olarak
`<output-dir>/audit_log.jsonl`'a yazılır — `forgelm purge` ile aynı
yola, böylece bir `verify-audit` koşumu aynı subject için Madde 17
(silme) ve Madde 15 (erişim) olaylarını tek bir chain üzerinde
ilişkilendirir.  Override için `--audit-dir <writable-dir>` geçin;
explicit verilen `--audit-dir`'a yazılamıyorsa dispatcher Madde 15
forensic kaydını sessizce düşürmek yerine `EXIT_TRAINING_ERROR` ile
reddeder.

**Exit kodları:** `0` = scan tamamlandı (matches listesi boş
olabilir); `1` = config hatası (boş query, malformed regex, boş
glob); `2` = runtime hatası (mid-scan I/O failure).  JSON envelope
şeması için bkz.
[`../usermanuals/tr/reference/json-output.md`](../usermanuals/tr/reference/json-output.md).

## Bkz.

- `docs/qms/sop_data_management.md` — veri yaşam döngüsü SOP'si (toplama, kalite, saklama); bir saklama ya da silme prosedürü içermez, bunu bu kılavuzdan yola çıkarak yazın.
- `docs/usermanuals/tr/compliance/overview.md` — buraya link veren operator-facing compliance overview.
- [`docs/design/gdpr_erasure.md`](../design/gdpr_erasure.md) — bu implementation'ın gerçekleştirdiği Phase 20 design dokümanı.
