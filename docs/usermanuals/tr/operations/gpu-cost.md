---
title: GPU Maliyet Tahmini
description: GPU'yu tespit eder, koşuyu sizin saatlik tarifenizle ya da yerleşik bir tabloyla fiyatlandırır ve her koşunun maliyetini kaydeder.
---

# GPU Maliyet Tahmini

> **Durum:** GPU tespiti, eğitim süresi ve maliyet tahmini bugün her koşu için
> değerlendirme-kaybı ve benchmark kapılarını geçen her koşu için kaydedilir ve `training.gpu_cost_per_hour` tek maliyet ayarıdır.
> Konfigürasyon-tabanlı bir `cost_tracking:` bloğu (GPU başına tarife
> tabloları, alarm / durdurma eşikleri) **yol haritasında ve henüz
> uygulanmadı** — `forgelm/config.py`'de `output.cost_tracking` yüzeyi
> yok, bu yüzden bu sayfadaki hiçbir örnek onu kullanmaz. Erteleme için bkz.
> [GitHub'daki risks-and-decisions yol haritası](https://github.com/HodeTech/ForgeLM/blob/main/docs/roadmap/risks-and-decisions.md).

Bir eğitim koşusu değerlendirme-kaybı ve benchmark kapılarını geçtiğinde (birinde başarısız olan koşu maliyet toplanmadan döner) ForgeLM, ne kadar süre eğitildiğini, kaç GPU üzerinde koştuğunu ve bunun saatlik bir tarifeyle ne tuttuğunu kaydeder: tarifeyi siz verdiyseniz sizinki, vermediyseniz tespit edilen GPU için yerleşik bir fiyat kullanılır. Tahmin diğer koşu sonuçlarının yanına yazılır; fatura değildir ve hiçbir şeyi durdurmaz ya da uyarmaz.

## Tespit nasıl çalışır

Eğitimden sonra ForgeLM, PyTorch'tan ilk CUDA aygıtının adını (`torch.cuda.get_device_name(0)`), sürecin görebildiği CUDA aygıtı sayısını ve ilk aygıtta ayrılan tepe belleği ister. Eğitim süresini Hugging Face trainer'ının `train_runtime` değerinden alır. `nvidia-smi` çağırmaz. CUDA GPU yoksa GPU alanlarının hiçbiri kaydedilmez.

Tarife, ayarlıysa `training.gpu_cost_per_hour`'dan; değilse `forgelm/trainer.py` içindeki `_GPU_PRICING` fiyat tablosundan gelir ve PyTorch'un bildirdiği tam aygıt adıyla eşleştirilir:

| Sınıf | Tanınan adlar |
|---|---|
| Tesla | Tesla T4, Tesla P100, Tesla V100, Tesla K80 |
| NVIDIA data center | NVIDIA A10G, NVIDIA A100-SXM4-40GB, NVIDIA A100-SXM4-80GB, NVIDIA A100 80GB PCIe, NVIDIA H100 80GB HBM3, NVIDIA H100 SXM5 80GB, NVIDIA H200, NVIDIA L4, NVIDIA L40S, NVIDIA B200 |
| GeForce (kendi sunucunuz) | NVIDIA GeForce RTX 3090, NVIDIA GeForce RTX 4090 |

Tablodaki fiyatlar yaklaşık on-demand cloud ortalamalarıdır (GeForce kartlarında elektrik artı amortisman tahmini). Tabloda olmayan bir GPU için, örneğin `NVIDIA RTX A6000` ya da `NVIDIA H100 PCIe`, **uyarı verilmez**: `training.gpu_cost_per_hour` ayarlamadıysanız koşunun tahmini bir maliyeti olmaz.

## Saatlik tarife konfigürasyonu

```yaml
training:
  gpu_cost_per_hour: 1.10          # GPU başına saatlik USD
```

`training.gpu_cost_per_hour`'u gerçek tarifenize ayarlayın. Ayarlı değilse ForgeLM tespit edilen GPU için yerleşik fiyat tablosuna döner; koşunun `resource_usage` kaydı hangisinin kullanıldığını `cost_source` alanında yazar:

| `cost_source` | Anlamı |
|---|---|
| `user_config` | Tarife `training.gpu_cost_per_hour`'dan geldi (`0` kabul edilir ve sıfır maliyet kaydeder). |
| `auto_detected` | Aygıt adı bir tablo girdisiyle tam eşleşti. |
| `fuzzy_match` | Aygıt adı bir tablo girdisiyle kısmen eşleşti. Bu bir tahmindir: örneğin `NVIDIA L40`, `NVIDIA L40S` fiyatıyla hesaplanır. |

Hiçbir tarife belirlenemediğinde `cost_source`, `gpu_cost_per_hour_usd` ve `estimated_cost_usd` bulunmaz.

:::warn
**CUDA GPU'su olmayan makineler (CPU, Apple silicon).** Aygıt adı yoktur ve boş bir ad tablonun her girdisiyle kısmen eşleşir ve en uzun ad, `NVIDIA GeForce RTX 3090`, seçilir; bu yüzden koşu, `cost_source: fuzzy_match` ile yer tutucu bir `0.15` USD/saat tarifesiyle kaydedilir. Bunu gerçek bir maliyet saymayın; böyle makinelerde `training.gpu_cost_per_hour`'u açıkça ayarlayın (koşu size ücretsizse `0`).
:::

Koşu başına tek bir tarife vardır ve bu tarife GPU başınadır: GPU modeli başına tarife tabloları desteklenmez.

## Çıktı

Tahmin şu yerlerde görünür:

- **`compliance/compliance_report.json`**, `resource_usage` altında; koşunun `training.output_dir` dizininde, güvenlik ve judge kapılarını da geçen bir koşunun sonunda yazılır (geri alınan koşu rapor dışa aktarmaz). Aynı veri `annex_iv_metadata.json` içine `computational_resources` olarak kopyalanır.
- **`--output-format json` sonucu**: `resource_usage` ve üst düzey `estimated_cost_usd` olarak.
- **Konsol**: `Estimated training cost` satırı ve fiyat kaynağını belirten bir `GPU-hours` satırı (text modunda).
- **Koşu metrikleri**: her sayısal alan `resource/<alan>` olarak kopyalanır (örneğin `resource/estimated_cost_usd`). Kopyalar sonucun `metrics` alanında, başarı webhook'unda ve `pipeline.completed` audit olayının `metrics_summary` alanında görünür (koşu insan-onayı kapısında durduğunda olay yayımlanmaz). W&B, MLflow ya da TensorBoard'a gönderilmezler.

`gpu_cost_per_hour: 1.10` ile 1892 saniye eğitilen 4 GPU'lu bir koşu şunu kaydeder:

```json
{
  "resource_usage": {
    "gpu_model": "NVIDIA A100-SXM4-80GB",
    "peak_vram_gb": 41.7,
    "gpu_count": 4,
    "training_duration_seconds": 1892.0,
    "gpu_hours": 2.102,
    "cost_source": "user_config",
    "gpu_cost_per_hour_usd": 1.1,
    "estimated_cost_usd": 2.3124
  }
}
```

Model card maliyeti içermez.

## Uçuş öncesi maliyet tahmini

Uzun bir eğitim koşusuna başlamadan önce sert bir adım sınırıyla kısa bir kalibrasyon koşturun, `resource_usage` içindeki `gpu_hours`'u okuyun, tam adım sayısına ölçekleyin ve saatlik tarifenizle çarpın. Özel bir `--estimate-cost` flag'i tartışıldı ama ship edilmedi; resource-tracking yolu yalnızca gerçek değerleri kaydeder.

```yaml
# configs/calibration.yaml — gerçek config'iniz ve ek olarak:
training:
  max_steps: 20
  output_dir: /tmp/calib
```

```shell
$ forgelm --config configs/calibration.yaml
$ jq '.resource_usage.gpu_hours' /tmp/calib/compliance/compliance_report.json
0.034
$ python -c "print(round(0.034 * (3000 / 20) * 1.10, 2))"   # 3.000 adım, GPU-saat başına $1.10
5.61
```

Sonucu kaba bir rakam olarak değerlendirin. İlk adımlar ısınma içerir ve birkaç adımdaki dizi uzunluğu karışımı nadiren tüm veri setininkiyle örtüşür; ölçeklemeden önce kararlı bir adım süresi görecek kadar adım koşturun.

## Çoklu-GPU ve dağıtık

`gpu_hours`, eğitim süresinin sürecin görebildiği CUDA aygıtı sayısıyla çarpımıdır ve tek `training.gpu_cost_per_hour` tarifesi GPU başına uygulanır.

4×A100 koşusu 2 saat = 4 × 2 × $1.10 = $8.80; ZeRO veya FSDP kullanmak fark etmez.

İş, makinedeki GPU'ların bir kısmını kullanıyorsa sürecin görebileceği aygıtları sınırlayın (örneğin `CUDA_VISIBLE_DEVICES` ile); aksi halde tahmin, koşunun hiç kullanmadığı GPU'ları da sayar.

## Maliyet alarmları (yol haritası — henüz uygulanmadı)

Kontrolden çıkabilecek koşular için planlanan `cost_tracking` bloğu eşik
tabanlı alarmı (alarm eşiği aşılınca webhook tetiklenir) ve durdurmayı
(durdurma eşiğinde eğitim durur) destekleyecek. Böyle bir ayar bugün
`forgelm/config.py`'de yoktur.

İmplemente edildiğinde, alarm konfigüre webhook'u fırlatır (bkz. [Webhook'lar](#/operations/webhooks)) — yanlış konfigüre koşunun gece bütçeyi haftalık bitirmesini CI'da yakalamak için faydalı. O zamana kadar maliyeti kaydedilen `resource_usage` ve zamanlayıcınızdaki bütçe-tarafı koruyucu ile manuel izleyin.

## Özel GPU profilleri

:::warn
**Uygulanmadı.** ForgeLM'de `gpu_profiles` ayarı yoktur ve `gpu_profiles.local.yaml` okunmaz; aşağıdaki snippet yalnızca planlanan biçimdir ve onu içeren bir config reddedilir. Bugün tabloda olmayan bir GPU'yu fiyatlandırmanın yolu, her GPU için çalışan `training.gpu_cost_per_hour`'dur.
:::

Yerleşik tabloda olmayan bir GPU eklemek için (planlanan):

```yaml
gpu_profiles:
  custom:
    - name: "AcmeNVIDIA-XYZ"
      pattern: "AcmeNVIDIA-XYZ"        # aygıt adına karşı eşleştir
      memory_gb: 96
      compute_capability: 9.0
      tensor_cores: true
```

## Sık hatalar

:::warn
**Saatlik tarifeyi mutlak doğru saymak.** ForgeLM'in varsayılanları kaba ortalamalar — gerçek cloud faturanız instance tipi, bölge, spot vs on-demand ve indirimlerinize bağlı. Kendi gerçek tarifenizle override edin.
:::

:::warn
**Paylaşımlı GPU'lar.** Tahmin, görünen her GPU'daki eğitim süresinin tamamını faturalar; kesirli ya da paylaşımlı tahsis için bir ayar yoktur. GPU'yu başka işlerle paylaşıyorsanız rakamı kendiniz bölün.
:::

:::tip
**Maliyeti zaman içinde izleyin.** Her koşu maliyetini kaydeder; haftalar boyunca trend alın, sürünen aşımları yakalayın. Aynı eğitim süresinde 2× maliyet artışı genelde verinin büyüdüğü veya hyperparam'ların kaydığı işaretidir.
:::

## Bkz.

- [VRAM Fit-Check](#/operations/vram-fit-check) — maliyet tahmini ile birlikte koşar.
- [CI/CD Hatları](#/operations/cicd) — CI'da maliyet alarmları.
- [Konfigürasyon Referansı](#/reference/configuration) — `training.gpu_cost_per_hour` alanı.
