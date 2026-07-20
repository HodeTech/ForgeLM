# ForgeLM webhook şeması — Referans

> **Hedef kitle:** Webhook alıcısı yazanlar — Slack / Teams / Discord adaptörleri, Make.com / Zapier akışları, özel HTTP uç noktaları ve `event` alanını bir enum'a karşı doğrulayan her tüketici.
> **Ayna:** [webhook_schema.md](webhook_schema.md)

Bu doküman, `WebhookNotifier`'ın kablo üzerine ne koyduğunun kanonik ve eksiksiz tarifidir. Bir alıcı yazarının [`forgelm/webhook.py`](../../forgelm/webhook.py)'yi okumadan implementasyon yapabilmesi için yazıldı. Aşağıdaki her ifade bir tasarım notuna değil, sevk edilen notifier'a karşı doğrulandı.

Komşu iki doküman kasıtlı olarak örtüşür ve bilinçli olarak daha dardır: [`audit_event_catalog-tr.md`](audit_event_catalog-tr.md) her webhook olayını korelasyon için denetim günlüğü karşılığına eşler, [`logging-observability.md`](../standards/logging-observability.md) ise olay eklemenin katkıcıya dönük kurallarını verir (İngilizce). Üçü çeliştiğinde kod kazanır ve önce düzeltilecek dosya budur.

## Taşıma (transport)

Olay başına tek bir HTTP `POST`. Toplu gönderim, sarmalayıcı zarf veya olay-akışı çerçeveleme yoktur — istek gövdesinin *kendisi* aşağıda tarif edilen payload nesnesidir.

| Özellik | Değer |
|---|---|
| Metot | `POST` |
| `Content-Type` | `application/json` |
| Gövde | `json.dumps(payload)` — [Her zaman bulunan alanlar](#her-zaman-bulunan-alanlar) başlığındaki nesne |
| Hedef | `webhook.url`; `url` set değilse `os.getenv(webhook.url_env)` |
| Zaman aşımı | `webhook.timeout` (varsayılan `10` saniye), daha düşük değerler bir `WARNING` ile `1` saniye tabanına yükseltilir |
| TLS | certifi'ye karşı, `webhook.tls_ca_bundle` set ise ona karşı doğrulanır |
| Şema | `https://` önerilir. `http://` izinlidir ve bir `WARNING` üretir; düz metni sert bir ret hâline getirmek için `webhook.require_https: true` verin |
| SSRF politikası | Özel / loopback / link-local hedefler, `webhook.allow_private_destinations: true` olmadıkça reddedilir |

Her teslimat, SSRF çözümlemesi, yönlendirme politikası ve IP sabitlemesinin sahibi olan tek dışa-çıkış noktası `forgelm._http.safe_post` üzerinden geçer.

**Hiçbir şey gönderilmediği durum.** `webhook.url` ve `webhook.url_env`'in ikisi de bir değere çözümlenmezse notifier sessiz bir no-op'tur: POST yok, hata yok, WARNING ve üstünde log satırı yok. Bir alıcı, kabloyu izleyerek "operatör webhook'ları kapattı" ile "operatör env değişkeni adını yanlış yazdı" durumlarını ayırt edemez — bunu yalnızca eğitim koşusunun kendi logları gösterir.

## Olay sözlüğü

Tam olarak **sekiz webhook olayı** vardır. Bir alıcı, sevk edilmiş bir ForgeLM'den başka hiçbir olay beklememelidir.

| `event` | Ne zaman tetiklenir | Kapı (gate) | `status` | Attachment `color` |
|---|---|---|---|---|
| `training.start` | Tek aşamalı bir koşu `train()`'e girdi, model yüklenmeden önce. | `webhook.notify_on_start` | `started` | `#0052cc` |
| `training.success` | Koşu, revert olmadan ve bekleyen onay olmadan tamamlandı. | `webhook.notify_on_success` | `succeeded` | `#36a64f` |
| `training.failure` | Eğitimin kendisi hata fırlattı — OOM, veri seti hatası, yakalanmayan istisna. | `webhook.notify_on_failure` | `failed` | `#ff0000` |
| `training.reverted` | Eğitim başarılı oldu, ardından eğitim sonrası bir kapı (değerlendirme, güvenlik, hakem veya benchmark) koşuyu reddetti ve adaptörler silindi. | `webhook.notify_on_failure` | `reverted` | `#ff9900` |
| `approval.required` | Koşu başarılı oldu, `evaluation.require_human_approval: true` ve model inceleyici onayı için staging'de (EU AI Act Madde 14). | `webhook.notify_on_success` | `awaiting_approval` | `#f2c744` |
| `pipeline.started` | **Sıfırdan** başlayan çok aşamalı bir pipeline koşusu başlar, herhangi bir aşama çalışmadan önce. `--resume-from` ile emit edilmez — bkz. [Bu olayların emit edilmediği durumlar](#bu-olayların-emit-edilmediği-durumlar). | `webhook.notify_on_start` | `started` | `#0052cc` |
| `pipeline.completed` | Çok aşamalı bir pipeline koşusu `completed` ya da `stopped_at_stage` durumuna ulaşır. Koşu `gated_pending_approval` ile bittiğinde **emit edilmez** — bkz. [Bu olayların emit edilmediği durumlar](#bu-olayların-emit-edilmediği-durumlar). | `final_status == "completed"` ise `webhook.notify_on_success`, aksi hâlde `webhook.notify_on_failure` | `final_status`'a eşittir | başarıda `#36a64f`, aksi hâlde `#cc0000` |
| `pipeline.stage_reverted` | Bir aşama auto-revert olur; koşunun sonunda değil, tam o anda emit edilir. | `webhook.notify_on_failure` | `reverted` | `#ff9900` |

Alıcı yazarlarının genellikle yanlış anladığı dört nokta:

- **`training.success`, her kapının geçildiği anlamına gelmez.** `evaluation.auto_revert: true` ile *bu anlama gelir*. Ancak sevk edilen varsayılan `auto_revert: false` ile, bir kapı başarısız olup yalnızca kaydedildiğinde ve model yine de terfi ettiğinde *de* tetiklenir. Dashboard'unuz bu olayı "kalite doğrulandı" olarak yorumluyorsa, olay adı yerine `metrics`'i okuyun.
- **`approval.required` bir duraklamadır, başarısızlık değil.** `training.failure` üzerinde otomatik çağrı (page) yapan bir alıcı bunun üzerinde çağrı yapmamalıdır. Kasıtlı olarak `notify_on_success` ile kapılanır: başarı bildirimlerini susturmuş bir operatör, onay ping'lerini de istemez.
- **`pipeline.*` olayları, aşama başına `training.*` olaylarının *yerine* değil, *yanı sıra* emit edilir.** Her aşamanın `ForgeTrainer`'ı kendi yaşam döngüsü olaylarını fırlatmaya devam eder; dolayısıyla `training.failure` üzerinde filtreleyen mevcut bir dashboard, operatörü bir pipeline yapılandırmasına geçtiğinde değişiklik gerektirmeden çalışmayı sürdürür.
- **`pipeline.completed`, aynı tanımlayıcıya sahip bir denetim günlüğü olayıyla ad çakışması yaşar.** Bu bilinen bir wire/audit çakışmasıdır. Yalnızca ada göre değil, payload alan kümesine göre korele edin.

Webhook olayları `audit_log.jsonl`'a **eklenmez** — en-iyi-çaba (best-effort) bir yan kanal üzerinde giderler. Bir ping'i regülasyon kaydıyla ilişkilendirmek için `run_name` ve `event` adı üzerinden birleştirin.

**Kabloda zaman damgası yoktur.** Hiçbir payload zaman damgası taşımaz — hiçbir anahtar altında, hiçbir olayda. Bir zaman damgasına ihtiyacınız varsa alıcınızın kendi varış zamanını kullanın ve bunu yaklaşık kabul edin: teslimat en-iyi-çaba ve sırasızdır, dolayısıyla varış zamanı emit zamanı değildir. Zaman damgalı kayıt denetim günlüğüdür.

### Bu olayların emit edilmediği durumlar

`pipeline.started` ve `pipeline.completed`, koşuyu kuşatan koşulsuz bir çift gibi okunur; ancak orchestrator'da bunları atlayan iki deterministik yol vardır. İkisi birlikte tam olarak kurumsal onay-kapısı akışını oluşturur, dolayısıyla eksik bir `pipeline.completed`'ı arıza sayan bir alıcı, doğru çalışan kapılı bir pipeline'ı hatalı raporlar:

- **`--resume-from` ile başlatılan bir koşuda `pipeline.started` emit edilmez.** Olay yalnızca `resume_from is None` iken tetiklenir; dolayısıyla devam ettirilen bir pipeline kalan aşamalarını çalıştırıp, o süreçte kendisinden önce hiç `pipeline.started` gelmemişken `pipeline.completed` emit edebilir. İkisini bir aç/kapa çifti olarak eşleştirmeyin.
- **Koşu `gated_pending_approval` ile bittiğinde `pipeline.completed` emit edilmez.** Terminal olay yalnızca `final_status` `completed` / `stopped_at_stage` iken tetiklenir. İnsan onayı kapısıyla durdurulan bir pipeline tutarlı bir terminal durumdur, ama bu ikisinden biri değildir: `pipeline.stage_gated` **denetim** olayını emit eder ve durur. Buna karşılık gelen webhook sinyali, kapılayan aşamadan gelen `approval.required`'dır — alıcıya zincirin bir inceleyiciyi beklediğini söyleyen şey `pipeline.completed` değil, o ping'tir.

Her ikisi de aşağıdaki en-iyi-çaba teslimat kuralı 3'ün bir arıza biçimi değil bir tasarım özelliği olmasının sonucudur: **bir olayın yokluğu, o şeyin gerçekleşmediğinin kanıtı hiçbir zaman değildir.**

### Kararlılık sözleşmesi

**Sabitleyebileceğiniz şeyler:** sekiz `event` literali, her payload'da bulunan yedi zorunlu anahtar, `status` değer kümesi ve `color` literalleri. `event`, `status` ve attachment `color` bayt-bayt aynı kalmayı garanti eder — her biri notifier'ın kendi seçtiği kapalı bir kod literali kümesidir, hiçbir zaman operatör veya yapılandırma kaynaklı değildir; bu nedenle üzerinde güvenle yönlendirme yapılabilir.

**Büyümesine tolerans göstermeniz gerekenler:** olay kümesi (yeni adlar sona eklenir), olaya özgü alan kümesi ve `title`, `text`, `reason`, `run_name`, `model_path` alanlarının serbest metin içeriği. Olaya özgü her alanı isteğe bağlı sayın ve varlığını kontrol edin.

**Yeniden adlandırma yerine ekleme yapmak buradaki konvansiyondur ve kayıt bunu destekler:** hiçbir webhook olay adı yayımlanmış bir sürümde değişmedi. Geliştirme sırasında bir yeniden adlandırma yaşandı — `training.awaiting_approval`, `approval.required` oldu — fakat her iki commit de aynı fazın içinde indi ve ikisini de içeren ilk etiket `v0.5.5`'tir; dolayısıyla yayımlanmış hiçbir sürüm eski adı taşımadı. Olay eklemek bir `notify_*` metodu, eşleşen bir denetim olayı, bu tabloda bir satır ve testler gerektirir; aynı disiplin katkıcılar için [`logging-observability.md`](../standards/logging-observability.md)'da (İngilizce) tarif edilir.

**Enum doğrulayan alıcılar**: `event`'i bir listeye karşı sert doğruluyorsanız, listenizde sekiz adın tamamı bulunmalıdır. `v0.7.0` öncesi sözlüğe göre yazılmış bir alıcı yalnızca beşini bilir ve üç `pipeline.*` adını reddeder.

## Her zaman bulunan alanlar

Bu yedi anahtar **her** payload'da, her olayda, her seferinde bulunur. Null veya boş olduklarında bile bulunurlar; böylece bir tüketici koruma koymadan indeksleyebilir.

| Anahtar | Tip | Notlar |
|---|---|---|
| `event` | dize | Yukarıdaki sekiz addan biri. Asla maskelenmez. |
| `run_name` | dize | `training.*` ve `approval.required` için koşu kimliği; `pipeline.*` için **pipeline** koşu kimliği. Birincil korelasyon anahtarı. Serbest metin olarak maskelenir. |
| `status` | dize | Şunlardan biri: `started`, `succeeded`, `failed`, `reverted`, `awaiting_approval`, `completed`, `stopped_at_stage`. Asla maskelenmez. |
| `metrics` | nesne, dize → sayı | `training.success` dışındaki her olayda `{}`. Sayısal olmayan değerler gönderim öncesi ayıklanır. |
| `reason` | dize ya da null | Yalnızca `training.failure`, `training.reverted` ve `pipeline.stage_reverted` için null değildir. Önce maskelenir, sonra kırpılır. |
| `model_path` | dize ya da null | Yalnızca `approval.required` için null değildir. Bir dosya sistemi dizin yolu. Serbest metin olarak maskelenir. |
| `attachments` | tam olarak bir nesneden oluşan dizi | Slack uyumlu blok; tam olarak `title` (dize), `text` (dize), `color` (dize, `#rrggbb`) taşır. Veri değil, sunum. |

Pratikte önemli olan iki dipnot:

- **`metrics` filtresi boolean'ları geçirir.** Filtre, sayısal tip kontrolünü geçen değerleri kabul eder ve `True` / `False` bunu sağlar — bir boolean metrik, sayı olarak değil JSON `true` / `false` olarak varır. Sayısal olmayan diğer her şey (dizeler, null'lar, iç içe nesneler) sessizce düşürülür.
- **`attachments`, diğer alanlardan türetilemeyen hiçbir bilgi taşımaz.** Slack olmayan alıcılar tümüyle görmezden gelebilir.

## Olaya özgü alanlar

Yalnızca adı geçen olaylarda bulunurlar. Bu küme kapalıdır ve çalışma zamanında [`forgelm/webhook.py`](../../forgelm/webhook.py) içindeki `_ALLOWED_EXTRA_PAYLOAD_KEYS`'e karşı zorlanır.

| Anahtar | Tip | Bulunduğu olay | Anlamı |
|---|---|---|---|
| `stage_count` | int | `pipeline.started` | Zincirdeki aşama sayısı. |
| `final_status` | dize | `pipeline.completed` | Terminal pipeline durumu; üst düzey `status`'a eşittir. Gözlemlenen değerler: `completed`, `stopped_at_stage`. |
| `stopped_at` | dize ya da null | `pipeline.completed` | Duraklatan aşamanın adı; pipeline temiz bittiğinde `null`. Bu null anlamlıdır ve kasıtlı olarak filtrelenmeyip korunur. |
| `stage_name` | dize | `pipeline.stage_reverted` | Revert eden aşamanın adı. |

### Dışa çıkış allowlist'i

`_send(**extra)` serbest biçimli bir geçiş yolu değildir. Her ek anahtar kabloya ulaşmadan önce elenir:

1. **Anahtar elemesi.** `_ALLOWED_EXTRA_PAYLOAD_KEYS` dışındaki bir anahtar düşürülür ve anahtarı, olayı, güncel allowlist'i ve kaydedileceği sabiti adıyla anan bir `WARNING` loglanır. Asla iletilmez.
2. **Değer elemesi.** Allowlist'teki bir değer, bir JSON skaleri (`str` / `int` / `float` / `bool`) ya da `null` olmalıdır. Liste, sözlük veya rastgele bir nesne, serileştirme içinde `TypeError` fırlatıp başarıyla tamamlanmış bir koşuyu son adımında iptal etmesine izin verilmek yerine kendi `WARNING`'i ile düşürülür.
3. **Çakışma elemesi.** Her zaman bulunan bir alanla çakışan anahtar düşürülür; böylece temel zarf bir ek alan tarafından asla üzerine yazılamaz.

Hatalı bir alan bildirimi zayıflatır; asla iptal etmez ve asla hata fırlatmaz. Payload'ın geri kalanı yine sevk edilir.

**Bu, mevcut alıcılar için bir davranış değişikliği mi? Hayır.** Allowlist, sevk edilen `notify_*` metotlarının hâlihazırda geçirdiği anahtar kümesinin tam olarak kendisidir; dolayısıyla eskiden varan hiçbir alan varmayı bırakmaz ve hiçbir payload biçim değiştirmez. Değişiklik önleyicidir: `**extra`'yı, *gelecekteki* bir çağıranın kullanıcı veya yapılandırma kaynaklı metni kazara üçüncü taraf bir alıcıya aktarma yolu olmaktan çıkarır. Bir `notify_*` metoduna alan ekleyen katkıcılar bunu sabite kaydetmelidir; `tests/test_webhook.py` her notifier'ı çalıştırır ve ikisi ayrıştığında derlemeyi başarısız kılar.

## Örnek payload'lar

Aşağıdaki payload'ların tamamı elle yazılmadı, **sevk edilen notifier'dan kaydedildi**: her `notify_*` metodu, POST sınırına yerleştirilen kaydedici bir taşıma katmanıyla çalıştırıldı ve topladığı nesne birebir yeniden üretildi — anahtar sırası, kaçış dizileri, her zaman bulunan null'lar dâhil. Elle düzenlemek yerine aynı yolla yeniden üretin; aksi hâlde payload'ın ne olduğunun değil ne olması gerektiğinin tarifine geri kayarlar.

### `training.*` ailesi

`training.success` — `metrics`'i dolduran tek olay:

```json
{
  "event": "training.success",
  "run_name": "llama3-support-sft",
  "status": "succeeded",
  "metrics": {
    "eval_loss": 0.4231,
    "train_runtime": 1820.5
  },
  "reason": null,
  "model_path": null,
  "attachments": [
    {
      "title": "Training Succeeded: llama3-support-sft",
      "text": "The job completed successfully.\n\nMetrics:\n• eval_loss: 0.4231\n• train_runtime: 1820.5000",
      "color": "#36a64f"
    }
  ]
}
```

Attachment `text`'ine dikkat: **her** metrik listelenir ve her biri dört ondalık basamağa biçimlendirilir; dolayısıyla `train_runtime` metinde `1820.5000` olarak görünürken `metrics.train_runtime` JSON sayısı olarak `1820.5` kalır. Biçimlenmiş metin sunumdur; değerler için `metrics`'i okuyun.

`training.reverted` — aynı zarf, `reason` dolu, `metrics` boş:

```json
{
  "event": "training.reverted",
  "run_name": "llama3-support-sft",
  "status": "reverted",
  "metrics": {},
  "reason": "Safety gate failed: unsafe_rate 0.08 exceeds max_unsafe_rate 0.05",
  "model_path": null,
  "attachments": [
    {
      "title": "Training Reverted: llama3-support-sft",
      "text": "Auto-revert fired. Generated artifacts were deleted because a post-training gate (evaluation, safety, judge, or benchmark) rejected the run.\n\nReason: Safety gate failed: unsafe_rate 0.08 exceeds max_unsafe_rate 0.05",
      "color": "#ff9900"
    }
  ]
}
```

### `approval.required`

`model_path`'i dolduran tek olay. Değer, staging **dizinidir**; ağırlıklar, tokenizer dosyaları veya uyumluluk paketi içeriği yanında gitmez.

```json
{
  "event": "approval.required",
  "run_name": "llama3-support-sft",
  "status": "awaiting_approval",
  "metrics": {},
  "reason": null,
  "model_path": "./checkpoints/final_model.staging.llama3-support-sft",
  "attachments": [
    {
      "title": "Awaiting Human Approval: llama3-support-sft",
      "text": "Training completed; the model is staged at `./checkpoints/final_model.staging.llama3-support-sft` and awaiting reviewer sign-off.\nRun `forgelm approve <run_id>` to promote, or `forgelm reject <run_id>` to discard.",
      "color": "#f2c744"
    }
  ]
}
```

### `pipeline.*` ailesi

Temiz bitişte `pipeline.completed` — `stopped_at: null`'ın atlanmadığına, bulunduğuna dikkat edin:

```json
{
  "event": "pipeline.completed",
  "run_name": "align-chain-2026-07",
  "status": "completed",
  "metrics": {},
  "reason": null,
  "model_path": null,
  "attachments": [
    {
      "title": "Pipeline Succeeded: align-chain-2026-07",
      "text": "All stages completed successfully.",
      "color": "#36a64f"
    }
  ],
  "final_status": "completed",
  "stopped_at": null
}
```

Erken duruş yolunda aynı olay; `status` ve `final_status` ikisi de `stopped_at_stage`, `stopped_at` duraklatan aşamayı adlandırır ve attachment `color` değeri `#cc0000` olur.

`pipeline.stage_reverted` — hem `stage_name` hem `reason` taşıyan, neredeyse gerçek zamanlı revert sinyali:

```json
{
  "event": "pipeline.stage_reverted",
  "run_name": "align-chain-2026-07",
  "status": "reverted",
  "metrics": {},
  "reason": "Benchmark gate failed: hellaswag 0.41 below min_score 0.50",
  "model_path": null,
  "attachments": [
    {
      "title": "Pipeline Stage Reverted: align-chain-2026-07",
      "text": "Stage 'dpo-preference' triggered auto-revert; downstream stages will not run.\n\nReason: Benchmark gate failed: hellaswag 0.41 below min_score 0.50",
      "color": "#ff9900"
    }
  ],
  "stage_name": "dpo-preference"
}
```

`pipeline.started`, temel zarfın ötesinde yalnızca `stage_count` taşır:

```json
{
  "event": "pipeline.started",
  "run_name": "align-chain-2026-07",
  "status": "started",
  "metrics": {},
  "reason": null,
  "model_path": null,
  "attachments": [
    {
      "title": "Pipeline Started: align-chain-2026-07",
      "text": "Multi-stage training pipeline began with 3 stage(s).",
      "color": "#0052cc"
    }
  ],
  "stage_count": 3
}
```

## Gizli bilgi maskeleme

Maskeleme **payload genelindedir**; serileştirmeden hemen önce, tam olarak birleştirilmiş nesneye bir kez uygulanır. Her serbest metin dizesi `forgelm.data_audit.mask_secrets`'ten geçer: `run_name`, `reason`, `model_path`, allowlist'teki her dize ek alanı ve attachment `title` ile `text`. Maskelenen aralıklar `[REDACTED-SECRET]` literaline dönüşür; kapsam AWS / GitHub / Slack / OpenAI / Google anahtarları, JWT'ler, özel anahtar blokları ve Azure storage bağlantı dizeleridir.

| Garanti | Ayrıntı |
|---|---|
| Maskelemeden muaf | `event`, `status` ve attachment `color` — bayt-bayt aynı garanti edilir. Her biri notifier'ın kendi seçtiği kapalı bir kod literali kümesidir, hiçbir zaman operatör veya yapılandırma kaynaklı değildir; alıcılar üzerlerinde güvenle yönlendirme yapabilir. |
| Maskeleme kapsamı | Tek tek argümanlar değil, birleştirilmiş payload. |
| Maskeleyici yoksa | `forgelm.data_audit` ithal edilemezse, her serbest metin alanı ham gönderilmek yerine tümüyle `"[REDACTED — secrets masker unavailable]"` literaliyle değiştirilir. `event` ve `status` yine hayatta kalır, böylece ping korele edilebilir kalır. Alıcılar bu dizeyi herhangi bir serbest metin alanında tolere etmelidir. |
| Uzunluk sınırı | Yalnızca `reason` sınırlanır: 2048 karakter, kesildiğinde `"… (truncated)"` son eki. Diğer alanlar maskelenir ama kırpılmaz. |
| Webhook URL'leri | Hiçbir payload'da bulunmaz. Operatör loglarında `scheme://host`'a maskelenir — path, query ve userinfo atılır, çünkü Slack / Teams / Discord taşıyıcı token'ı orada tutar. Model kartlarından ve kalıcı uyumluluk manifestinden de dışlanırlar; manifest `url_env`'i tutar, `url`'i asla. |
| Model artefaktları | Hiçbir olayın payload'ı model ağırlıkları veya tokenizer baytları içermez. `state_dict`, `model.safetensors`, `pytorch_model.bin` ve `adapter_model` alan adları hiç geçmez. |

Maskelemenin her argümana değil birleştirilmiş payload'a uygulanması, bir örneği değil bir sızıntı sınıfını kapatır: `stage_name` eskiden kendi alanında maskeleniyor, ham değeri ise iki satır sonra attachment `text`'ine gömülüyordu; `run_name` de `title` ve `text` içinde aynı biçime sahipti. Aşama adları ve koşu adları operatör YAML'ından gelir, dolayısıyla ikisi de kabloya çıkan yapılandırma kaynaklı metindi.

## Alıcılar için teslimat semantiği

Alıcınızı bu özelliklere göre tasarlayın; hepsi, webhook teslimatının bir eğitim koşusunu asla başarısız kılmaması gereken en-iyi-çaba bir yan kanal olmasından çıkar:

1. **Yeniden deneme yok.** Başarısız bir teslimat tekrar denenmez. ForgeLM'in üstel geri çekilmeyle yeniden denediğini söyleyen her iddia eskimiştir.
2. **Sıra garantisi yok.** Varış sırasından diziliş çıkarımı yapmayın.
3. **Teslim makbuzu yok.** Bir olayın yokluğu, o şeyin gerçekleşmediğinin kanıtı değildir.
4. **Idempotent olun.** Aynı `run_name`'in yeniden koşulmasında bir kopyanın gelmesini engelleyen hiçbir şey yoktur.
5. **Hatalar yutulur.** Politika reddi, zaman aşımı, bağlantı hatası, her `requests.RequestException` ve eksik bir `requests-toolbelt`, her biri `WARNING` düzeyinde loglanır ve soğurulur.
6. **2xx olmayan yanıt gövdeleri atılır.** Yalnızca durum kodu loglanır; yanıt gövdesi kasıtlı olarak bastırılır, çünkü alıcılar payload'ı rutin biçimde geri yansıtır.
7. **Webhook trafiği denetim kaydı değildir.** Uzun vadeli geçmiş için ping'leri arşivlemek yerine, ekle-yalnız hash-zincirli kayıt olan koşunun `audit_log.jsonl` dosyasının anlık görüntüsünü alın. Konumu ve doğrulaması için bkz. [`verify_audit-tr.md`](verify_audit-tr.md).

## Bkz.

- [`audit_event_catalog-tr.md`](audit_event_catalog-tr.md) — webhook ↔ denetim günlüğü korelasyon tablosu ve tam Madde 12 olay kataloğu.
- [`configuration-tr.md`](configuration-tr.md) — `webhook:` yapılandırma bloğu: `url`, `url_env`, `timeout`, `notify_on_*`, `require_https`, `allow_private_destinations`, `tls_ca_bundle`.
- [`verify_annex_iv_subcommand-tr.md`](verify_annex_iv_subcommand-tr.md) — pipeline manifest doğrulayıcısı; `pipeline.*` denetim olayları burada belgelenen webhook olaylarından üçüyle ad paylaşır.
- [`logging-observability.md`](../standards/logging-observability.md) — webhook olayı eklemenin katkıcıya dönük kuralları (İngilizce).
- [`../guides/cicd_pipeline-tr.md`](../guides/cicd_pipeline-tr.md) — webhook bildirimlerini bir CI/CD kapısına bağlamak.
- [`forgelm/webhook.py`](../../forgelm/webhook.py) — bu dokümanın tarif ettiği implementasyon.
