---
title: GPU Maliyet Tahmini
description: 16 GPU profilinde otomatik tespit ve saatlik tarifenize göre koşu başı maliyet izleme.
---

# GPU Maliyet Tahmini

> **Durum:** GPU tespiti + koşu süresi + audit-log damgalama bugün
> gönderiliyor ve `training.gpu_cost_per_hour` tek maliyet ayarıdır.
> Konfigürasyon-tabanlı bir `cost_tracking:` bloğu (GPU başına tarife
> tabloları, alarm / durdurma eşikleri) **yol haritasında ve henüz
> uygulanmadı** — `forgelm/config.py`'de `output.cost_tracking` yüzeyi
> yok, bu yüzden bu sayfadaki hiçbir örnek onu kullanmaz. Erteleme için bkz.
> [GitHub'daki risks-and-decisions yol haritası](https://github.com/HodeTech/ForgeLM/blob/main/docs/roadmap/risks-and-decisions.md).

ForgeLM, üzerinde koştuğunuz GPU'yu tespit eder, profilini (bellek, compute, tipik saatlik tarife) bulur ve koşu başı maliyeti izler. Her koşunun ardından audit log tam olarak ne kadar GPU zamanı kullanıldığını ve maliyetini kaydeder.

## Tespit nasıl çalışır

Başlangıçta `forgelm` şunları okur:
- Donanım tanımlama için `nvidia-smi --query-gpu=name,memory.total,...`.
- `forgelm/trainer.py` içindeki yerleşik GPU fiyat tablosundan eşleşen saatlik ücret.

Desteklenen GPU'lar:

| Sınıf | Modeller |
|---|---|
| Datacenter | A100 40 GB / 80 GB, H100 80 GB, H200, L40S |
| Workstation | RTX 6000 Ada, RTX A6000 |
| Consumer | RTX 4090, RTX 4080, RTX 3090, RTX 3080 |
| Sadece-cloud | T4, V100, A10G |
| Apple | M1/M2/M3 Max (CPU/MPS fallback) |

GPU'nuz tanınmazsa ForgeLM uyarı bırakır ve generic profile fallback yapar (maliyet tahmini yok ama eğitim çalışır).

## Saatlik tarife konfigürasyonu

```yaml
training:
  gpu_cost_per_hour: 1.10          # eğitim GPU'su için saatlik USD
```

`training.gpu_cost_per_hour`'u gerçek tarifenize ayarlayın. Ayarlı değilse ForgeLM tespit edilen GPU için yerleşik fiyat tablosuna döner; koşunun `resource_usage` kaydı hangisinin kullanıldığını yazar (`cost_source`: `user_config`; tam GPU adı eşleşmesi için `auto_detected`; kısmi ad eşleşmesi için `fuzzy_match`; tarife belirlenemediyse anahtar bulunmaz). CUDA GPU yoksa tespit edilen bir GPU da yoktur, bu yüzden `training.gpu_cost_per_hour`'u açıkça ayarlayın. Koşu başına tek bir tarife vardır: GPU modeli başına tarife tabloları desteklenmez.

## Çıktı

Her koşunun ardından audit log şunları kaydeder:

```json
{
  "event": "run_complete",
  "ts": "2026-04-29T14:33:10Z",
  "duration_seconds": 1892,
  "gpu_profile": "A100_80GB",
  "gpus_used": 1,
  "estimated_cost_usd": 0.578,
  "rate_per_hour": 1.10,
  "currency": "USD"
}
```

Model card atıf yapar:

```markdown
## Eğitim maliyeti

Bu model 1× A100 80GB üzerinde 31m 32s eğitildi,
konfigüre tarifeyle tahmini $0.58 USD.
```

## Uçuş öncesi maliyet tahmini

Uzun bir eğitim koşusuna başlamadan önce kısa bir kalibrasyon (1-2 adım, `training.max_steps: 2` ile) koşturun, koşum-başına `compliance_report.json`'dan ortaya çıkan `gpu_hours`'u yakalayın ve sağlayıcınızın saatlik tarifesiyle çarpın. (Özel bir `--estimate-cost` flag'i tartışıldı ama ship edilmedi; resource-tracking yolu yalnızca gerçek değerleri yayar.)

```shell
$ forgelm --config configs/calibration.yaml --output-dir /tmp/calib
$ jq '.resource_usage.gpu_hours' /tmp/calib/compliance_report.json
0.034
$ python -c "print(0.034 * (3 / 0.034) * 1.10)"   # 6h eğitim, $1.10/sa
$7.15
```

Kalibrasyon yaklaşımı genelde gerçeğin %20 içindedir.

## Çoklu-GPU ve dağıtık

Çoklu-GPU eğitiminde ForgeLM tek `training.gpu_cost_per_hour` tarifesini GPU sayısıyla çarpar (`gpu_hours`, eğitim süresi çarpı GPU sayısıdır).

4×A100 koşusu 2 saat = 4 × 2 × $1.10 = $8.80; ZeRO veya FSDP kullanmak fark etmez.

## Maliyet alarmları (yol haritası — henüz uygulanmadı)

Kontrolden çıkabilecek koşular için planlanan `cost_tracking` bloğu eşik
tabanlı alarmı (alarm eşiği aşılınca webhook tetiklenir) ve durdurmayı
(durdurma eşiğinde eğitim durur) destekleyecek. Böyle bir ayar bugün
`forgelm/config.py`'de yoktur.

İmplemente edildiğinde, alarm konfigüre webhook'u fırlatır (bkz. [Webhook'lar](#/operations/webhooks)) — yanlış konfigüre koşunun gece bütçeyi haftalık bitirmesini CI'da yakalamak için faydalı. O zamana kadar maliyeti audit log + zamanlayıcınızdaki bütçe-tarafı koruyucu ile manuel izleyin.

## Özel GPU profilleri

Varsayılan profilde olmayan GPU eklemek için:

```yaml
gpu_profiles:
  custom:
    - name: "AcmeNVIDIA-XYZ"
      pattern: "AcmeNVIDIA-XYZ"        # nvidia-smi name'ye karşı eşleştir
      memory_gb: 96
      compute_capability: 9.0
      tensor_cores: true
```

Bunu proje kökünüze `gpu_profiles.local.yaml` olarak bırakın; ForgeLM otomatik birleştirir.

## Sık hatalar

:::warn
**Saatlik tarifeyi mutlak doğru saymak.** ForgeLM'in varsayılanları makul ortalamalar — gerçek cloud faturanız instance tipi, bölge, spot vs on-demand ve indirimlerinize bağlı. Kendi gerçek tarifenizle override edin.
:::

:::warn
**Paylaşımlı GPU'larda multi-tenancy.** Birden çok iş bir GPU'yu paylaşıyorsa (eğitimde nadir, inference'ta yaygın), maliyet izleme bölmek yerine çarpıyor. Gerçek tahsisi belirtmek için `--gpus N` kullanın.
:::

:::tip
**Maliyeti zaman içinde izleyin.** Audit log her koşunun maliyetini içerir; haftalar boyunca trend alın, sürünen aşımları yakalayın. Aynı eğitim süresinde 2× maliyet artışı genelde verinin büyüdüğü veya hyperparam'ların kaydığı işaretidir.
:::

## Bkz.

- [VRAM Fit-Check](#/operations/vram-fit-check) — maliyet tahmini ile birlikte koşar.
- [CI/CD Hatları](#/operations/cicd) — CI'da maliyet alarmları.
- [Konfigürasyon Referansı](#/reference/configuration) — `training.gpu_cost_per_hour` alanı.
