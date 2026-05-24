---
title: Etkileşimli Chat
description: Akışlı REPL'de güvenlik routing'i ile fine-tuned modelinizi sınayın.
---

# Etkileşimli Chat

`forgelm chat` herhangi bir checkpoint'e karşı akışlı REPL açar — yerel, birleştirilmiş veya LoRA-adapter. Fine-tuning'in istediğiniz modeli üretip üretmediğini doğrulamanın en hızlı yolu.

## Hızlı örnek

```shell
$ forgelm chat ./checkpoints/customer-support
ForgeLM 0.5.5 — checkpoints/customer-support ile sohbet
forgelm> aboneliği nasıl iptal ederim?
Aboneliğinizi Ayarlar → Faturalandırma → Aboneliği İptal Et adımlarıyla
iptal edebilirsiniz. Erişiminiz mevcut faturalama döneminin sonuna kadar
devam eder…

forgelm> /system Türk telekom için kibar bir müşteri-destek temsilcisisin.
[system prompt güncellendi]

forgelm> tarifeyi nasıl yükseltirim?
Tarifenizi Ayarlar → Plan → Yükselt menüsünden değiştirebilirsiniz...
```

## Slash komutları

| Komut | Yaptığı |
|---|---|
| `/reset` | Konuşma geçmişini temizle. |
| `/save <yol>` | Konuşmayı JSONL'a kaydet. |
| `/system <prompt>` | System prompt'u ayarla veya güncelle. |
| `/temperature <değer>` | Sampling sıcaklığını ayarla (0.0 - 2.0). |
| `/help` | Bu listeyi göster. |
| `/exit` veya `Ctrl+D` | Çık. |

## Konfigürasyon

```yaml
chat:
  default_temperature: 0.7
  default_top_p: 0.9
  default_max_tokens: 1024
  default_system_prompt: "Yardımsever bir asistansın."
  history_file: "~/.forgelm/chat-history"     # oturumlar arası kalıcı
```

## Checkpoint yükleme

`forgelm chat` kabul eder:

- LoRA adapter ağırlıklı dizin: `./checkpoints/run/`
- Birleştirilmiş checkpoint dizini: `./checkpoints/run/merged/`
- HuggingFace model ID: `Qwen/Qwen2.5-7B-Instruct`
- GGUF dosyası: `./model.gguf` (alta llama.cpp)

LoRA checkpoint'lerinde base modeli pozisyonel argüman olarak verin ve adapter dizinini `--adapter` ile belirtin:

```shell
$ forgelm chat "Qwen/Qwen2.5-7B" --adapter ./checkpoints/run/
```

## Güvenlik routing'i

> Not: `forgelm chat` üzerinde yerleşik `--safety` bayrağı v0.6.0+ Pro CLI için planlanmıştır ([GitHub'daki Phase 13 yol haritası](https://github.com/HodeTech/ForgeLM/blob/main/docs/roadmap.md)). Bugün her-tur tarama davranışı yalnızca **YAML pipeline** (`safety:` bloğu) üzerinden yayınlanmıştır — chat REPL'i bu config'i okur ve her prompt + yanıtı Llama Guard'a yönlendirir. Aşağıdaki bayraklı çağrı planlanan UX'in önizlemesi olup BUGÜN runnable DEĞİLDİR.

`safety: enabled: true` config'iyle her prompt ve yanıt Llama Guard tarafından taranır:

```text
forgelm> [adversarial prompt]
[Llama Guard S2 (şiddet içermeyen suç) flagledi — reddediliyor]
Bunda yardım edemem. Soruyu farklı şekilde sormayı deneyin.
```

Deployment öncesi modeli jailbreak için sondalarken faydalı. Sıradan sohbette varsayılan kapalı.

## Multi-turn yönetimi

Konuşma geçmişi bir oturum içinde korunur. Model tüm chat'i (veya `max_length`'a sığanı) görür:

```text
forgelm> Türkiye'nin başkenti neresi?
Ankara.

forgelm> nüfusu ne kadar?
[önceki bağlamı kullanır: "nüfusu" Ankara'ya işaret ediyor]
Yaklaşık 5.7 milyon, son tahminlere göre...
```

Sıfırdan başlamak için: `/reset`.

## Kaydetme ve oynatma

```shell
forgelm> /save sessions/qa-1.jsonl
[6 tur sessions/qa-1.jsonl'a kaydedildi]
```

Kaydedilen JSONL, yeni bir oturumun prompt history'sine geri
besleyerek tekrar oynatılabilir (replay tooling v0.6.0+ Pro CLI için
roadmap'tedir).

Oturumlar şu durumlar için faydalı:
- Test sırasında bulduğunuz bir bug'ı yeniden üretmek.
- İnteraktif keşiften benchmark prompt seti kurmak.
- Aynı konuşmada iki model sürümünü karşılaştırmak.

## İki modeli karşılaştırma

> Not: `chat-compare` subcommand'ı v0.6.0+ Pro CLI için planlanmıştır ([GitHub'daki Phase 13 yol haritası](https://github.com/HodeTech/ForgeLM/blob/main/docs/roadmap.md)). Bugün aynı karşılaştırma her checkpoint'e karşı `forgelm --benchmark-only` ve aşağı akış judge config'i ile koşturulur; aşağıdaki snippet planlanan özel UX'i önizler.

```shell
$ forgelm chat-compare ./checkpoints/v1 ./checkpoints/v2 --prompts data/probes.jsonl
                          v1 (yardımsever)  v2 (yardımsever)  judge kazanan
"Aboneliği iptal..."     ✓ "Ayarlar →"      ✓ "Ayarlar →"     berabere
"Şifre sıfırlama?"       ✓ "Yardım edebil." ✓ "'Şifremi unuttum'a tıkla"  v2
"İade politikası?"       ✗ belirsiz         ✓ spesifik         v2
                                                                ───────
v1 vs v2 win rate: 0.62 (sig p=0.04)
```

## Sık hatalar

:::warn
**`/temperature 0`'ı determinist saymak.** Yakın-determinist ama argmax sampling'deki eşitlik bozma hâlâ küçük varyans üretebilir. Tam yeniden üretilebilirlik için YAML'da `seed:` ayarlayın.
:::

:::warn
**Context'i aşan uzun konuşmalar.** Geçmiş `max_length`'i aşınca ForgeLM en eski mesajları düşürür. Konuşma süreklilik kaybedebilir. Uzun test oturumlarında periyodik `/reset` kullanın.
:::

:::tip
Birçok prompt'un otomatik sondalanması için interaktif REPL yerine `forgelm batch-chat --prompts data/probes.jsonl --output responses.jsonl` kullanın. Aynı model, manuel yazma yok.
:::

## Bkz.

- [LLM-as-Judge](#/evaluation/judge) — karşılaştırmayı otomatikleştir.
- [Llama Guard Güvenliği](#/evaluation/safety) — burada da aynı Llama Guard model kullanılır.
- [Deploy Hedefleri](#/deployment/deploy-targets) — memnun kalınca deploy.
