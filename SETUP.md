# Kurulum Rehberi (bilgisayarına hiçbir şey kurmadan)

Her şey GitHub'ın ücretsiz bulut makinelerinde çalışır. Senin bilgisayarın yalnızca tarayıcı olarak kullanılır.
**Sırayla ilerle; her adımdan sonra durup kontrol edebilirsin. Sistem, sen `FORGE_LIVE=1` yapana kadar HİÇBİR ŞEY yayınlamaz.**

> ⚠️ **Şirket bilgisayarı uyarısı:** Bu hesapları (Instagram, X, YouTube, GitHub) şirket cihazı ve ağından açma/yönetme;
> kişisel telefonunu ve kendi internetini kullan. Şirket politikası bunu yasaklıyor olabilir.

---

## 0) Gerçekçi beklentiler
- **Para kazanma garanti değildir.** YouTube'da reklam geliri için 1.000 abone + 4.000 izlenme saati (veya Shorts için 10 milyon izlenme/90 gün)
  gerekir. YouTube, tamamen otomatik ve kalıplaşmış ("inauthentic") içerikleri ortaklığa kabul etmeyebilir; bu yüzden sistem
  özgünlük, kaynak doğrulama ve kalite kontrolüne ağırlık verir. İlk aylar gelir beklenmemeli.
- **Sahte takipçi/beğeni/otomatik yorum YOK.** Platformlar bunu cezalandırır. Büyüme; kalite, düzen ve öğrenen bir döngüyle gelir.
- Hesapları **sen** açacaksın (ben hesap açamam). Anahtarları GitHub'a **Secret** olarak gireceksin.

## 1) GitHub (ücretsiz politikayı aşmamak için)
1. Kişisel e-postanla **tek** bir GitHub hesabı aç (birden fazla ücretsiz hesapla limit aşmak şartlara aykırıdır).
2. **Yeni repo** oluştur: adı `mediaforge`, **Public**. Bu klasörün içeriğini repoya yükle (web arayüzünde *Add file → Upload files*).
   - Public repoda GitHub Actions dakikaları **sınırsızdır**. Private'ta ayda 2.000 dakika vardır ve bu sistem onu aşar.
   - Tokenlar/anahtarlar repoda değil, *Secrets* içinde durur. Yalnızca zaten yayınladığın içeriklerin kayıtları herkese açık olur.
3. **Ücret çıkmasın diye:** Hesap *Settings → Billing and plans → Budgets and alerts* → harcama limitini **$0** yap.
4. Repo *Settings → Actions → General* → "Allow all actions" aç; *Workflow permissions* → **Read and write permissions**.
5. *Settings → Pages* → Source: **Deploy from a branch**, Branch: `main`, klasör: `/docs`. Panelin adresi:
   `https://<kullanıcı-adın>.github.io/mediaforge/`
6. Not: 60 gün hiç repo etkinliği olmazsa zamanlanmış işler durur. Sistem her çalışmada durum commit'i attığı için bu olmaz.
7. **Kullanım şartı riski:** GitHub Actions'ın ek kullanım şartları, Actions'ın depodaki yazılım projesiyle ilgisiz işler için
   kullanılmasını kısıtlar. Bir medya üretim hattı gri alandır; makul kaynak kullan, kötüye kullanma. Kod her yerde çalışacak şekilde
   yazıldığı için gerekirse ücretsiz bir bulut sunucusuna (ör. Oracle Cloud Always Free) taşınabilir.

## 2) Ücretsiz API anahtarları (hepsi *Settings → Secrets and variables → Actions → New repository secret*)
| Secret | Nereden | Zorunlu mu |
|---|---|---|
| `GEMINI_API_KEY` | https://aistudio.google.com/apikey | **Evet** (LLM + web arama temellendirme) |
| `GROQ_API_KEY` | https://console.groq.com/keys | Önerilir (yedek LLM) |
| `PEXELS_API_KEY` | https://www.pexels.com/api/ | Önerilir (ücretsiz stok video/foto) |
| `YOUTUBE_API_KEY` | Google Cloud → API'ler → YouTube Data API v3 → Kimlik bilgileri → API anahtarı | İsteğe bağlı (rakip analizi) |

Ücretsiz katmanların günlük/dakikalık limitleri vardır; sistem istekleri yavaşlatır ve LLM yedeğine geçer. Limitler zamanla değişebilir.

## 3) Platform hesapları (hepsini kişisel telefon/ağından)

### 3a) X (Sistem 2)
1. Yeni X hesabı aç. 2. https://developer.x.com → ücretsiz geliştirici hesabı → Project + App.
3. App → *User authentication settings* → **Read and write**. 4. *Keys and tokens*: API Key/Secret ve **Access Token/Secret** (OAuth 1.0a) üret.
5. Secrets: `X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN`, `X_ACCESS_SECRET`.
- Ücretsiz katmanda aylık yazma limiti vardır (`forge/config.py → monthly_post_limit`, varsayılan 500). Günde 10–15 gönderi buna yakındır;
  sistem günlük çağrı bütçesini aylık limite göre otomatik ayarlar. Gerçek limit X'in güncel planına göre değişebilir, kontrol et.
- Ücretsiz katmanda okuma (metrik) çok kısıtlıdır; panelde X metrikleri boş/eksik kalabilir.

### 3b) Instagram (Sistem 1)
1. Yeni Instagram hesabı → **Professional (Creator veya Business)** yap.
2. https://developers.facebook.com → uygulama oluştur → **"Instagram API with Instagram login"** ürününü ekle.
3. Uygulama rollerinde Instagram hesabını **Instagram Tester** olarak ekle ve davet ettiğin hesaptan kabul et (kendi hesabın için uygulama incelemesi gerekmez).
4. Dashboard'dan **uzun ömürlü (60 gün) access token** üret. Secrets: `IG_ACCESS_TOKEN`, `IG_USER_ID` (hesap ID'si aynı ekranda görünür).
5. Token 60 günde dolar. Her ~50 günde Meta panelinden yeni uzun ömürlü token üretip `IG_ACCESS_TOKEN` Secret'ını güncelle
   (takvimine hatırlatıcı ekle). Süresi dolarsa *Sağlık kontrolü* ve panel hata gösterir. Token'ı Actions günlüklerine yazdırma (repo public).
- Meta arayüzü sık değişir; adım adları farklılaşabilir.

### 3c) YouTube (Sistem 3 ve 4) – iki ayrı kanal, iki ayrı Google Cloud projesi
**Neden iki proje?** YouTube API kotası proje başınadır (günde 10.000 birim; bir video yüklemesi ~1.600 birim). Eğlence kanalı (1 uzun + 4 Shorts/gün)
tek başına ~8.500 birim kullanır. Belgesel kanalı için ayrı proje açmak kotayı ayırır.
1. İki YouTube kanalı aç: **eğlence** ve **belgesel** (kanal başına bir marka hesabı önerilir).
2. https://console.cloud.google.com → proje oluştur (`forge-fun`) → *YouTube Data API v3* ve *YouTube Analytics API* etkinleştir.
3. *OAuth consent screen* → External → bilgileri doldur, kendi e-postanı **Test user** ekle. **Sonra mutlaka "Publish app / Uygulamayı yayınla" (In production) yap**;
   "Testing" durumunda refresh token **7 günde** geçersiz olur. (Doğrulanmamış uygulama uyarısı kendi kullanımın için sorun değildir.)
4. *Credentials → OAuth client ID*: tür **Desktop app**. `Client ID` ve `Client secret` değerlerini al.
5. **Refresh token al** (yöntem A veya B):
   - **A (kurulumsuz):** https://developers.google.com/oauthplayground → sağ üst dişli → *Use your own OAuth credentials* → client id/secret yaz.
     Scope: `https://www.googleapis.com/auth/youtube`, `.../yt-analytics.readonly`, `.../yt-analytics-monetary.readonly` → *Authorize APIs* → kanalı seç → *Exchange authorization code for tokens* → **Refresh token**.
     (Playground'un çalışması için Web türü istemciye `https://developers.google.com/oauthplayground` yönlendirme adresini eklemen gerekir; bu yüzden istemciyi **Web application** türünde oluştur.)
   - **B:** başka bir bilgisayarda `python tools/get_youtube_token.py CLIENT_ID CLIENT_SECRET` (hiçbir şey kurmaz, yalnızca Python gerekir).
6. Secrets (eğlence): `YT_FUN_CLIENT_ID`, `YT_FUN_CLIENT_SECRET`, `YT_FUN_REFRESH_TOKEN`. Belgesel için aynısı `YT_DOC_...` ile (ikinci proje).
7. ⚠️ **API denetimi (kritik):** Denetimden geçmemiş API projelerinde yüklenen videolar **özel (private) kilitlenir**.
   https://support.google.com/youtube/contact/yt_api_form formunu doldurup "YouTube API Services Audit" iste. Onaylanana kadar sistem videoları yükler
   ama herkese açık olmaz; sistem bunu logda `Video 'özel' kaldı` uyarısıyla bildirir. Gerekirse o süreçte videoları Studio'dan elle yayınlayabilirsin.
8. Ayrıca YouTube'da *Studio → Ayarlar → Kanal → Gelişmiş* bölümünden "çocuklara özel değil" varsayılanını ayarla; gerçekçi yapay zekâ içeriğinde sistem
   "değiştirilmiş/sentetik içerik" bildirimini otomatik işaretler.

## 4) Çalıştırma sırası (güvenli)
1. Secrets'ları gir. *Actions → Sağlık kontrolü → Run workflow*. Hangi anahtarın eksik/hatalı olduğunu listeler.
2. *Actions → (herhangi bir kanal) → Run workflow → "Üret ama yayınlama" işaretli*. Bittiğinde **Artifacts** bölümünden üretilen videoyu indirip izle (önizleme, 3 gün saklanır).
3. Kaliteden memnunsan *Settings → Secrets and variables → Actions → Variables* → `FORGE_LIVE` = `1` oluştur. Bundan sonra zamanlanmış işler gerçekten yayınlar.
4. İlk günler panelden (Pages adresi) ve *Actions* sekmesinden hataları izle.

## 5) Yönetim
- **Durdur/başlat, bütçe, sıklık:** `state/control.json` dosyasını GitHub'da düzenle (`"paused": true` tüm sistemi durdurur; kanal bazında `enabled`, `daily`, `weekly`, `budget_usd`).
- **Bütçe:** Varsayılan 0 $. Tüm sağlayıcılar ücretsizdir; ücretli bir şey eklenirse `budget.py` önce bütçeyi kontrol eder (%80 uyarı, %100 durdurur). Ücretli abonelik/ödeme asla otomatik başlatılmaz.
- **Zamanlama:** `tools/gen_workflows.py` içindeki cron değerlerini düzenleyip `python tools/gen_workflows.py` ile yeniden üret (UTC; Türkiye = UTC+3).
- **Hata davranışı:** bir iş en fazla 3 kez denenir; başarısızsa `failed` olarak kaydedilir ve diğer işler sürer. Kalite eşiğini geçemeyen içerik yayınlanmaz.

## 6) Maliyet notları (Actions dakikası)
Tahmini: Reels ~4 dk × 5, X ~1 dk × 36, YouTube eğlence ~25 dk + Shorts ~4 dk × 4, belgesel ~50 dk × 2/hafta ≈ günde 90–110 dk → ayda ~3.000 dk.
**Public repoda bu ücretsizdir.** Private repoya geçersen 2.000 dk sınırını aşarsın.
