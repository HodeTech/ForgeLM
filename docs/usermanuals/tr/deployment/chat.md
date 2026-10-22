---
title: Etkileşimli Chat
description: Akışlı REPL'de fine-tuned modelinizi sınayın.
---

# Etkileşimli Chat

`forgelm chat` herhangi bir checkpoint'e karşı akışlı REPL açar — yerel, birleştirilmiş veya LoRA-adapter. Fine-tuning'in istediğiniz modeli üretip üretmediğini doğrulamanın en hızlı yolu.

## Hızlı örnek

```shell
$ forgelm chat ./checkpoints/customer-support
ForgeLM Chat  —  type your message and press Enter
Streaming : on  |  Temperature : 0.7
Commands  : /help  /reset  /save  /temperature  /system  /exit
────────────────────────────────────────────────────────

> aboneliği nasıl iptal ederim?
Assistant: Aboneliğinizi Ayarlar → Faturalandırma → Aboneliği İptal Et
adımlarıyla iptal edebilirsiniz. Erişiminiz mevcut faturalama döneminin
sonuna kadar devam eder…

> /system Türk telekom için kibar bir müşteri-destek temsilcisisin.
[System prompt updated]

> tarifeyi nasıl yükseltirim?
Assistant: Tarifenizi Ayarlar → Plan → Yükselt menüsünden değiştirebilirsiniz...
```

CLI çıktısı yalnızca İngilizcedir; yukarıdaki banner ve köşeli parantezli mesajlar bu yüzden çevrilmemiştir. `rich` kuruluysa banner bir panel içinde çizilir. Yeni bir system prompt sonraki turlara uygulanır; o ana kadarki konuşmayı yeniden yazmaz.

## Slash komutları

| Komut | Yaptığı |
|---|---|
| `/reset` | Konuşma geçmişini temizle. |
| `/save [yol]` | Konuşmayı JSONL'a kaydet; yol verilmezse geçerli dizine `chat_history_<zaman damgası>.jsonl` yazar. |
| `/system [prompt]` | System prompt'u ayarla veya güncelle; argümansız çağrılırsa mevcut olanı gösterir. |
| `/temperature <değer>` | Sampling sıcaklığını ayarla: 0'ın üstünde ve 2.0'a kadar (`0` reddedilir). |
| `/help` veya `/?` | Bu listeyi göster. |
| `/exit`, `/quit` veya `Ctrl+D` | Çık. |

## Konfigürasyon

`forgelm chat` YAML config okumaz ve `chat:` bloğu yoktur: komut satırı flag'leri ve yukarıdaki slash komutlarıyla ayarlanır.

| Ayar | Flag | Varsayılan |
|---|---|---|
| Sohbetten önce birleştirilecek adapter | `--adapter DIR` (`--backend unsloth` ile desteklenmez) | yok |
| Başlangıç system prompt'u | `--system PROMPT` (sonra `/system` ile değiştirin) | yok |
| Sampling temperature | `--temperature` (sonra `/temperature` ile değiştirin) | `0.7` |
| Yanıt başına maksimum token | `--max-new-tokens` | `512` |
| Streaming çıktı | `--no-stream` kapatır | açık |
| Quantize yükleme (yalnızca CUDA + bitsandbytes; aksi halde yok sayılır) | `--load-in-4bit` (NF4) veya `--load-in-8bit`; `--backend unsloth` ile ikisi de verilirse 4-bit kazanır, hiçbiri verilmezse 4-bit varsayılandır | kapalı |
| Model backend'i | `--backend transformers` veya `--backend unsloth` | `transformers` |
| Modelle birlikte gelen kodu çalıştırma | `--trust-remote-code` | kapalı |

Top-p, top-k ve repetition penalty sabittir (0.9 / 50 / 1.1) ve ayarlanamaz. Chat her zaman sampling yapar: seed yoktur ve greedy mod yoktur. Oturumlar arası kalıcı bir history dosyası da yoktur; bir konuşmayı `/save` ile kaydedin. Komut satırı flag'i `0.0` sıcaklığı kabul eder, ancak sampling onu reddeder; bu yüzden her tur bir `[Generation error: …]` satırıyla başarısız olur. 0'ın üstünde bir değer kullanın.

## Checkpoint yükleme

`forgelm chat` kabul eder:

- LoRA adapter ağırlıklı dizin: `./checkpoints/run/`
- Birleştirilmiş checkpoint dizini: `./checkpoints/run/merged/`
- HuggingFace model ID: `Qwen/Qwen2.5-7B-Instruct`

GGUF dosyalarını `forgelm chat` yüklemez: komut yalnızca Hugging Face checkpoint'lerini yükler. Bir GGUF çıktısını denemek için onu Ollama veya llama.cpp'de çalıştırın (bkz. [Deploy Hedefleri](#/deployment/deploy-targets)).

LoRA checkpoint'lerinde base modeli pozisyonel argüman olarak verin ve adapter dizinini `--adapter` ile belirtin:

```shell
$ forgelm chat "Qwen/Qwen2.5-7B" --adapter ./checkpoints/run/
```

## Güvenlik routing'i

> Not: `forgelm chat` üzerinde yerleşik `--safety` bayrağı gelecekteki bir Pro CLI sürümü için planlanmıştır ([GitHub'daki Phase 13 yol haritası](https://github.com/HodeTech/ForgeLM/blob/main/docs/roadmap.md)). Mevcut sürüm chat REPL'inde her-tur güvenlik taramasını **desteklemez**: `forgelm chat`'in `--safety` bayrağı yoktur ve hiçbir YAML config okumaz (chat subcommand parser'ında doğrulanabilir, `forgelm/cli/_parser.py`). `evaluation.safety.enabled: true` config bloğu yalnızca eğitim pipeline'ının auto-revert güvenlik kapısı (`forgelm/trainer.py`) ve bağımsız `forgelm safety-eval` subcommand'ı tarafından okunur — chat REPL'i tarafından asla. Aşağıdaki snippet planlanan `--safety` UX'in önizlemesidir; bugün runnable bir komut DEĞİLDİR.

Planlanan `--safety` bayrağı altında (yalnız önizleme — yukarıdaki nota bakın) her prompt ve yanıt Llama Guard tarafından taranır:

```text
> [adversarial prompt]
[Llama Guard S2 (şiddet içermeyen suç) flagledi — reddediliyor]
Bunda yardım edemem. Soruyu farklı şekilde sormayı deneyin.
```

Deployment öncesi modeli jailbreak için sondalarken faydalı. Sıradan sohbette varsayılan kapalı.

## Multi-turn yönetimi

Konuşma geçmişi bir oturum içinde korunur. Model her prompt'ta system prompt'u ve son 50 soru-cevap çiftini alır; daha eski çiftler bellekte (ve `/save` dosyasında) kalır ama artık gönderilmez. Sınır token'ı değil, çiftleri sayar:

```text
> Türkiye'nin başkenti neresi?
Assistant: Ankara.

> nüfusu ne kadar?
Assistant: Yaklaşık 5.7 milyon, son tahminlere göre...
```

İkinci yanıt, ilk tur onunla birlikte gönderildiği için mümkündür ("nüfusu" Ankara'ya işaret ediyor). Sıfırdan başlamak için: `/reset`.

## Kaydetme ve oynatma

```shell
> /save sessions/qa-1.jsonl
[History saved → sessions/qa-1.jsonl (7 messages)]
```

Dosya, satır başına bir `{"role": ..., "content": ...}` mesajı içerir: önce (ayarlıysa) system prompt, ardından oturumun tüm kullanıcı ve asistan mesajları. Yazılan system prompt, konuşma sırasında değiştirmiş olsanız bile kaydettiğiniz andaki geçerli olandır. Kaydedilen bir dosyayı bir oturuma geri yükleyen bir komut yoktur (replay tooling gelecekteki bir Pro CLI sürümü için roadmap'tedir).

Oturumlar şu durumlar için faydalı:
- Test sırasında bulduğunuz bir bug'ı yeniden üretmek.
- İnteraktif keşiften benchmark prompt seti kurmak.
- Aynı konuşmada iki model sürümünü karşılaştırmak.

## İki modeli karşılaştırma

> Not: `chat-compare` subcommand'ı — judge hakemliğiyle interaktif, yan-yana chat-prompt karşılaştırması — gelecekteki bir Pro CLI sürümü için planlanmıştır ([GitHub'daki Phase 13 yol haritası](https://github.com/HodeTech/ForgeLM/blob/main/docs/roadmap.md)). Bugün eşdeğeri yoktur: `forgelm --benchmark-only` (bkz. `forgelm/cli/_no_train_modes.py`) her seferinde **tek** bir checkpoint üzerinde lm-evaluation-harness benchmark'ları çalıştırır — yan-yana karşılaştırma veya judge hakemliği yapmaz. İki checkpoint'i şimdi karşılaştırmak için `--benchmark-only`'yi checkpoint başına bir kez çalıştırın, judge'ı ayrıca koşturun ve sonuçları kendiniz birleştirin. Aşağıdaki snippet planlanan UX'i önizler.

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
**Chat'i yeniden üretilebilir saymak.** Chat her zaman sampling yapar; seed'i ve greedy modu yoktur (`/temperature 0` reddedilir), bu yüzden aynı prompt farklı yanıtlar verebilir. Keşif için kullanın; modelin gerçekte ne söylediğini `/save` dökümüyle saklayın ve tekrarlanabilir kontroller için değerlendirme komutlarını kullanın.
:::

:::warn
**Context'i aşan uzun konuşmalar.** Yalnızca son 50 çift gönderilir, ancak hiçbir şey token saymaz: uzun bir system prompt, uzun yanıtlar ya da küçük context'li bir model yine de taşabilir; tur o zaman bir `[Generation error: …]` satırıyla başarısız olur (tur geçmişe eklenmez). Uzun test oturumlarında periyodik `/reset` kullanın.
:::

:::tip
Birçok prompt'u otomatik sondalarken toplu güvenlik taraması için `forgelm safety-eval` kullanın ya da her prompt'u `forgelm chat`'e besleyen ve çıktıyı toplayan bir döngü yazın. Mevcut sürümde ayrı bir batch-chat subcommand'ı bulunmamaktadır.
:::

## Bkz.

- [LLM-as-Judge](#/evaluation/judge) — karşılaştırmayı otomatikleştir.
- [Llama Guard Güvenliği](#/evaluation/safety) — burada da aynı Llama Guard model kullanılır.
- [Deploy Hedefleri](#/deployment/deploy-targets) — memnun kalınca deploy.
