# GPU olmadan ForgeLM'i deneyin

> CPU üzerinde yapılandırmayı doğrulayın, sekiz sentetik satırı denetleyin ve kaydedilmiş audit log zincirini kontrol edin.
> **Ayna:** [English](README.md)

## Kontrolleri çalıştırın

Çalışma kopyasını kurmak için [katkıda bulunanların kurulum adımlarını](../../../CONTRIBUTING.md#your-first-pull-request)
izleyin. Üretilen raporların kaydedilmiş dosyaları değiştirmemesi için örneği geçici dizine kopyalayın:

```bash
# From the repository root, with ForgeLM installed in the active environment:
FORGELM_DEMO_DIR=$(mktemp -d)
cp -R docs/guides/cpu-demo/. "$FORGELM_DEMO_DIR/"
cd "$FORGELM_DEMO_DIR"
python3 -m forgelm --config config.yaml --dry-run --output-format json
python3 -m forgelm audit tiny.jsonl --output audit --output-format json
python3 -m forgelm verify-audit recorded-output/audit_log.jsonl
```

Kaydedilen ortamda üç komut da `0` çıkış koduyla tamamlandı. Kurulumdan sonra sırasıyla yaklaşık 0,18, 0,54 ve
0,14 saniye sürdüler; bunlar tek bir makinedeki gözlemlerdir, performans taahhüdü değildir.
Dry-run, modeli yüklemeden veya ağırlıklarını indirmeden YAML'ı doğrular. Audit yerel algılayıcıları çalıştırır;
`verify-audit` örnekteki logu ve yan dosyasını okur. Bu kontroller model eğitmez.

## Kaydedilen dosyalar

| Dosya | İçerik |
|---|---|
| [config.yaml](config.yaml) | Kaynak eğitimde ve kaydedilmiş dry-run'da kullanılan yapılandırmanın aynısı |
| [tiny.jsonl](tiny.jsonl) | Özgün olarak yazılmış sekiz sentetik soru-cevap satırı |
| [dry-run.json](dry-run.json) | İlk komutun değiştirilmemiş stdout çıktısı |
| [audit-summary.json](audit-summary.json) | İkinci komutun değiştirilmemiş JSON stdout çıktısı |
| [verify-audit.txt](verify-audit.txt) | Üçüncü komutun değiştirilmemiş stdout çıktısı |
| [terminal.txt](terminal.txt) | Üçüncü komutun kabuk izini de içeren `bash -ex` kaydı |
| [audit_log.jsonl](recorded-output/audit_log.jsonl) | Kaynak CPU eğitiminden alınan özgün yedi kayıtlı log |
| [audit_log.jsonl.manifest.json](recorded-output/audit_log.jsonl.manifest.json) | Logun yanında tutulan özgün genesis yan dosyası |

Projenin README dosyası bu terminal kaydını aynen gösterir; web sitesi bu rehbere bağlantı verir:

```text
+ python3 -m forgelm verify-audit recorded-output/audit_log.jsonl
OK: 7 entries verified
```

JSON audit özeti örneğe dahildir; üretilen tam rapor, mutlak yerel kaynak yolu içerdiği için dahil edilmemiştir.
Audit'i yeniden çalıştırmak tam raporu geçici dizinde oluşturur. Yolları kaldırmak amacıyla hiçbir üretilmiş çıktı
düzenlenmedi; model ağırlıkları dağıtılmıyor.

## Kaydın kaynağı

Kayıt **11 Ekim 2026** tarihinde, ForgeLM **0.11.1rc1**, `61d95a9` çalışma kopyası (çalışma zamanı kaynak kodu
`4917e7a`), Python 3.13.14, PyTorch 2.12.1, Transformers 5.13.0 ve TRL 1.7.1 ile CUDA GPU olmadan CPU'da alındı.
Log, Hub revizyonu `5f91d94bd9cd7190a9f3216ff93cd1dd95f2c7be` olarak sabitlenen `sshleifer/tiny-gpt2` modeliyle
gerçekleştirilen iki adımlı gerçek SFT eğitiminden geldi. Örneğin çalışma dizininde çalıştırılan kaynak komut:

```bash
FORGELM_OPERATOR=cpu-demo python3 -m forgelm --config config.yaml --output-format json
```

Bu eğitim komutu `0` çıkış koduyla tamamlandı ve adaptör ağırlıklarını değiştirdi. Küçük bir model indirmesi
gerektirdi; yukarıdaki üç kontrol bunu gerektirmez. Eğitim komutu burada kaynağı belgelemek için yer alır;
uçtan uca model tesliminin doğrulanmış örneği değildir. Model paketi ve uyumluluk raporları örneğe dahil değildir.

## Kanıtın sınırları

- Küçük sentetik veri kümesi ve eğitim metrikleri model kalitesi, güvenliği veya hukuki uyumluluk kanıtı değildir.
- Audit özeti PII veya sır saptamadığını bildirir; regex algılayıcıları hassas içeriği kaçırabilir.
  Kalite puanı veri kümesi sezgisel kontrollerini anlatır, model performansını değil.
- `verify-audit` bu anahtarsız logun zincirini ve genesis yan dosyasını kontrol eder. Yazarın kimliğini doğrulamaz,
  olayların anlamını denetlemez veya model dosyalarını doğrulamaz. Audit sırrı kullanılmadı.
- Kaynak eğitim `model.integrity_verified` olayını kaydetti; ancak son paketin ayrı `verify-integrity` kontrolü
  `6` çıkış koduyla bitti: `deployer_instructions.md`, bütünlük manifestinden sonra yazıldı.
  Örnek bu olayı değiştirmeden korur. Başarılı log kontrolü, başarılı model kontrolü olarak yorumlanmamalıdır.
  Bu kayıt [#370](https://github.com/HodeTech/ForgeLM/issues/370) için yapılan sonlandırma sırası düzeltmesinden öncedir:
  düzeltilmiş sonlandırma, yönergeleri manifestten önce yazar. Kaydedilen `6` çıkış kodu tarihsel bir sonuçtur.
- Kaynak eğitim, eksik veri audit raporu için `compliance.governance_section_missing` olayını da kaydetti.
  Burada gösterilen sonraki bağımsız audit, o eğitim çalışmasını geriye dönük değiştirmez.
- Bu ortamda `--output-format json` ile eğitim, JSON zarfından önce kütüphane çıktısı da üretti.
  Bu nedenle kaynak eğitim stdout'u makine tarafından okunabilir sonuç olarak dahil edilmedi.

## İlgili belgeler

- [Hızlı Başlangıç](../quickstart-tr.md) — eğitim kurulumu ve donanım gereksinimleri.
- [Veri Denetimi](../data_audit-tr.md) — algılayıcıların davranışı ve denetim kapıları.
- [Audit doğrulaması](../../reference/verify_audit-tr.md) — zincir, yan dosya ve HMAC sınırları.
- [Model bütünlüğü doğrulaması](../../reference/verify_integrity_subcommand-tr.md) — ayrı model dosyası kontrolü.
