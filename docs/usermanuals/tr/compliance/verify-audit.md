---
title: Audit Log Doğrulama
description: Bir audit_log.jsonl dosyasının SHA-256 hash zincirini (ve isteğe bağlı HMAC etiketlerini) Madde 12 kanıtı saymadan önce doğrulayın.
---

# Audit Log Doğrulama

`forgelm verify-audit`, Madde 12 kayıt-tutma log'unun salt-okunur doğrulayıcısıdır. Eğitim koşunuzun ürettiği `audit_log.jsonl`'un yapısal olarak bütün olduğunu kontrol eder: SHA-256 hash zinciri satır satır doğru ilerliyor, genesis manifest sidecar (varsa) ilk girdiyi çapraz kontrol ediyor ve — ortamda bir operatör sırrı varsa — satır başına HMAC etiketleri yetkilendiriyor. CI pipeline'ları, audit log'u kanıt sayma kararını veren eğitim sonrası adıma bunu bağlar.

## Ne zaman kullanılır

- **Düzenleyiciye veya denetçiye bir audit paketi sunmadan önce.** Temiz bir `verify-audit` çıktısı göndermeniz gereken minimum bütünlük kanıtıdır.
- **CI/CD yayın kapılarında.** Her eğitim pipeline'ından sonra çalıştırın; çıkış `6`'da (zincir/HMAC tahrifatı — log okundu ve doğrulanmadı) veya `1`'de (seçenek/kullanım hatası — doğrulayıcı hiç çalışmadı) yayını başarısız sayın.
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
    loop her girdi
        Verify->>Verify: sha256(prev_line) yeniden hesapla
        Verify->>Verify: prev_hash alanını karşılaştır
        opt ortamda sır
            Verify->>Verify: HMAC(satır - _hmac) yeniden hesapla
            Verify->>Verify: _hmac alanını karşılaştır
        end
    end
    Verify->>Manifest: yükle + ilk girdi hash'iyle çapraz kontrol
    Verify-->>CI: çıkış 0 (temiz) / 1 (seçenek hatası, hiç çalışmadı) / 2 (G/Ç hatası) / 6 (zincir veya HMAC bütünlük arızası)
```

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

Satır numarası olmayan çıplak bir neden, hatanın zincir yürüyüşünden önce meydana geldiğini gösterir (örn. eksik genesis manifest, satır 1'de JSON çözüm hatası):

```text
FAIL: manifest present but unreadable at 'checkpoints/run/audit_log.jsonl.manifest.json': …
```

Her iki durumda da log dosyasının kendisi bulundu ve okundu — bu yüzden bu bir bütünlük kararıdır ve çıkış kodu `1` değil `6`'dır. Log'u kanıt saymadan önce inceleyin. `1`, doğrulayıcının log'u okumaya bile başlayamadığı durum için ayrılmıştır (eksik yol, sırsız `--require-hmac`).

### Çıkış-kodu özeti

| Kod | Anlam |
|---|---|
| `0` | Zincir (ve doğrulandığında HMAC etiketleri) uçtan uca bütün. |
| `1` | Seçenek/kullanım hatası, ya da log bulunamadı: sırsız `--require-hmac`, veya log yolu eksik / bir dizin. Doğrulayıcı hiç çalışmadı, dolayısıyla bir bütünlük kararı yok. |
| `2` | Erişilebilir bir log üzerinde gerçek çalışma-zamanı G/Ç hatası (izin reddi, okuma-ortası hata). Tekrar denenebilir. |
| `6` | Tahrifat / bozulma tespiti: zincir kopması, HMAC uyuşmazlığı, genesis-manifest uyuşmazlığı, çözülemeyen satır veya geçerli-olmayan UTF-8 baytları — log okundu ve doğrulanmadı. |

## Sık hatalar

:::warn
**HMAC doğrulamasını "zincir hash'i yeter" diyerek atlamak.** Zincir hash'i tek-satırlık düzenlemelere ve yeniden sıralamaya karşı savunur; ancak yazma erişimine sahip kararlı bir saldırgan tüm zinciri uçtan uca yeniden yazabilir. HMAC etiketleri çıtayı "operatör sırrını da taklit etmek lazım" seviyesine çıkarır; sır bir HSM'de yaşıyorsa anlamlıdır.
:::

:::warn
**`verify-audit`'i, log'u yazan ana makinede secret-host ayrımı olmadan çalıştırmak.** Saldırganın hem yazma erişimi hem HMAC sırrı varsa HMAC ek bir savunma katmaz. Log'u, sırrı yazıcı host'un okuyamadığı bir KMS veya HSM içinde tutan ayrı bir doğrulayıcı host'a gönderin.
:::

:::warn
**Eksik `<log>.manifest.json`'u zararsız saymak.** Genesis manifest, kesme-ve-devam ettirme tespitçisidir. Uzun-süreli bir deployment'ta eksikse, saldırgan log'u zincir kopması görünmeden "yalnız genesis"e geri sarmış olabilir. Eğitim sonrası artifact paketinizde manifest'in mevcut olduğunu doğrulayın.
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
