# Autonomous Media Engine (mediaforge)

Beş kanalı kendi kendine yöneten, tamamen ücretsiz katmanlarla çalışan içerik sistemi. GitHub Actions üzerinde çalışır; yerel kurulum gerekmez.
Kurulum için önce **[SETUP.md](SETUP.md)**.

| Kanal (`forge/config.py`) | Platform | Konsept | Hedef |
|---|---|---|---|
| `ig_facts` | Instagram Reels | "Bunu biliyor muydun?" – bilim/doğa/uzay | günde 5 |
| `x_tech` | X | Yapay zekâ & teknoloji yorumları (RSS'ten beslenir) | günde ~12 |
| `yt_fun` | YouTube uzun | "Ya böyle olsaydı?" – eğlenceli, kaynaklı | günde 1 (7–10 dk) |
| `yt_fun_shorts` | YouTube Shorts | Aynı kanal, 30 sn'lik hikâyeler | günde 4 |
| `yt_doc` | YouTube uzun | "Dünya Nasıl Çalışıyor?" belgesel (en az 5 bağımsız kaynak) | haftada 2 (12–18 dk) |

## Akış
```
trend/RSS/Vikipedi/web araması → fırsat puanı → konu seçimi → çok kaynaklı araştırma (yetersizse konu reddedilir)
→ iddia çıkarma + doğrulama durumu → bölüm bölüm senaryo → seslendirme (edge-tts) → görsel (Pexels/AI/grafik)
→ FFmpeg render → kalite kapısı (özgünlük, sayı doğrulama, kaynak kapsamı, medya bütünlüğü, politika) → yayın → analitik → strateji
```

## Mimari
- `forge/llm.py` Gemini → Groq yedek zinciri · `tts.py` edge-tts → espeak-ng · `images.py` Pexels → Pollinations → gradyan
- `db.py` kanal başına SQLite (`state/<kanal>.db`, repoya commit edilir) · `budget.py` bütçe koruması · `quality.py` kalite kapısı
- `publish/` YouTube, Instagram, X (yalnızca stdlib) · `pipelines/` short_video, long_video, tweets
- `analytics.py` + `strategy.py` gerçek metriklerden öğrenme · `dashboard.py` salt-okunur panel (`docs/index.html`)
- `.github/workflows/` zamanlayıcı (cron) · `tools/gen_workflows.py` ile yeniden üretilir

## Testler
```
python -m unittest discover -s tests -v
```
FFmpeg/Pillow/ağ gerektirmez (mock mod). Video render'ı ve gerçek API'ler yalnızca GitHub Actions'ta çalışır.

## Dürüst durum tablosu
| Parça | Durum |
|---|---|
| Karar/araştırma/kalite/kuyruk/bütçe/DB/panel mantığı | Mock modda uçtan uca test edildi (25 test) |
| X OAuth 1.0a imzalama | Bilinen test vektörüyle doğrulandı |
| FFmpeg render, edge-tts, Pexels, Pollinations | **Henüz çalıştırılmadı** – ilk gerçek test Actions'ta "Üret ama yayınlama" ile yapılmalı |
| Gemini/Groq, YouTube, Instagram, X API çağrıları | **Henüz canlı denenmedi** (anahtar/hesap gerekir) |
| Panel | Statik HTML üretimi test edildi; tarayıcıda görsel kontrol yapılmadı |
| CTR/gösterim verisi | YouTube Analytics API sunmadığı için yok (Studio'da görülür) |
| Next.js/PostgreSQL/Redis/Docker | Bilerek kullanılmadı: ücretsiz ve kurulumsuz çalışma hedefi için SQLite + cron + statik panel |
