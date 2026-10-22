---
title: GDPR Silme & Erişim
description: `forgelm purge` ve `forgelm reverse-pii` subcommand'ları ile Madde 15 (erişim) ve Madde 17 (silme) taleplerini destekleyin.
---

# GDPR Silme & Erişim

ForgeLM, iki yaygın GDPR veri-sahibi talebini destekleyen kardeş subcommand'lar ship eder: `forgelm purge` satır kaldırır (Madde 17 silmenin bir adımı) ve `forgelm reverse-pii` satır bulur (Madde 15 yanıtının bir girdisi). Hiçbiri tek başına eksiksiz bir yanıt değildir. Per-output-dir salt'ı paylaşırlar; böylece compliance reviewer aynı veri sahibi için silme ve erişim taleplerini tek bir tamper-evident audit zincirinde — cleartext identifier operatör terminalinden hiç çıkmadan — korele edebilir.

Bu sayfa operatör hızlı-referansıdır. Daha derin deployer akışı [GitHub'daki GDPR silme deployer kılavuzunda](https://github.com/HodeTech/ForgeLM/blob/main/docs/guides/gdpr_erasure-tr.md) bulunur; flag-başına referanslar manuel içindeki [CLI Referansı](#/reference/cli) ve GitHub'daki özel subcommand sayfaları [`purge_subcommand-tr.md`](https://github.com/HodeTech/ForgeLM/blob/main/docs/reference/purge_subcommand-tr.md) + [`reverse_pii_subcommand-tr.md`](https://github.com/HodeTech/ForgeLM/blob/main/docs/reference/reverse_pii_subcommand-tr.md) sayfalarındadır.

## İki hak, tek zincir

| Talep | Subcommand | Ne yapar |
|---|---|---|
| **Madde 15** — erişim | `forgelm reverse-pii` | Taradığınız JSONL corpus'larında subject identifier'ını içeren satırları bulur: yanıtın bir girdisidir, yanıtın kendisi değil. |
| **Madde 17** — silme | `forgelm purge` | Bir corpus satırını id ile atomik olarak siler (silmenin bir adımı, tamamı değil); `--run-id` ile bir staging dizinini veya adı run id'yi embed eden compliance dosyalarını siler. |

İki subcommand da audit satırını kaydetmeden önce subject identifier'ını **aynı** per-output-dir salt (`<output_dir>/.forgelm_audit_salt`) ile hash'ler. `data.access_request_query` üzerindeki `query_hash` ve `data.erasure_*` üzerindeki `target_id` yalnızca `--row-id` değeri ile `--query` değeri aynı metinse, iki komut da aynı `--output-dir`'i kullanıyorsa ve `FORGELM_AUDIT_SECRET` ikisinde de aynı durumdaysa byte-byte aynıdır; aksi halde ikisini `forgelm purge --justification`'a verdiğiniz ticket referansıyla bağlayın.

## Madde 17 — `forgelm purge`

### Üç mod

```shell
# Corpus-satır silme
forgelm purge --row-id "ali@example.com" --corpus train.jsonl \
    --output-dir ./outputs --justification "GDPR Mad.17 ticket #1234"

# Koşum-kapsamlı silme
forgelm purge --run-id fg-abc123def456 --kind staging --output-dir ./outputs
forgelm purge --run-id fg-abc123def456 --kind artefacts --output-dir ./outputs   # standart pakette hiçbir şey bulmaz, aşağıya bakın

# Retention-policy raporu (salt-okunur)
forgelm purge --check-policy --config configs/run.yaml --output-dir ./outputs
```

### Neyi kaydeder

Audit zincirine altı event yazılır (bkz. [GitHub'daki Audit Event Kataloğu](https://github.com/HodeTech/ForgeLM/blob/main/docs/reference/audit_event_catalog-tr.md)):

- `data.erasure_requested` — herhangi bir silmeden ÖNCE, ilk yayılır.
- `data.erasure_completed` — disk operasyonu başarılı sonra son yayılır.
- `data.erasure_failed` — disk operasyonu raise ettiğinde veya eşleşen satır/koşum bulunmadığında bunun yerine yayılır.
- `data.erasure_warning_memorisation` — corpus-satır silme × `--output-dir` altında doğrudan bir `final_model/` mevcut (satır diskten gitti ama eğitilmiş ağırlıklarda hâlâ memorize edilmiş olabilir).
- `data.erasure_warning_synthetic_data_present` — corpus-satır silme × `output_dir`'de `synthetic_data*.jsonl` mevcut.
- `data.erasure_warning_external_copies` — yalnızca corpus-satır silmede ve yalnızca `--config` verdiğinizde: config'in `webhook` bloğu boş değil (literal bir `url` ya da purge ortamında export edilmiş bir `url_env` değişkeni); downstream tüketiciler bildirim almış olabilir.

### Neyi YAPMAZ

- **Modelleri yeniden eğitmez.** Bir satırı corpus'tan kaldırmak onu zaten eğitilmiş ağırlıklardan unutturmaz — silinmiş satır olmadan tam yeniden eğitim tek doğru çözümdür. Tool `data.erasure_warning_memorisation` yayar; böylece boşluk görünür olur.
- **Audit log'unu silmez.** Tool, silmeyi geçmişi yeniden yazmak yerine yeni bir event olarak kaydeder ve audit log'dan hiçbir girdiyi silmez. Veri sahibini adlandıran girdilerin tutulup tutulamayacağı veri sorumlusunun kararıdır ve belgelenmelidir. Bir Madde 17(1) gerekçesi geçerliyse girdiler yalnızca bir Madde 17(3) istisnasının kapsadığı yerde kalır: 17(3)(b), yalnızca işlemenin veri sorumlusunun tabi olduğu AB veya üye devlet hukukundan doğan bir yasal yükümlülüğe uyum için (veya kamu yararına ya da kamu otoritesinin kullanılmasına ilişkin bir görev için) gerekli olduğu ölçüde uygulanır; 17(3)(e) ise hukuki talepleri kapsar. Hiçbir Madde 17(1) gerekçesi geçerli değilse (örneğin girdiler toplandıkları amaç için hâlâ gereklidir) silme gerekmez; ancak talebe yine de Madde 12 süresi içinde gerekçeli bir yanıt verilir ve şikâyet hakkı bildirilir (Madde 12(4)).
- **İşaretsiz legacy `final_model.staging/` dizinini silmez.** `--kind staging`, `final_model.staging.<run_id>/` dizinini siler; legacy `final_model.staging/` dizinini yalnızca içinde run id'ye eşit bir `staging_run_id` dosyası varsa siler. ForgeLM bu işaretçiyi yazmaz, bu yüzden işaretsiz bir legacy dizini kendiniz silin.
- **`--kind artefacts` ile standart compliance paketini bulmaz.** Trainer run id içermeyen sabit dosya adları yazar (`compliance_report.json`, `training_manifest.yaml`, ...); resolver ise yalnızca run id'yi embed eden dosya adlarını eşler. Normal bir çıktı dizininde komut hiçbir şey bulmaz, `data.erasure_failed` (`NoMatchingArtefacts`) yayar, 1 ile çıkar ve hiçbir şey silmez; paketi depolama katmanınız üzerinden kaldırın.
- **Downstream tüketicilere bildirim göndermez.** Webhook receiver'ları, dataset mirror'ları ve deploy edilmiş model endpoint'leri operatörün runtime-katmanı sorumluluğudur (GDPR Madde 19, verinin ifşa edildiği her alıcıya silmeyi bildirmeyi ister; bu, imkânsız olmadıkça veya orantısız çaba gerektirmedikçe geçerlidir ve veri sahibi isterse bu alıcıları ona da bildirmek gerekir; Madde 17(2) ise veri sorumlusu veriyi kamuya açtıysa makul adımlar ekler). `data.erasure_warning_external_copies` event'ı açık hatırlatmadır.
- **Yanıtın tamamını vermez.** Eksiksiz bir yanıt, ForgeLM'in yapmadığı adımları da gerektirir: talep edenin kimliğini doğrulamak (Madde 12(6)); talebin alınmasından itibaren bir ay içinde yanıtlamak (Madde 12(3); karmaşık veya çok sayıda talep için iki ay daha uzatılabilir, uzatma ilk ay içinde bildirilir); veri sahibine verinin bir kopyasını ve Madde 15(1)(a)-(h) bilgilerini vermek (Madde 15(3)) ve bunu başkalarının hak ve özgürlüklerini olumsuz etkilemeden yapmak (Madde 15(4)); bir Madde 17(1) gerekçesinin geçerli olup olmadığına ve tuttuğunuz şeyi hangi Madde 17(3) istisnasının (varsa) kapsadığına karar vermek; alıcıları bilgilendirmek (Madde 19); satırların gittiğini doğrulamak için purge sonrasında erişim aramasını yeniden çalıştırmak; ve bir talebi reddederseniz aynı süre içinde gerekçeyi ve şikâyet hakkını bildirmek (Madde 12(4)). Eğitilmiş modelin kişisel veri sayılıp sayılmayacağı ayrı bir sorudur (EDPB Görüş 28/2024).

## Madde 15 — `forgelm reverse-pii`

### İki scan modu

```shell
# Plaintext residual scan: mask sızıntılarını tespit eder (operatör
# corpus'un maskelendiğini sandı ama bir residual span sızdı).
forgelm reverse-pii --query "alice@example.com" --type email \
    --output-dir ./outputs data/*.jsonl

# Hash-mask scan: corpus, purge'ün per-output-dir salt'ını paylaşılan
# secret olarak kullanan HARİCİ bir pipeline tarafından SHA256(salt +
# identifier) digest'leriyle maskelenmişti.
forgelm reverse-pii --query "alice@example.com" --type email \
    --salt-source per_dir --output-dir ./outputs data/*.jsonl
```

### `reverse-pii` neyi kaydeder

Çağrı başına bir event (bkz. katalog):

- `data.access_request_query` — scan'den sonra (veya mid-scan I/O hatası sonrası, `error_*` field'larıyla) yayılır. **Hash'lenmiş** `query_hash`'i, `identifier_type`'ı, `scan_mode`'u, `files_scanned` listesini, `match_count`'u ve `salt_source`'ü taşır.

Chain satırı cleartext identifier'ı asla taşımaz, yalnızca salt'lı bir digest taşır. Digest pseudonymised'dır, anonim değildir: salt dosyası log'un yanında durur; ikisini de okuyabilen biri tahmin ettiği bir identifier'ı onunla sınayabilir. Log'u ve salt'ı aynı erişim kontrolleriyle koruyun ve digest'leri kişisel veri sayın.

### `--type custom` regex caveat

`--type custom` `--query`'yi Python regex olarak yorumlar. POSIX main-thread çağrılarında dosya başına 30s SIGALRM bütçesi ReDoS hang'lerine karşı koruma sağlar. **Windows'ta VE POSIX worker thread'lerinde SIGALRM guard no-op'tur** — worker thread'den veya Windows'ta `--type custom` çalıştıran operatörler regex'lerini kendileri vet etmelidir.

## Salt + audit-secret ayrımı

`<output_dir>/.forgelm_audit_salt`'taki per-output-dir salt (ilk kullanımda mode `0600`, atomik O_EXCL yazma) **identifier-hash salt'ıdır**. Audit-chain HMAC anahtarına katılmaz; o anahtar bağımsız olarak ForgeLM'in `AuditLogger`'ı içinde `SHA-256(FORGELM_AUDIT_SECRET ‖ run_id)` şeklinde türetilir.

İki primitif kasıtlı olarak ayrıdır:

- `FORGELM_AUDIT_SECRET` rotasyonu chain HMAC anahtarını **ve**, değişken ayarlıysa, identifier-hash salt'ını rotate eder: `forgelm purge` secret'ın ilk 16 byte'ını per-output-dir salt'a XOR'lar ve `salt_source=env_var` kaydeder; bu yüzden `target_id` digest'leri secret ile değişir. Düz-metin bir `forgelm reverse-pii` sorgusu (`--salt-source` yok) salt'ı aynı şekilde çözer; bu yüzden aynı secret durumunda koşturulmuş bir purge ile korele eder. `--salt-source` farklı bir özelliktir: identifier'ları ForgeLM dışında hash'lenmiş corpus'lar için hash-mask taramasıdır ve düz metin içeren bir corpus'ta hiçbir şey bulmaz.
- Birini incelemek diğerini ifşa etmez.
- Per-output-dir salt identifier hash'lemenin her zaman bir girdisidir ve output dizininin yaşam süresi boyunca kalıcıdır; secret, ayarlıysa, ikinci girdidir.

## Exit kodları

İki subcommand da projenin herkese açık exit-code sözleşmesinin (0-6) ilk üç kodunu kullanır:

| Kod | Anlamı |
|---|---|
| 0 | Başarı. İhlal bulunduğunda bile `forgelm purge --check-policy` 0 ile çıkar (report-not-gate semantik). Eşleşme bulunmadığında bile `forgelm reverse-pii` 0 ile çıkar (sıfır eşleşme yalnızca literal metnin taradığınız dosyalarda olmadığını söyler; kişi hakkında veri tutmadığınızı tek başına doğrulamaz). |
| 1 | Config hatası: eksik `--kind`, `--corpus`'suz `--row-id`, hatalı regex, `--run-id` ile eşleşen staging dizini / compliance dosyası yok, yüklenemeyen bir `--config`. |
| 2 | Runtime hatası: I/O başarısızlığı, izin reddedildi, yazılamaz `--audit-dir`, argüman-parser kullanım hatası (birlikte verilen iki mod, `--kind logs`), ReDoS SIGALRM timeout, atomic-rename başarısızlığı. |

Kod 3 ile 6 arası (`EXIT_EVAL_FAILURE`, `EXIT_AWAITING_APPROVAL`, `EXIT_WIZARD_CANCELLED`, `EXIT_INTEGRITY_FAILURE`) iki subcommand'ın da yüzeyinin parçası değildir.

## Sık hatalar

:::warn
**Subject identifier'ını `--justification`'a yapıştırmak.** Justification audit zincirine verbatim kaydedilir. Bunun yerine dahili ticket id'nizi referans verin ("GDPR Mad.17 ticket #1234").
:::

:::warn
**Memorization'ı kontrol etmeden `forgelm purge` çalıştırmak.** Eğitilmiş bir model varsa, satır diskten gitti ama ağırlıklarda hâlâ encoded olabilir. `data.erasure_warning_memorisation` event'ı boşluğu yalnızca `--output-dir` altında doğrudan bir `final_model/` varsa yüzeyselleştirir — `--output-dir` varsayılan olarak corpus'un dizinidir, bu yüzden eğitim çıktı dizininizi verin; harekete geçin (silinmiş satır olmadan yeniden eğitin veya DPIA'nızda residual riski belgeleyin).
:::

:::tip
**`--output-dir`'i explicit olarak pin'leyin.** İlk corpus path'inden `--output-dir`'i çözen implicit fallback çözülen dizini adlandıran bir WARNING yayar; `purge` ve `reverse-pii` arasında cross-tool korelasyon için her zaman `--output-dir`'i explicit verin; iki subcommand da aynı `.forgelm_audit_salt`'ı görsün.
:::

:::tip
**`forgelm verify-audit`'i DSAR kapanışınıza wire'layın.** Erasure / access event zincire girdikten sonra `forgelm verify-audit <output_dir>/audit_log.jsonl` çalıştırın ve doğrulama çıktısını DSAR ticket'a iliştirin. `--require-hmac`'i yalnızca zincir yazılırken `FORGELM_AUDIT_SECRET` ayarlıysa ve şimdi de ayarlıysa ekleyin: bayrak secret olmadan 1 ile çıkar ve herhangi bir satırda HMAC etiketi yoksa 6 ile çıkar; yani secret olmadan yazılmış bir zincirde tasarım gereği başarısız olur. Auditor okuyacak.
:::

## Bkz.

- [Audit Log](#/compliance/audit-log) — `data.*` event'lerinin kaydedildiği yer.
- [İnsan Gözetimi](#/compliance/human-oversight) — yüksek-riskli deployment'lar için GDPR haklarıyla eşleşen Madde 14 kardeş kapısı.
- [GDPR silme deployer kılavuzu](https://github.com/HodeTech/ForgeLM/blob/main/docs/guides/gdpr_erasure-tr.md) — tam deployer akışı (GitHub kaynağı).
- [`purge_subcommand-tr.md`](https://github.com/HodeTech/ForgeLM/blob/main/docs/reference/purge_subcommand-tr.md) — `forgelm purge` flag-başına referans (GitHub kaynağı).
- [`reverse_pii_subcommand-tr.md`](https://github.com/HodeTech/ForgeLM/blob/main/docs/reference/reverse_pii_subcommand-tr.md) — `forgelm reverse-pii` flag-başına referans (GitHub kaynağı).
