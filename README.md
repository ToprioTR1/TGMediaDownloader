# 🎬 Telegram Video İndirme Botu

Instagram, YouTube, TikTok, X (Twitter), Facebook ve yt-dlp'nin desteklediği
yüzlerce siteden video indirip Telegram üzerinden gönderen bot.

Python 3.12 + [aiogram 3](https://github.com/aiogram/aiogram) + [yt-dlp](https://github.com/yt-dlp/yt-dlp)
ile yazıldı, Docker ile çalışacak şekilde hazırlandı.

## Özellikler

- 🔗 Bağlantı gönder → bot en uygun kalitede indirip gönderir
- 🎯 Akıllı kalite: link sonuna `1080p` / `720p` / `4k` yazarsan en yakın mevcut kalite;
  yazmazsan platform varsayılanı (Instagram 1280p, TikTok 1012p, YouTube 720p)
- 📦 Boyut koruması: seçilen kalite `MAX_UPLOAD_MB` limitini aşacaksa otomatik bir alt kaliteye iner
- 🎵 `mp3` / `ses` yazarak sadece ses indirme
- ⚡️ `file_id` önbelleği: aynı video tekrar istenirse yeniden indirilmeden anında gönderilir
- 🔘 Videoların altında `🔗 Kaynak` · `🎵 Ses olarak al` butonları
- 📝 Medya açıklamasındaki (caption) ve yanıtlanan mesajdaki bağlantılar da işlenir
- 🖼 Instagram fotoğraf / carousel gönderileri (gallery-dl ile, albüm olarak)
- 🧵 Kuyruk sistemi: aynı anda N indirme, kullanıcı başına kuyruk limiti
- ⏱ İlerleme mesajı (yüzde, hız, kalan süre)
- 📉 Günlük indirme limiti ve spam koruması (cooldown)
- 👤 Kullanıcı kaydı, indirme istatistikleri (SQLite)
- 🛠 Admin paneli: `/stats`, `/users`, `/broadcast`
- 🍪 Instagram/TikTok için `cookies.txt` desteği
- 🔄 Docker açılışında yt-dlp + gallery-dl otomatik güncelleme (`AUTO_UPDATE_YTDLP=1`)
- 🐳 Docker imajında ffmpeg hazır gelir

## Hızlı Başlangıç (Docker / VPS)

### 1. Bot tokeni al

Telegram'da [@BotFather](https://t.me/BotFather) → `/newbot` → tokeni kopyala.

Kendi Telegram kullanıcı ID'ni öğrenmek için [@userinfobot](https://t.me/userinfobot)
kullanabilirsin (admin komutları için gerekli).

### 2. Projeyi sunucuya kopyala

```bash
git clone <repo> video-bot && cd video-bot
# ya da dosyaları scp/rsync ile at
```

### 3. Ayarları gir

```bash
cp .env.example .env
nano .env
```

En az şunları doldur:

```env
BOT_TOKEN=123456789:AA...
ADMIN_IDS=123456789
```

### 4. Çalıştır

```bash
docker compose up -d --build
docker compose logs -f bot
```

Logda `Bot calisiyor: @kullanici_adin (id=...)` görürsen hazır. Telegram'da
`/start` yazıp test et.

### 5. Güncel tut

yt-dlp platform değişikliklerine çok sık uyum sağlar. Docker imajı her açılışta
`yt-dlp` ve `gallery-dl`'yi otomatik günceller (`AUTO_UPDATE_YTDLP=1`, varsayılan).
Güncelleme başarısız olursa bot mevcut sürümle açılmaya devam eder.

Otomatik güncellemeyi kapatmak için `.env` dosyasına `AUTO_UPDATE_YTDLP=0` ekleyin.
Bu durumda imajı ara ara yeniden derleyin:

```bash
docker compose build --no-cache && docker compose up -d
```

## Docker Olmadan Çalıştırma

Linux:

```bash
sudo apt update && sudo apt install -y ffmpeg python3-venv
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # .env'i düzenle
export DOWNLOAD_DIR=./data/downloads DB_PATH=./data/bot.db
python -m bot
```

Windows (PowerShell):

```powershell
winget install Gyan.FFmpeg          # ffmpeg gerekli
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env         # .env'i düzenle
$env:DOWNLOAD_DIR=".\data\downloads"; $env:DB_PATH=".\data\bot.db"
python -m bot
```

> `.env` içindeki `DOWNLOAD_DIR`, `DB_PATH` ve `COOKIES_FILE` yolları Docker
> içindir (`/data/...`). Yerelde çalıştırırken bunları `.\data\...` olarak
> değiştirin veya yukarıdaki gibi ortam değişkeni olarak verin.

## Yapılandırma

Tüm ayarlar `.env` üzerinden okunur ([bot/config.py](bot/config.py)):

| Değişken | Varsayılan | Açıklama |
|---|---|---|
| `BOT_TOKEN` | — | **Zorunlu.** BotFather tokeni |
| `ADMIN_IDS` | boş | Virgülle ayrılmış admin ID'leri |
| `CONCURRENCY` | `2` | Aynı anda çalışan indirme sayısı |
| `MAX_UPLOAD_MB` | `50` | Telegram'a gönderilebilecek maks. boyut |
| `MAX_HEIGHT` | `0` | Bu çözünürlüğün üstünü listede gösterme (0 = sınırsız) |
| `MAX_DURATION_SEC` | `1800` | Maks. video süresi (0 = sınırsız) |
| `MAX_QUEUE_PER_USER` | `3` | Kullanıcı başına bekleyen iş limiti |
| `DAILY_LIMIT` | `20` | Günlük indirme limiti (0 = sınırsız) |
| `COOLDOWN_SEC` | `10` | İki istek arası minimum süre |
| `QUALITY_INSTAGRAM` | `1280` | Kalite yazılmayan Instagram linklerinde hedef |
| `QUALITY_TIKTOK` | `1012` | Kalite yazılmayan TikTok linklerinde hedef |
| `QUALITY_YOUTUBE` | `720` | Kalite yazılmayan YouTube linklerinde hedef |
| `QUALITY_DEFAULT` | `720` | Diğer platformlar için hedef |
| `CACHE_DAYS` | `30` | `file_id` önbellek süresi, gün (0 = kapalı) |
| `GALLERY_ENABLED` | `1` | Instagram fotoğraf/carousel desteği (gallery-dl) |
| `GALLERY_LIMIT` | `10` | Gönderi başına en fazla medya (Telegram albüm limiti 10) |
| `AUTO_UPDATE_YTDLP` | `1` | Docker açılışında yt-dlp + gallery-dl güncelle |
| `COOKIES_FILE` | — | `cookies.txt` yolu (çerez gereken siteler için) |
| `TELEGRAM_API_URL` | boş | Yerel Bot API sunucusu adresi |
| `PROXY_URL` | boş | İndirmeler için HTTP(S)/SOCKS proxy |
| `DOWNLOAD_DIR` | `./data/downloads` | Geçici indirme klasörü |
| `DB_PATH` | `./data/bot.db` | SQLite veritabanı |
| `LOG_LEVEL` | `INFO` | Log seviyesi |

## Instagram / TikTok için Çerezler

Instagram (ve bazen TikTok, YouTube) giriş istediğinde bot "Bu içerik için
giriş gerekiyor" der. Çözüm: tarayıcıdan çerez dosyası çıkarmak.

1. Tarayıcıya **"Get cookies.txt LOCALLY"** (Chrome/Firefox) eklentisini kur.
2. Instagram'a giriş yap → eklenti → `instagram.com` için **Export as Netscape**.
3. Dosyayı sunucuda `data/cookies.txt` olarak kaydet:
   `scp cookies.txt kullanici@sunucu:~/video-bot/data/cookies.txt`
4. `docker compose restart bot`

> İpucu: Çerezler kişisel hesabınıza ait. Mümkünse bot için ayrı bir hesap
> kullanın; çerezler süresi doldukça (genelde haftalar) yenilemeniz gerekir.

## 50 MB Üstü Videolar (Yerel Bot API Sunucusu)

Telegram bulut API'si botların 50 MB'tan büyük dosya yüklemesine izin vermez.
Sınırı 2 GB'a çıkarmak için kendi Bot API sunucunuzu çalıştırabilirsiniz:

1. https://my.telegram.org → API development tools → `api_id` ve `api_hash` al.
2. `.env` dosyasına ekle:

```env
TELEGRAM_API_ID=123456
TELEGRAM_API_HASH=abcdef1234567890
```

3. Hazır override dosyasıyla başlat:

```bash
docker compose -f docker-compose.yml -f docker-compose.local-api.yml up -d --build
```

Bu dosya `aiogram/telegram-bot-api` imajını ayağa kaldırır, botu ona bağlar ve
`MAX_UPLOAD_MB` değerini `2000` yapar.

## Kullanım

1. Bota bir bağlantı gönder: `https://www.instagram.com/reel/...`
2. Bot kaliteyi otomatik seçer ve indirmeye başlar.
3. İlerleme mesajı güncellenir, video hazır olunca gönderilir; gönderim
   başarılı olursa durum mesajı silinir (sohbette sadece medya kalır).
4. Gönderilen videonun altındaki butonlarla kaynağa gidebilir veya sesini alabilirsin.

Aynı video ikinci kez istendiğinde `file_id` önbelleğinden **anında** gönderilir.
Gönderi açıklamasında (caption) link varsa veya linki bir mesajı yanıtlayarak
gönderirsen de çalışır: gönderiyi yanıtla, sadece `720p` yaz.

### Kalite kuralları

| Mesaj | Sonuç |
|---|---|
| `https://...` | Platform varsayılanı: Instagram **1280p**, TikTok **1012p**, YouTube **720p**, diğerleri 720p |
| `https://... 1080p` | En yakın mevcut kalite (ör. sadece 1280p varsa **1280p**) |
| `https://... 720p` | 720p'ye en yakın mevcut kalite |
| `https://... 4k` | 4K (2160p) en yakın kalite |
| `https://... mp3` | Sadece ses (MP3 192 kbps) |

Varsayılanlar `.env` içindeki `QUALITY_*` değişkenleriyle değiştirilebilir.
Seçilen kalitenin tahmini boyutu `MAX_UPLOAD_MB` limitini aşacaksa bot
indirmeden önce bir alt kaliteye iner; hiçbiri sığmıyorsa net bir hata gösterir.
Bir mesajda en fazla 3 bağlantı işlenir.

### Komutlar

| Komut | Kim | Açıklama |
|---|---|---|
| `/start` | herkes | Karşılama |
| `/help` | herkes | Platformlar ve limitler |
| `/me` | herkes | Kendi kullanım istatistikleri |
| `/admin` | admin | Butonlu admin paneli |
| `/stats` | admin | Kullanıcı/indirme istatistikleri |
| `/users` | admin | Son 10 kullanıcı |
| `/broadcast <mesaj>` | admin | Herkese duyuru |

## Proje Yapısı

```
bot/
├── __main__.py      # giriş noktası, dispatcher/middleware kurulumu
├── config.py        # .env → Settings (+ platform kalite varsayılanları)
├── database.py      # SQLite: kullanıcılar, indirmeler, file_id önbelleği
├── downloader.py    # yt-dlp: probe (metadata + boyut tahmini) + indirme
├── gallery.py       # gallery-dl: Instagram fotoğraf/carousel indirme
├── queue.py         # iş kuyruğu, işçiler, ilerleme, önbellek, Telegram gönderimi
├── keyboards.py     # inline klavyeler (video aksiyonları, admin paneli)
├── middlewares.py   # kullanıcı kaydı
├── utils.py         # URL/kalite ayrıştırma, önbellek anahtarı, biçimlendirme
└── handlers/
    ├── start.py     # /start /help /me
    ├── download.py  # link/caption/yanıt → kalite + boyut çözümleme → kuyruk
    └── admin.py     # admin komutları
```

Akış: `handlers/download.py` → `downloader.probe()` → `utils.nearest_height()`
(+ boyut koruması) → `queue.JobQueue.submit()` → işçi `downloader.download()`
→ `bot.send_video()` → `database.cache_put()` + `database.record_download()`.
Instagram fotoğraf gönderilerinde yt-dlp başarısız olursa `gallery.py` devreye girer.

## Test

`tests/smoke_test.py` sahte bir Bot nesnesiyle uçtan uca akışı dener (gerçek
ağdan küçük bir TikTok videosu indirir; varsayılan/istenen kalite, MP3,
file_id önbelleği, caption ve yanıt desteği, boyut koruması, gallery-dl ile
fotoğraf gönderimi, DB ve admin adımlarını kontrol eder):

```bash
pip install -r requirements.txt
$env:PYTHONPATH="." ; python tests/smoke_test.py    # PowerShell
PYTHONPATH=. python tests/smoke_test.py             # bash
```

Test kendi verisini `.tmp/` altında oluşturur ve Telegram'a gerçek istek atmaz.

## Sorun Giderme

| Belirti | Çözüm |
|---|---|
| `Bu içerik için giriş gerekiyor` | `data/cookies.txt` ekle, `docker compose restart bot` |
| `HTTP Error 403` / `429` | Çerezleri yenile, birkaç dakika bekle, VPS IP'si engellenmiş olabilir |
| `Sign in to confirm you're not a bot` (YouTube) | Çerez ekle veya `PROXY_URL` tanımla |
| `ffmpeg bulunamadı` | Docker imajında var; yerelde `apt install ffmpeg` / `winget install Gyan.FFmpeg` |
| İndirme çalışmıyor, başka site çalışıyor | `docker compose restart bot` (açılışta yt-dlp güncellenir) veya `docker compose build --no-cache` |
| Instagram fotoğrafı inmiyor | `gallery-dl` kurulu mu? (`GALLERY_ENABLED=1`); özel gönderiler için `cookies.txt` gerekir |
| `Request Entity Too Large` | 50 MB limiti; daha düşük kalite seç veya yerel Bot API kur |
| Bot cevap vermiyor | `docker compose logs -f bot`; webhook varsa silinir, polling kullanılır |

## Notlar

- Videolar geçici klasörde tutulur ve gönderimden sonra silinir; sunucuda birikmez.
- Başarılı gönderimden sonra ilerleme/durum mesajı silinir; sohbette sadece
  gönderilen medya kalır. Hata durumunda mesaj açıklamayla birlikte kalır.
- Instagram fotoğraf/carousel gönderileri gallery-dl ile indirilir; video içeren
  gönderilerde kalite seçimi için yt-dlp kullanılır.
- `file_id` önbelleği SQLite'ta tutulur (`CACHE_DAYS`, varsayılan 30 gün).
  Telegram dosyayı reddederse önbellek otomatik temizlenir ve yeniden indirilir.
- Canlı yayınlar desteklenmez.
- Bu botu yalnızca indirmeye hakkınız olan içerikler ve platformların kullanım
  koşullarına uygun şekilde kullanın. Sorumluluk kullanıcıya aittir.
