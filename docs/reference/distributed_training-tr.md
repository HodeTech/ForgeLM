# Dağıtık Eğitim Rehberi (Çoklu GPU)

ForgeLM, **DeepSpeed** ve **PyTorch FSDP** aracılığıyla çoklu GPU dağıtık eğitimi destekler. Bu sayede daha büyük modeller (30B+ parametre) eğitilebilir veya eğitim birden fazla GPU'da önemli ölçüde hızlandırılabilir.

## Ön Koşullar

```bash
# DeepSpeed
pip install forgelm[distributed]

# FSDP, PyTorch'un içinde yerleşiktir — ek kurulum gerekmez
```

## Hızlı Başlangıç

### DeepSpeed ZeRO-2 (Önerilen Başlangıç Noktası)

```yaml
# my_config.yaml
distributed:
  strategy: "deepspeed"
  deepspeed_config: "zero2"
```

`torchrun` ile başlatma:
```bash
torchrun --nproc_per_node=4 -m forgelm.cli --config my_config.yaml
```

Veya `accelerate` ile:
```bash
accelerate launch --num_processes=4 -m forgelm.cli --config my_config.yaml
```

### FSDP Full Shard

```yaml
distributed:
  strategy: "fsdp"
  fsdp_strategy: "full_shard"
  fsdp_auto_wrap: true
```

```bash
torchrun --nproc_per_node=4 -m forgelm.cli --config my_config.yaml
```

---

## DeepSpeed Yapılandırması

Depo, `configs/deepspeed/` altında üç DeepSpeed ön ayarı barındırır. `deepspeed_config` parametresine bunların adlarından birini veya kendi JSON dosyanızın yolunu verin.

**Ön ayar adları yalnızca bir kaynak checkout'unda çalışır.** Kurulu paketin yanındaki ya da geçerli dizindeki
`configs/deepspeed/<ad>.json` dosyasına çözülürler. `pip install forgelm` wheel'i ve Docker imajı bu dosyaları içermez;
orada bir ön ayar adı yapılandırma hatasıyla (exit 1) başarısız olur. Deponun `configs/deepspeed/` klasöründeki JSON
dosyalarını kopyalayın ya da kendinizinkini yazın ve `deepspeed_config`'i onun yoluna ayarlayın.

**Bilinen sorun ([#410](https://github.com/HodeTech/ForgeLM/issues/410)):** `deepspeed_config` ayarlanmadığında da
kullanılan `zero2` ön ayarı `allgather_bucket_size` değerini `"auto"` yapar; transformers'ın DeepSpeed entegrasyonu bu
değeri doldurmaz, bu yüzden onunla bir ZeRO-2 koşusu durur. Düzelene kadar kendi ZeRO-2 JSON'unuzu kullanın.

### Ön Ayarlar (Presets)

| Ön Ayar | ZeRO Aşaması | Offload | En Uygun |
|---------|-------------|---------|----------|
| `zero2` | 2 | Hayır | 2-4 GPU'da 7B-13B modeller |
| `zero3` | 3 | Hayır | 13B-30B modeller, parametrelerin GPU'lar arasında bölünmesi |
| `zero3_offload` | 3 | optimizer state + parametreler → CPU | 30B-70B modeller, sınırlı VRAM, yeterli CPU RAM |

### Kullanım

```yaml
distributed:
  strategy: "deepspeed"
  deepspeed_config: "./my_ds_config.json"  # Kendi JSON'unuzun yolu (her kurulumda çalışır)
  # deepspeed_config: "zero2"       # Ön ayar adı: yalnızca kaynak checkout'u
```

### ZeRO Aşama Karşılaştırması

**ZeRO-2** (Optimizer + Gradient Bölümleme):
- Optimizer durumlarını ve gradyanları GPU'lar arasında böler
- Her GPU, model parametrelerinin tam bir kopyasını tutar
- Bellek tasarrufu ve iletişim yükü arasında iyi denge
- QLoRA (4-bit) ile iyi çalışır

**ZeRO-3** (Tam Parametre Bölümleme):
- Her şeyi böler: optimizer, gradyanlar VE parametreler
- Hiçbir GPU'nun tam modeli tutması gerekmez
- En yüksek bellek tasarrufu — çok daha büyük modeller eğitilebilir
- Daha yüksek iletişim yükü
- **Uyarı**: QLoRA (4-bit kuantizasyon) ile bilinen uyumluluk sorunları var

**ZeRO-3 + CPU Offload**:
- ZeRO-3 ile aynı, artı optimizer ve parametreleri CPU RAM'e aktarır
- Eğitim hızı pahasına GPU bellek tasarrufunu maksimize eder
- GPU VRAM darboğaz olduğunda ama CPU RAM yeterli olduğunda faydalı

### Custom DeepSpeed Config

Kendi JSON dosyanızı oluşturun.  HuggingFace Trainer'ın `"auto"`
değerleri ForgeLM YAML config'inizden çözmesini istiyorsanız o şekilde
bırakın:

```json
{
  "zero_optimization": {
    "stage": 2,
    "overlap_comm": true,
    "contiguous_gradients": true
  },
  "train_batch_size": "auto",
  "train_micro_batch_size_per_gpu": "auto",
  "gradient_accumulation_steps": "auto",
  "gradient_clipping": "auto"
}
```

---

## FSDP Yapılandırması

PyTorch Fully Sharded Data Parallel (FSDP), DeepSpeed'e alternatif olarak PyTorch'un içinde yerleşik bir çözümdür.

### Stratejiler

| Strateji | Açıklama | Bellek Tasarrufu |
|----------|----------|-----------------|
| `full_shard` | Parametreleri, gradyanları ve optimizer durumlarını böl | En Yüksek |
| `shard_grad_op` | Yalnızca gradyanları ve optimizer durumlarını böl | Orta |
| `hybrid_shard` | Düğüm içi tam bölme, düğümler arası çoğaltma | Çoklu Düğüm |
| `no_shard` | Standart DDP (bölme yok) | Yok |

### Kullanım

```yaml
distributed:
  strategy: "fsdp"
  fsdp_strategy: "full_shard"
  fsdp_auto_wrap: true                    # Auto-wrap transformer layers
  fsdp_offload: false                     # Offload parameters AND gradients (between forward/backward) to CPU
  fsdp_backward_prefetch: "backward_pre"  # Prefetch strategy
  fsdp_state_dict_type: "FULL_STATE_DICT" # State dict handling
```

### FSDP'yi DeepSpeed yerine ne zaman seçmeli

- **FSDP**: Native PyTorch, ekstra bağımlılık yok, daha basit kurulum,
  çoğu use-case için iyi.
- **DeepSpeed**: Daha çok özellik (ZeRO-Infinity, NVMe offload), çok
  büyük modeller için daha iyi optimizasyon, 70B+ parametre eğitiminde
  daha çok savaş tecrübesi.

---

## Uyumluluk Notları

### QLoRA + Dağıtık

| Kombinasyon | Durum | Notlar |
|------------|-------|--------|
| QLoRA + ZeRO-2 | Çalışır | Çoklu GPU QLoRA için önerilir |
| QLoRA + ZeRO-3 | Kararsız | bitsandbytes + parametre bölümleme ile bilinen sorunlar |
| QLoRA + FSDP full_shard | Deneysel | Özel PEFT/FSDP entegrasyon bayrakları gerekebilir |
| QLoRA + FSDP shard_grad_op | Çalışır | ZeRO-2 seviyesi bölümlemeye benzer |

### Arka Uç Uyumluluğu

| Arka Uç | Çoklu GPU | Notlar |
|---------|-----------|--------|
| `transformers` | Evet | Tam DeepSpeed ve FSDP desteği |
| `unsloth` | Hayır | Yalnızca tek GPU — dağıtık config ayarlanırsa ForgeLM uyarı verir |

### LoRA + Dağıtık

LoRA/DoRA adapter'ları hem DeepSpeed hem FSDP ile iyi çalışır.
Adapter'lar bölümlenmenin overhead'ini önemsiz kılacak kadar küçükken,
dondurulmuş base-model parametreleri bölümlenmeden ciddi yarar görür.

---

## Çok Düğümlü Eğitim

Birden fazla makine üzerinde eğitim için `torchrun`'ı düğüm
yapılandırmasıyla kullanın:

```bash
# Düğüm 0 (master)
torchrun \
  --nproc_per_node=4 \
  --nnodes=2 \
  --node_rank=0 \
  --master_addr=192.168.1.100 \
  --master_port=29500 \
  -m forgelm.cli --config my_config.yaml

# Düğüm 1
torchrun \
  --nproc_per_node=4 \
  --nnodes=2 \
  --node_rank=1 \
  --master_addr=192.168.1.100 \
  --master_port=29500 \
  -m forgelm.cli --config my_config.yaml
```

---

## Docker + Çoklu GPU

```bash
docker run --gpus all \
  -v $(pwd)/my_config.yaml:/workspace/config.yaml \
  --shm-size=16g \
  forgelm:full \
  torchrun --nproc_per_node=4 -m forgelm.cli --config /workspace/config.yaml
```

> **Not**: `--shm-size=16g` çoklu GPU eğitimi için önemlidir. PyTorch, süreçler arası iletişim için paylaşılan bellek kullanır ve Docker'ın varsayılan paylaşılan belleği (64MB) yetersizdir.

---

## Sorun Giderme

### "CUDA out of memory"
- ZeRO-3 veya CPU offload'lu ZeRO-3 deneyin
- `per_device_train_batch_size` değerini düşürün
- Efektif batch boyutunu korumak için `gradient_accumulation_steps` değerini artırın

### "NCCL error" veya "timeout"
- Tüm GPU'ların görünür olduğunu doğrulayın: `nvidia-smi`
- Tanı için `NCCL_DEBUG=INFO` ortam değişkenini kontrol edin
- Zaman aşımını artırın: `export NCCL_TIMEOUT=1800`

### "DeepSpeed not found"
```bash
pip install forgelm[distributed]
```

### ZeRO-3 ile yavaş eğitim
- ZeRO-3 daha yüksek iletişim yüküne sahiptir — bu beklenen bir durumdur
- Model GPU belleğine sığıyorsa ZeRO-2'yi değerlendirin
- DeepSpeed yapılandırmasında `overlap_comm: true` etkinleştirin (ön ayarlarda varsayılan)
