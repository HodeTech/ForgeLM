---
title: Audit Log Doğrulama
description: Bir audit_log.jsonl dosyasının SHA-256 hash zincirini (ve isteğe bağlı HMAC etiketlerini) Madde 12 kanıtı saymadan önce doğrulayın.
---

# Audit Log Doğrulama

`forgelm verify-audit`, Madde 12 kayıt-tutma log'unun salt-okunur doğrulayıcısıdır. Eğitim koşunuzun ürettiği `audit_log.jsonl`'un yapısal olarak bütün olduğunu kontrol eder: SHA-256 hash zinciri satır satır doğru ilerliyor, genesis manifest sidecar (varsa) ilk girdiyi çapraz kontrol ediyor ve — ortamda bir operatör sırrı varsa — satır başına HMAC etiketleri yetkilendiriyor. CI pipeline'ları, audit log'u kanıt sayma kararını veren eğitim sonrası adıma bunu bağlar.

## Ne zaman kullanılır

- **Düzenleyiciye veya denetçiye bir audit paketi sunmadan önce.** Temiz bir `verify-audit` çıktısı göndermeniz gereken minimum bütünlük kanıtıdır.
- **CI/CD yayın kapılarında.** Her eğitim pipeline'ından sonra çalıştırın; çıkış `6`'da (zincir/HMAC tahrifatı, ya da manifest'i olan bir log'un sıfır girdiye truncate edilmesi — doğrulayıcı bir şeyi karşılaştırdı ve tutmadı) veya `1`'de (hiçbir şey karşılaştırılamadı — seçenek/kullanım hatası, eksik yol, ya da manifest'siz boş bir log) yayını başarısız sayın.
- **Log'u makineler arasında taşıdıktan sonra.** Aktarımda oluşan herhangi bir bayt-seviyesi bozulma zincir kopması olarak ortaya çıkar.
- **Periyodik uyumluluk taramasının parçası olarak.** Geçmiş log'lar üzerinde gece çalışan bir cron, sessiz tahrifatları erken yakalar.

## Nasıl çalışır

```mermaid
sequenceDiagram
    participant CI as CI / operatör
    participant Verify as forgelm verify-audit
    participant Log as audit_log.jsonl
    participant Manifest as audit_log.jsonl.manifest.json

    CI->>Verify: verify-audit log_path
    Verify->>Log: satırları akıt
    alt sıfır girdi
        Verify->>Manifest: bir genesis manifest var mı?
        alt manifest bir ilk girdi sabitliyor (ya da bozuk)
            Verify-->>CI: çıkış 6 — sıfır girdiye truncate edilmiş
        else manifest yok
            Verify-->>CI: çıkış 1 — referans yok, hiçbir şey karşılaştırılamadı
        end
    else bir veya daha fazla girdi
        loop her girdi
            Verify->>Verify: sha256(prev_line) yeniden hesapla
            Verify->>Verify: prev_hash alanını karşılaştır
            opt ortamda sır
                Verify->>Verify: HMAC(satır - _hmac) yeniden hesapla
                Verify->>Verify: _hmac alanını karşılaştır
            end
        end
        Verify->>Manifest: yükle + ilk girdi hash'iyle çapraz kontrol
        Verify-->>CI: çıkış 0 (temiz) / 2 (G/Ç hatası) / 6 (zincir veya HMAC bütünlük arızası)
    end
```

Var olan ama **sıfır girdi** tutan bir log asla `0` ile çıkmaz. Bu meşru bir taze-çalıştırma durumu değildir: `AuditLogger` çıktı dizinini oluşturur ama log dosyasını oluşturmaz; dosya ve genesis manifest'i ilk event tarafından birlikte yazılır — yani hiç kullanılmamış bir log *yok*tur (çıkış `1`, `audit log not found`), boş değil.

## Hızlı başlangıç

```shell
$ forgelm verify-audit checkpoints/run/audit_log.jsonl
OK: 87 entries verified
```

HMAC ile yetkilendirilmiş log'lar için önce operatör sırrını set edin:

```shell
$ FORGELM_AUDIT_SECRET="$(cat /run/secrets/audit-secret)" \
    forgelm verify-audit checkpoints/run/audit_log.jsonl
OK: 87 entries verified (HMAC validated)
```

## Ayrıntılı kullanım

### Regüle CI için sıkı mod

Her kaydın HMAC ile yetkilendirilmiş olması gerektiğinde (kurumsal denetim profili) `--require-hmac`'i geçirin:

```shell
$ FORGELM_AUDIT_SECRET="$(cat /run/secrets/audit-secret)" \
    forgelm verify-audit --require-hmac \
        checkpoints/run/audit_log.jsonl
```

Sıkı mod iki güvenlik ağını birden devreye sokar:

- Yapılandırılmış env var set değilse, çıkış `1` (operatörün düzeltebileceği ön-uçuş hatası — doğrulayıcı hiç çalışmadı). Pipeline'ı çalıştırmadan önce sırrı yüklemeyi unutan operatörü yakalar.
- Herhangi bir satırda `_hmac` alanı eksikse, çıkış `6` (log okundu ve sıkı-mod zincir doğrulamasını geçemedi). HMAC'in koşu ortasında kapatıldığı karışık-mod log'larını yakalar.

### Varsayılan olmayan bir sır değişkenini adlandırma

Çok-kiracılı CI için her kiracının kendi sır env adı vardır:

```shell
$ TENANT_ACME_AUDIT_KEY="$(cat /run/secrets/acme-audit)" \
    forgelm verify-audit --hmac-secret-env TENANT_ACME_AUDIT_KEY \
        artifacts/acme/audit_log.jsonl
```

Değişken adı yapılandırılabilir; varsayılan `FORGELM_AUDIT_SECRET`'tir.

### Hata çıktısını okuma

Bir zincir kopması 1-tabanlı satır numarasını yazar:

```text
FAIL at line 53: prev_hash mismatch — chain break suggests entry was inserted, removed, or reordered
```

Genesis-manifest hataları, manifest'in sabitlediği girdi olan 1. satıra atfedilir; dolayısıyla onlar da bir satır numarası taşır:

```text
FAIL at line 1: manifest present but unreadable at 'checkpoints/run/audit_log.jsonl.manifest.json': …
```

(En az bir girdi taşıyan bir log'da manifest'in *hiç olmaması* bir hata değildir: doğrulayıcı, truncate-and-resume tespitinin yalnızca zincir-içi hash sürekliliğiyle sınırlı kaldığına dair bir uyarı yazar ve devam eder. **Sıfır** girdili bir log'da ise eksik manifest belirleyicidir — ortada hiçbir referans yoktur, bu yüzden komut `1` ile çıkar; aşağıdaki özette boş-log satırlarına bakın.)

Hiç satır numarası taşımayan çıplak bir neden, hatanın tek bir girdinin değil dosyanın bütününün bir özelliği olduğunu gösterir — CLI üzerinden erişilebilen durum, UTF-8 olmayan bayt içeren bir log'dur:

```text
FAIL: audit log is not valid UTF-8: 'utf-8' codec can't decode byte 0xff in position 0: invalid start byte
```

Yukarıdaki durumların hepsinde log dosyasının kendisi bulundu ve okundu — bu yüzden bu bir bütünlük kararıdır ve çıkış kodu `1` değil `6`'dır. Log'u kanıt saymadan önce inceleyin. `1`, doğrulayıcının log'u okumaya bile başlayamadığı durum için ayrılmıştır (eksik yol, sırsız `--require-hmac`).

### Çıkış-kodu özeti

| Kod | Anlam |
|---|---|
| `0` | En az bir girdi okundu ve zincir (doğrulandığında HMAC etiketleriyle birlikte) uçtan uca bütün. |
| `1` | Hiçbir şey karşılaştırılamadı: sırsız `--require-hmac`; log yolu eksik / bir dizin; ya da log var, **sıfır girdi** tutuyor ve ne tutması gerektiğini söyleyen bir genesis manifest yok. Bütünlük kararı yok. |
| `2` | Erişilebilir bir log üzerinde gerçek çalışma-zamanı G/Ç hatası (izin reddi, okuma-ortası hata). Tekrar denenebilir. |
| `6` | Tahrifat / bozulma tespiti: zincir kopması, HMAC uyuşmazlığı, genesis-manifest uyuşmazlığı, çözülemeyen satır, geçerli-olmayan UTF-8 baytları veya **genesis manifest'i bir ilk girdi sabitleyen sıfır-girdili bir log** (boşa truncate edilmiş) — doğrulayıcı bir şeyi karşılaştırdı ve tutmadı. |

Boş-log ayrımını iki kez okumaya değer, çünkü iki yarısı da `FAIL`'dir ama yalnızca biri güvenlik çağrısıdır. Manifest'i hayatta kalan sıfır-girdili bir log bir **truncate**'tir (`6`): manifest, saldırganın taklit edemeyeceği bir-kez-yazılan referanstır, 1. satırın var olduğunu söyler ve o satır yoktur. **Manifest'i olmayan** sıfır-girdili bir log ise bir **girdi hatası**dır (`1`): referansın kendisi eksikken doğrulayıcı silinmiş bir log ile yanlış yazılmış bir yolu gerçekten ayırt edemez ve birinin `touch`ladığı bir dosya için tahrifat bildirmek boş yere alarm vermek olurdu. Her iki durumda da log kanıt değildir — sunmadan önce inceleyin.

## Sık hatalar

:::warn
**HMAC doğrulamasını "zincir hash'i yeter" diyerek atlamak.** Zincir hash'i tek-satırlık düzenlemelere ve yeniden sıralamaya karşı savunur; ancak yazma erişimine sahip kararlı bir saldırgan tüm zinciri uçtan uca yeniden yazabilir. HMAC etiketleri çıtayı "operatör sırrını da taklit etmek lazım" seviyesine çıkarır; sır bir HSM'de yaşıyorsa anlamlıdır.
:::

:::warn
**`verify-audit`'i, log'u yazan ana makinede secret-host ayrımı olmadan çalıştırmak.** Saldırganın hem yazma erişimi hem HMAC sırrı varsa HMAC ek bir savunma katmaz. Log'u, sırrı yazıcı host'un okuyamadığı bir KMS veya HSM içinde tutan ayrı bir doğrulayıcı host'a gönderin.
:::

:::warn
**Eksik `<log>.manifest.json`'u zararsız saymak.** Genesis manifest, kesme-ve-devam ettirme tespitçisidir. Uzun-süreli bir deployment'ta eksikse, saldırgan log'u zincir kopması görünmeden "yalnız genesis"e geri sarmış olabilir. Eğitim sonrası artifact paketinizde manifest'in mevcut olduğunu doğrulayın — ve manifest'i kaybetmenin boş-log kararını `6`'dan `1`'e *düşürdüğünü* unutmayın, çünkü doğrulayıcı truncate'i kanıtlayabilecek tek referansı yitirir. Hem log'u hem manifest'ini silen bir saldırgan `1`'e düşer ve bir yazım hatasından ayırt edilemez. İkisini birlikte paketleyin ve birlikte yedekleyin.
:::

:::tip
**Doğrulayıcıyı CI'da herhangi bir sunum adımından önce sabitleyin.** Her eğitim koşusundan sonra `forgelm verify-audit --require-hmac`'i sert bir kapı olarak bağlayın. Çıkış `6` (tahrifat) veya `1` (operatör sırrının eksik olduğu ön-uçuş durumu) ikisi de yayını başarısız etmeli.
:::

## Bkz.

- [Audit Log](#/compliance/audit-log) — bu komutun doğruladığı log'a dair operatör-odaklı kılavuz.
- [Annex IV](#/compliance/annex-iv) — doğrulayıcısı (`forgelm verify-annex-iv`) bu komutun tasarım desenini paylaşan teknik dokümantasyon artifact'ı.
- [GGUF Doğrulama](#/deployment/verify-gguf) — deployment-bütünlük yüzeyindeki kardeş doğrulayıcı.
- [Model Bütünlüğünü Doğrulama](#/compliance/verify-integrity) — Article 15 model-bütünlük manifestinin kardeş doğrulayıcısı.
- [`audit_event_catalog-tr.md`](https://github.com/HodeTech/ForgeLM/blob/main/docs/reference/audit_event_catalog-tr.md) — doğrulanan log'un *içinde* görünen event'ler (GitHub kaynağı).
