# Troubleshooting & SSS

ForgeLM kullanırken sık karşılaşılan sorunlar ve çözümleri.

---

## Kurulum sorunları

### `ModuleNotFoundError: No module named 'bitsandbytes'`

bitsandbytes (QLoRA) yalnız Linux'ta çalışır:

```bash
# Yalnızca Linux
pip install forgelm[qlora]

# macOS/Windows: 4-bit quantization'ı kapatın
# Config'inizde:
model:
  load_in_4bit: false
```

### `ModuleNotFoundError: No module named 'unsloth'`

Unsloth yalnız Linux'ta çalışır:

```bash
# Yalnızca Linux
pip install forgelm[unsloth]

# Diğer platformlar: transformers backend kullanın
model:
  backend: "transformers"
```

### `ImportError: lm-evaluation-harness is required`

```bash
pip install forgelm[eval]
```

---

## Eğitim sorunları

### CUDA Out of Memory (OOM)

**Çözümler (etki sırasına göre):**

1. **4-bit quantization'ı aç** (zaten kapalıysa):
   ```yaml
   model:
     load_in_4bit: true
   ```

2. **Otomatik OOM recovery'yi aç** (ForgeLM kademeli olarak küçülen
   batch size'larla yeniden dener):
   ```yaml
   training:
     per_device_train_batch_size: 8
     gradient_accumulation_steps: 2
     oom_recovery: true              # OOM'da batch'i otomatik yarıya indir
     oom_recovery_min_batch_size: 1  # batch_size=1'de dur
   ```
   Etkili batch size denemeler arasında korunur. Her deneme audit
   trail'e loglanır.

3. **Batch size'ı manuel azalt**:
   ```yaml
   training:
     per_device_train_batch_size: 1
     gradient_accumulation_steps: 8  # etkili batch size'ı koru
   ```

4. **Maks dizilim uzunluğunu azalt**:
   ```yaml
   model:
     max_length: 1024  # 2048'den indir
   ```

5. **Büyük modeller için DeepSpeed ZeRO-3 kullan**:
   ```yaml
   distributed:
     strategy: "deepspeed"
     deepspeed_config: "zero3_offload"
   ```

6. **LoRA rank'ı azalt**:
   ```yaml
   lora:
     r: 8  # 16'dan indir
   ```

### Eğitim Loss'u NaN ya da Inf

**Sebepler:**
- Learning rate çok yüksek
- Gradient accumulation olmadan batch size çok küçük
- Mixed precision sorunları

**Çözümler:**

```yaml
training:
  learning_rate: 1.0e-5  # 2e-5'ten azalt
  gradient_accumulation_steps: 4
```

Devam ederse `bf16: false` ve `fp16: false` olduğunu doğrulayın
(bunlar ForgeLM varsayılanlarıdır).

### Eğitim çok yavaş

1. **Unsloth kullan** (Linux, 2-5x hızlanma):
   ```yaml
   model:
     backend: "unsloth"
   ```

2. **Packing'i aç** (verileriniz destekliyorsa):
   ```yaml
   training:
     packing: true
   ```

3. **Birden çok GPU kullan**:
   ```yaml
   distributed:
     strategy: "deepspeed"
     deepspeed_config: "zero2"
   ```

### GaLore + Multi-GPU / Layerwise uyumsuzluğu

GaLore'un layerwise optimizer varyantı (`galore_adamw_layerwise`)
multi-GPU eğitimle (DeepSpeed/FSDP) **uyumlu değildir**. Bunun yerine
standart GaLore optimizer kullanın:

```yaml
training:
  galore_enabled: true
  galore_optim: "galore_adamw_8bit"  # "galore_adamw_layerwise" DEĞİL

# Layerwise GaLore'u dağıtık eğitimle BİRLEŞTİRMEYİN:
# distributed:
#   strategy: "deepspeed"  # layerwise GaLore ile başarısız olur
```

Hem multi-GPU hem GaLore'a ihtiyacınız varsa `galore_adamw` ya da
`galore_adamw_8bit` kullanın.

### Long-context eğitim VRAM sorunları

Long-context eğitimi (büyük `sliding_window_attention` ya da RoPE
ölçekleme) VRAM kullanımını ciddi şekilde artırır. Hafifletmek için:

1. **Sliding window boyutunu azalt**:
   ```yaml
   training:
     sliding_window_attention: 2048  # 4096'dan indir
   ```

2. **Gradient checkpointing'i aç** (hız pahasına VRAM azaltır):
   ```yaml
   training:
     gradient_checkpointing: true
   ```

3. **Sekans paketleme kullan** (padding israfını azalt):
   ```yaml
   training:
     packing: true
   ```

4. **Ek bellek tasarrufu için GaLore ile birleştir**:
   ```yaml
   training:
     galore_enabled: true
     galore_rank: 64  # düşük rank = daha az bellek
   ```

### Sentetik veri API zaman aşımı

Teacher model API'si `--generate-data` sırasında zaman aşımına
uğrarsa:

```yaml
synthetic:
  api_timeout: 120   # varsayılan 60 saniyeden artır
  api_delay: 1.0     # API çağrıları arası saniye (rate limiting; varsayılan 0.5)
  max_new_tokens: 512 # asılı kalıyorsa teacher yanıt boyutunu sınırla
```

`SyntheticConfig`, v0.5.5'te ayrı retry / batch ayarları yüzeylemiyor —
retry'lar HTTP-client katmanında ele alınır ve batch size API çağrısı
başına bir prompt'a sabittir. Phase 28+ backlog'u açık retry-count ve
batched-call parametreleri eklemeyi takip ediyor.

Yerel teacher modelleri için yeterli GPU belleği olduğundan emin olun.
Daha küçük bir teacher modeli kullanmayı ya da `synthetic.max_new_tokens`'ı
azaltmayı düşünün.

### `ValueError: Unknown trainer_type`

Geçerli trainer tipleri: `sft`, `orpo`, `dpo`, `simpo`, `kto`, `grpo`

```yaml
training:
  trainer_type: "sft"  # yazımı kontrol et
```

### `KeyError: Dataset must contain 'chosen' and 'rejected' columns`

Veri seti formatınız trainer tipiyle eşleşmiyor:

| Trainer | Gerekli sütunlar |
|---------|-----------------|
| `sft` | `User`/`instruction` + `Assistant`/`output`, ya da `messages` |
| `dpo`, `simpo`, `orpo` | `chosen` + `rejected` |
| `kto` | `completion` + `label` (boolean) |
| `grpo` | `prompt` |

---

## Konfigürasyon sorunları

### Eğitmeden config nasıl doğrulanır

```bash
forgelm --config my_config.yaml --dry-run
```

### Config doğrulama hatası: Field type uyumsuzluğu

ForgeLM doğrulama için Pydantic v2 kullanır. Hata mesajları tam
field'ı gösterir:

```text
Configuration validation failed: 1 validation error for ForgeConfig
training -> learning_rate
  Input should be a valid number [type=float_parsing, input_value='not_a_number']
```

YAML değerini beklenen tipe uyacak şekilde düzeltin.

### Config'te bilinmeyen alanlar (v0.3.1rc1+)

ForgeLM artık YAML config'lerinde **bilinmeyen alanları reddediyor** —
tüm sub-model'ler katı doğrulamayı (`extra="forbid"`) zorunlu kılıyor.
Typo'lar ya da desteklenmeyen alanlar net bir hata atar:

```text
ConfigError: Configuration validation failed: 1 validation error for ForgeConfig
training.lerning_rate
  Extra inputs are not permitted [type=extra_forbidden, input_value=2e-5]
```

Bu kasıtlıdır: sessiz typo'lar (örn. `learning_rate` yerine
`lerning_rate`) önceden eğitimi yanlış varsayılanlarla çalıştırırdı.
Şimdi net bir mesajla hızlı başarısız oluyorlar.

**Düzeltmek için:** Hata mesajındaki tam field path'ine bakın (örn.
`training.lerning_rate`) ve field adını düzeltin.

**Tüm geçerli alanları görmek için:** Çözümlenmiş tüm parametre
değerlerini listeleyen `forgelm --config job.yaml --dry-run` çalıştırın.

### Deprecated LoRA method söz dizimi

Boolean flag'lar `lora.use_dora` ve `lora.use_rslora` deprecated.
Bunun yerine `lora.method` kullanın:

```yaml
# Yeni (önerilen)
lora:
  method: "dora"      # ya da "rslora", "pissa", "lora"

# Deprecated (hâlâ çalışır, uyarı atar, otomatik normalize eder)
lora:
  use_dora: true      # deprecated — otomatik method: "dora" set'ler
  use_rslora: true    # deprecated — otomatik method: "rslora" set'ler
```

### `mix_ratio` doğrulama hatası

```text
ConfigError: mix_ratio values must be non-negative
ConfigError: mix_ratio values cannot all be zero
```

`mix_ratio` çoklu-veri eğitimi için sampling oranını kontrol eder.
Negatif olmayan değerler taşımalı ve hepsi sıfır olamaz:

```yaml
data:
  dataset_name_or_path: "org/primary-dataset"
  extra_datasets: ["org/secondary-dataset"]
  mix_ratio: [0.7, 0.3]  # %70 birincil, %30 ikincil
  # mix_ratio: [-0.5, 1.0]  # negatif değerler izin verilmez
  # mix_ratio: [0.0, 0.0]   # tümü sıfır izin verilmez
```

---

## Değerlendirme sorunları

### Auto-revert modelimi sürekli siliyor

Değerlendirme eşikleriniz çok katı olabilir:

```yaml
evaluation:
  auto_revert: true
  max_acceptable_loss: 2.0  # modeller geri alınmaya devam ederse artır
```

Ya da auto-revert'i kapat ve manuel kontrol et:

```yaml
evaluation:
  auto_revert: false
```

### Benchmark puanları sıfır

- `lm-eval` kurulu olduğunu kontrol et: `pip install forgelm[eval]`
- Benchmark görevlerinin geçerli olduğunu kontrol et: `arc_easy`,
  `hellaswag`, `mmlu` vs.
- Hızlı test için önce `limit: 10` ile dene:
  ```yaml
  evaluation:
    benchmark:
      enabled: true
      tasks: ["arc_easy"]
      limit: 10
  ```

---

## Multi-GPU sorunları

### NCCL hataları

```bash
# Timeout'u artır
export NCCL_TIMEOUT=1800

# NCCL debug
export NCCL_DEBUG=INFO

# GPU'lar görünür mü kontrol et
nvidia-smi
```

### DeepSpeed Config bulunamadı

```text
FileNotFoundError: DeepSpeed preset 'zero2' not found
```

ForgeLM proje kökünden çalıştırdığınızdan emin olun ya da mutlak yol
kullanın:

```yaml
distributed:
  deepspeed_config: "/path/to/ForgeLM/configs/deepspeed/zero2.json"
```

### QLoRA + ZeRO-3 sorunları

QLoRA (4-bit) ile DeepSpeed ZeRO-3'ün bilinen uyumluluk sorunları var.
Bunun yerine ZeRO-2 kullanın:

```yaml
distributed:
  strategy: "deepspeed"
  deepspeed_config: "zero2"  # zero3 değil
```

---

## Docker sorunları

### Docker'da GPU tespit edilmiyor

```bash
# NVIDIA Container Toolkit'in kurulu olduğundan emin ol
nvidia-smi  # host'ta çalışmalı

# GPU desteği ile çalıştır
docker run --gpus all forgelm --version
```

### Multi-GPU Docker'da paylaşılan bellek hatası

```bash
docker run --gpus all --shm-size=16g ...
```

---

## Exit kodları

| Kod | Anlam | Eylem |
|------|---------|--------|
| `0` | Başarı | Model hazır |
| `1` | Config hatası | YAML'inizi düzeltin |
| `2` | Eğitim hatası | GPU, bellek, bağımlılıkları kontrol edin |
| `3` | Değerlendirme arızası | Model kalitesi eşiğin altında — eşikleri ayarlayın ya da veriyi iyileştirin |
| `4` | Onay bekleniyor | İnsan incelemesi gerekli — staging dizinini incelemek için `forgelm approvals --show <run_id> --output-dir <dir>` çalıştırın, sonra promote için `forgelm approve <run_id> --output-dir <dir>` ya da kalıcı reject için `forgelm reject <run_id> --output-dir <dir>`. Staging yolu `<output_dir>/final_model.staging.<run_id>/`'dir. |

---

## Yardım alma

- **GitHub Issues**: [github.com/HodeTech/ForgeLM/issues](https://github.com/HodeTech/ForgeLM/issues)
- **Dry-run debug**: `forgelm --config job.yaml --dry-run --log-level DEBUG`
- **JSON tanılama**: `forgelm --config job.yaml --output-format json 2>error.log`
