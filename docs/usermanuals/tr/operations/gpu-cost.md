---
title: GPU Maliyet Tahmini
description: 16 GPU profilinde otomatik tespit ve saatlik tarifenize göre koşu başı maliyet izleme.
---

# GPU Maliyet Tahmini

> **Durum:** GPU tespiti + koşu süresi + audit-log damgalama bugün
> gönderiliyor; konfigürasyon-tabanlı `cost_tracking:` bloğu (tarife
> tabloları, uyarı / durdurma eşikleri) **yol haritasında ve henüz
> uygulanmadı** — `forgelm/config.py`'de `output.cost_tracking` yüzeyi
> yok. Aşağıdaki `cost_tracking:` örnekleri ileriye dönük yer
> tutuculardır — YAML yüzeyi inene kadar saatlik tarifeleri manuel
> ayarlayın. Erteleme için bkz. [GitHub'daki risks-and-decisions yol haritası](https://github.com/HodeTech/ForgeLM/blob/main/docs/roadmap/risks-and-decisions.md).

ForgeLM, üzerinde koştuğunuz GPU'yu tespit eder, profilini (bellek, compute, tipik saatlik tarife) bulur ve koşu başı maliyeti izler. Her koşunun ardından audit log tam olarak ne kadar GPU zamanı kullanıldığını ve maliyetini kaydeder.

## Tespit nasıl çalışır

Başlangıçta `forgelm` şunları okur:
- Donanım tanımlama için `nvidia-smi --query-gpu=name,memory.total,...`.
- `forgelm/gpu_profiles.yaml`'dan eşleşen profil.

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
output:
  cost_tracking:
    enabled: true
    rate_per_hour:
      A100_80GB: 1.10              # saatlik USD
      A100_40GB: 0.85
      H100_80GB: 2.40
      RTX_4090: 0.50               # kabaca elektrik maliyeti
      default: 1.00                # eşleşmeyen GPU'lar için
    currency: "USD"
```

`cost_tracking:` yüzeyi indiğinde, bunu proje başına bir kez ayarlayın ve ForgeLM maliyet raporlama için eşleşen tarifeyi kullanacak. O zamana kadar blok ileriye dönük bir yer tutucudur (yukarıdaki durum bandına bakın).

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

Çoklu-GPU eğitiminde ForgeLM GPU başı tarifeyi GPU sayısıyla çarpar:

```yaml
output:
  cost_tracking:
    rate_per_hour:
      A100_80GB: 1.10
```

4×A100 koşusu 2 saat = 4 × 2 × $1.10 = $8.80; ZeRO veya FSDP kullanmak fark etmez.

## Maliyet uyarıları (yol haritası — henüz uygulanmadı)

Kontrolden çıkabilecek koşular için planlanan `cost_tracking` bloğu eşik
tabanlı uyarı + durdurma destekleyecek:

```yaml
# yol haritası yer tutucusu — forgelm/config.py tarafından henüz honure edilmez
output:
  cost_tracking:
    alert_threshold_usd: 50.0          # geçildiğinde webhook fırlat
    halt_threshold_usd: 200.0          # eğitim durur
```

İmplemente edildiğinde, uyarı konfigüre webhook'u fırlatır (bkz. [Webhook'lar](#/operations/webhooks)) — yanlış konfigüre koşunun gece bütçeyi haftalık bitirmesini CI'da yakalamak için faydalı. O zamana kadar maliyeti audit log + zamanlayıcınızdaki bütçe-tarafı koruyucu ile manuel izleyin.

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
- [CI/CD Hatları](#/operations/cicd) — CI'da maliyet uyarıları.
- [Konfigürasyon Referansı](#/reference/configuration) — tam `cost_tracking` bloğu.
