# 🎬 Telegram Video İndirme Botu

**Diller:** Türkçe · [English](README.md)

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
- 📄 `dosya` / `belge` / `file` / `fl` yazarak medyayı Telegram dosyası olarak gönderme
- ⚡️ `file_id` önbelleği: aynı video tekrar istenirse yeniden indirilmeden anında gönderilir
- 🧵 Aynı link indirilirken tekrar gönderilirse ikinci indirme veya durum mesajı açılmaz
- 🔁 Bağlantı inceleme veya indirme hata verirse bir kez otomatik tekrar denenir;
  yine olmazsa normal hata mesajı ve `Yeniden dene` butonu gösterilir. Tekrar
  deneme mevcut hata mesajını düzenler
- 📐 Telegram'da doğru en-boy oranı: her videoyla birlikte gerçek genişlik,
  yükseklik, süre ve küçük bir JPEG önizleme gönderilir; büyük videolar kare
  kutucuk olarak görünmez
- ⚙️ `/ayar` ile kişisel varsayılanlar: kalite, ses tercihi, caption
- 🔘 Her videonun altında hızlı aksiyonlar: `🎵 Ses olarak al` ·
  `🎚 Kalite değiştir` · `📄 Dosya olarak gönder` · `🖼 Kapak fotoğrafı`;
  mümkün olduğunda yeniden indirmeden `file_id` önbelleğinden çalışır
- 📝 Medya açıklamasındaki (caption) ve yanıtlanan mesajdaki bağlantılar da işlenir
- 🚦 Yalnızca yt-dlp site eşleştiricisinin tanıdığı URL'ler (veya sitelerin
  resmi kısa link alan adları) işlenir; normal web sayfaları ağ isteği
  yapılmadan ve mesaj yazılmadan yok sayılır
- 🖼 Instagram fotoğraf / carousel ve TikTok fotoğraf gönderileri (gallery-dl
  ile; herkese açık Instagram gönderileri için embed yedeğiyle; albüm olarak,
  `dosya` yazılırsa belge olarak)
- 🚫 Her Python TCP `connect` anında SSRF koruması: yönlendirmeler, DNS
  rebinding, ikincil medya URL'leri ve bulut metadata adresleri reddedilir;
  konteyner çıkışı için host güvenlik duvarı yapılandırması dahildir
- 🧵 Kuyruk sistemi: aynı anda N indirme, kullanıcı başına kuyruk limiti
- ⏱ İlerleme mesajı (yüzde, hız, kalan süre)
- 📉 Günlük indirme limiti ve spam koruması (cooldown)
- ⭐ Yetki yükseltme: `/promote @kullanıcıadı` ile kullanıcının günlük limiti kaldırılır (`/demote` ile geri alınır)
- 👤 Kullanıcı kaydı, indirme istatistikleri (SQLite)
- 🛠 Admin paneli: `/stats`, `/users`, `/cache`, `/broadcast`
- 📣 Duyuru, kayıtlı kullanıcılara **ve botun bulunduğu gruplara** gönderilir
- 🧹 Botun oluşturduğu geçici dosyalar (`dl-*`) periyodik olarak ve kapanışta temizlenir
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
| `UPLOAD_TIMEOUT_SEC` | `600` | Medya yükleme isteği zaman aşımı (yavaş bağlantı veya 2 GB yerel Bot API için artırın) |
| `MAX_HEIGHT` | `0` | Bu çözünürlüğün üstünü reddet; uygun format yoksa bağlantı reddedilir (0 = sınırsız) |
| `MAX_DURATION_SEC` | `1800` | Maks. video süresi (0 = sınırsız) |
| `MAX_QUEUE_PER_USER` | `3` | Kullanıcı başına bekleyen iş limiti |
| `DAILY_LIMIT` | `20` | Günlük indirme limiti, yerel gece yarısında sıfırlanır (0 = sınırsız) |
| `COOLDOWN_SEC` | `10` | İki istek arası minimum süre |
| `QUALITY_INSTAGRAM` | `1280` | Kalite yazılmayan Instagram linklerinde hedef |
| `QUALITY_TIKTOK` | `1012` | Kalite yazılmayan TikTok linklerinde hedef |
| `QUALITY_YOUTUBE` | `720` | Kalite yazılmayan YouTube linklerinde hedef |
| `QUALITY_DEFAULT` | `720` | Diğer platformlar için hedef |
| `CACHE_DAYS` | `30` | `file_id` önbellek süresi, gün (0 = kapalı) |
| `GALLERY_ENABLED` | `1` | Instagram/TikTok fotoğraf desteği (gallery-dl) |
| `GALLERY_LIMIT` | `10` | Gönderi başına en fazla medya (Telegram albüm limiti 10) |
| `AUTO_UPDATE_YTDLP` | `1` | Docker açılışında yt-dlp + gallery-dl güncelle |
| `COOKIES_FILE` | — | `cookies.txt` yolu (çerez gereken siteler için) |
| `TELEGRAM_API_URL` | boş | Yerel Bot API sunucusu adresi (host'u otomatik izin listesine eklenir) |
| `PROXY_URL` | boş | İndirmeler için HTTP(S)/SOCKS proxy (`NETGUARD_ALLOW_PROXY=1` olmadan kullanılmaz) |
| `NETGUARD_ALLOW` | boş | SSRF korumasında ek izin verilecek güvenilir `host:port` uçları |
| `NETGUARD_ALLOW_PROXY` | `0` | `PROXY_URL`'i kullan; yalnızca proxy kendisi özel/metadata çıkışını engelliyorsa |
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

## Ağ Çıkışı / SSRF Koruması

Kullanıcıdan gelen URL'ler güvenilmezdir. Bot yalnızca ilk URL metnini değil,
her giden Python TCP bağlantısını denetler:

- Hedef IP `connect()` anında kontrol edilir. Yönlendirme, ikinci bir
  medya/segment isteği veya ilk çözümlemeden sonra değişen bir DNS yanıtı özel
  ağ, loopback, link-local, CGNAT, multicast, rezerve, dokümantasyon ya da bulut
  metadata adresine ulaşırsa yine reddedilir.
- DNS doğrulaması başarısız olursa istek reddedilir (fail closed); devam izni
  verilmez.
- yt-dlp yerel HLS indiricisine zorlanır; gallery-dl `--config-ignore` ile
  çalışır ve aynı korumayı `sitecustomize` önyüklemesiyle alt sürecine taşır.
- `TELEGRAM_API_URL` host'u (yerel Bot API dahil) otomatik izin listesine
  eklenir. Başka güvenilir uçları `NETGUARD_ALLOW` ile ekleyin.

Süreç içi koruma `ffmpeg` gibi ayrı yerel süreçlerin trafiğini göremez;
etkinleştirilmiş bir proxy ise nihai hedefi gizler. **Konteyner çıkışını
kapsamak için host güvenlik duvarını uygulayın:**

```bash
sudo nft -f deploy/ssrf-firewall.nft
sudo nft list table inet ssrf_egress
```

`docker-compose.yml` botu `172.28.240.0/24` ağında `172.28.240.3` adresine
koyar; yerel Bot API konteyneri `172.28.240.2:8081` olur. nftables kuralları
yalnızca bu API ucuna izin verir ve bottan özel/metadata aralıklarına yeni
bağlantıları **ffmpeg dahil** tüm süreçler için düşürür. Bu alt ağ VPS'inizle
çakışıyorsa hem compose dosyalarında hem firewall dosyasında değiştirin. Bu
Docker ağında IPv6'yı kapalı tutun; açarsanız dosyadaki yorumlu `ip6`
kurallarını da ekleyin, aksi halde IPv4-mapped IPv6 adresleri IPv4 kurallarını
atlayabilir.

Kuralları dağıtımınıza göre kalıcı yapın, örneğin:

```bash
sudo sh -c 'nft list ruleset > /etc/nftables.conf'
sudo systemctl enable --now nftables
```

Yerel Bot API host'ta çalışıyorsa (`host.docker.internal:8081`),
`deploy/ssrf-firewall.nft` içindeki yorumlu izin satırını açın ve `.env`
dosyasına `NETGUARD_ALLOW=172.28.240.1:8081` ekleyin.

`PROXY_URL` veya standart `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY` ortam
değişkenlerinden biri ayarlıysa koruma nihai hedefi doğrulayamaz. Bu nedenle
indirmeler varsayılan olarak proxy'yi **kullanmaz** ve doğrudan bağlanır; her
hedef yine soket katmanında denetlenir. `NETGUARD_ALLOW_PROXY=1` yalnızca proxy
kendisi özel/metadata çıkışını engelliyorsa kullanılmalıdır; eşdeğer kuralları
proxy tarafında da uygulayın. Proxy'yi SSRF sınırı olarak kullanmayın.

Proxy özel bir adreste ise (örneğin host üzerinde `172.28.240.1:3128`), özel
ağ drop kuralından önce dar bir izin kuralı ekleyin; aksi halde host güvenlik
duvarı proxy'nin kendisini engeller:

```nft
ip saddr 172.28.240.3 ip daddr 172.28.240.1 tcp dport 3128 accept
```

Kuralı olabildiğince dar tutun. Proxy kendi çıkış politikasını uygulamalıdır;
botun soket koruması yalnızca proxy bağlantısını görür.

Docker olmadan, `python -m bot` çalıştıran UID için eşdeğer nftables `output`
kuralını uygulayın (`1000` yerine kendi UID'niz):

```nft
table inet ssrf_egress_host {
    chain output {
        type filter hook output priority -10; policy accept;
        meta skuid 1000 ip daddr {
            0.0.0.0/8, 10.0.0.0/8, 100.64.0.0/10, 127.0.0.0/8,
            168.63.129.16/32, 169.254.0.0/16, 172.16.0.0/12,
            192.0.0.0/24, 192.0.2.0/24, 192.88.99.0/24,
            192.168.0.0/16, 198.18.0.0/15, 198.51.100.0/24,
            203.0.113.0/24, 224.0.0.0/4, 240.0.0.0/4
        } drop
    }
}
```

Ayrıca eşdeğer `ip6` kurallarını ekleyin (veya IPv6'yı kapatın) ve yerel Bot
API kullanıyorsanız ilgili portu açın.

Kalan sınır: yerel kütüphane/alt süreçleri ve proxy üzerinden tünellenen
hedefleri yalnızca host güvenlik duvarı kapsar. Firewall uygulanmazsa `ffmpeg`
(veya başka bir yerel indirici) çağıran bir extractor ve proxy hâlâ iç adreslere
ulaşabilir. Uygulama içi koruma tek başına bu yolları kapsadığını iddia etmez.
Host `forward` kuralları konteynerin kendi loopback'ini veya `AF_UNIX`
soketlerini göremez; uygulama içi koruma Python loopback'ini engeller ve bot
imajı yerel servis çalıştırmaz. UDP/QUIC özel hedefler için yalnızca host
güvenlik duvarıyla, Python soket kancasıyla değil, kapsanır.

## Kullanım

1. Bota bir bağlantı gönder: `https://www.instagram.com/reel/...`
2. Bot kaliteyi otomatik seçer ve indirmeye başlar.
3. İlerleme mesajı güncellenir, video hazır olunca gönderilir; gönderim
   başarılı olursa durum mesajı silinir (sohbette sadece medya kalır).
4. Gönderilen videonun altındaki butonlarla kaynağa gidebilir veya sesini alabilirsin.

Aynı video ikinci kez istendiğinde `file_id` önbelleğinden **anında** gönderilir.
Gönderi açıklamasında (caption) link varsa veya linki bir mesajı yanıtlayarak
gönderirsen de çalışır: gönderiyi yanıtla, sadece `720p` yaz.

Her istek tek bir durum mesajı kullanır: link incelenirken, indirilirken ve
gönderilirken bu mesaj yerinde düzenlenir; medya gelince silinir. Hızlı
güncellemeler birleştirilir, içerik değişmediyse tekrar gönderilmez ve aynı
sohbet içindeki durum düzenlemeleri kısa bir minimum aralıkla sırayla yapılır.
İlerleme en fazla 10 saniyede bir yenilenir. Aynı link indirilirken tekrar
gönderilirse yeni iş veya yeni durum mesajı açılmaz. Başarısız işlerde mevcut
hata mesajı `🔁 Yeniden dene` butonuyla kalır; tekrar deneme de aynı mesajı
kullanır.

`/ayar` ile kişisel varsayılanları kaydedebilirsin:

```text
/ayar                     # mevcut ayarları göster
/ayar kalite 1080         # varsayılan hedef kalite (0 = platform varsayılanı)
/ayar ses aç              # kalite yazılmayan linkleri ses olarak indir
/ayar ses kapat           # ses yalnızca mp3/ses yazınca indirilsin
/ayar caption kapat       # medyayı açıklamasız gönder
```

Mesajda açıkça yazılan kalite her zaman kayıtlı varsayılanı ezer. Ses modu
açıkken video istemek için linkin sonuna `720p` gibi bir kalite yaz.

### Kalite kuralları

| Mesaj | Sonuç |
|---|---|
| `https://...` | Platform varsayılanı: Instagram **1280p**, TikTok **1012p**, YouTube **720p**, diğerleri 720p |
| `https://... 1080p` | En yakın mevcut kalite (ör. sadece 1280p varsa **1280p**) |
| `https://... 720p` | 720p'ye en yakın mevcut kalite |
| `https://... 4k` | 4K (2160p) en yakın kalite |
| `https://... mp3` | Sadece ses (MP3 192 kbps) |
| `https://... dosya` | İndirilen medyayı Telegram dosyası olarak gönder (`dosya`, `belge`, `file`, `fl`) |

Kalite eki `1080`, `1080p` veya `1080 p` şeklinde yazılabilir; üçü de aynıdır.

Varsayılanlar `.env` içindeki `QUALITY_*` değişkenleriyle veya kullanıcı bazında
`/ayar` ile değiştirilebilir. Seçilen kalitenin tahmini boyutu `MAX_UPLOAD_MB`
limitini aşacaksa bot indirmeden önce bir alt kaliteye iner; hiçbiri sığmıyorsa
net bir hata gösterir. Bir mesajda en fazla 3 bağlantı işlenir.

### Komutlar

| Komut | Kim | Açıklama |
|---|---|---|
| `/start` | herkes | Karşılama |
| `/help` | herkes | Platformlar ve limitler |
| `/ayar` | herkes | Kişisel varsayılanlar: kalite, ses, caption |
| `/me` | herkes | Kendi kullanım istatistikleri |
| `/admin` | admin | Butonlu admin paneli |
| `/stats` | admin | Kullanıcı/grup/indirme istatistikleri |
| `/users` | admin | Son 10 kullanıcı (`⭐` = sınırsız hak) |
| `/cache` | admin | Önbellek adedi/boyutu ve temizleme butonları |
| `/cache temizle 7` | admin | 7 günden eski önbellek kayıtlarını sil (en az 1 gün) |
| `/cache tum` | admin | Onay isteyip tüm önbelleği sil |
| `/broadcast <mesaj>` | admin | Tüm kullanıcılara ve kayıtlı gruplara duyuru |
| `/promote @kullanıcıadı` | admin | Kullanıcıya sınırsız günlük indirme hakkı ver (mesaja yanıt da olur) |
| `/demote @kullanıcıadı` | admin | Sınırsız hakkı geri al |

`/broadcast` botun bulunduğu gruplara da gönderilir. Gruplar bot gruba
eklendiğinde veya grupta etkinlik gördüğünde kaydedilir; bot gruptan ayrılır ya
da atılırsa listeden çıkarılır.

`/promote` ile yetkilendirilen kullanıcıya günlük indirme limiti uygulanmaz.
Hak, `/demote` yazılana kadar geçerlidir. Kullanıcı `/me` çıktısında
`⭐ Sınırsız indirme hakkı: aktif` satırını görür.

Kullanıcı adıyla yetkilendirme için kullanıcının bota en az bir kez yazmış
olması gerekir (botun kullanıcı adından ID çözebilmesi için). Alternatif olarak
kullanıcının mesajına yanıt verip `/promote` yazabilirsiniz. `t.me/kullaniciadi`
biçimi ve büyük/küçük harf farkı da desteklenir.

## Proje Yapısı

```
bot/
├── __main__.py      # giriş noktası, dispatcher/middleware kurulumu
├── config.py        # .env → Settings (+ platform kalite varsayılanları)
├── database.py      # SQLite: kullanıcılar, indirmeler, file_id önbelleği
├── downloader.py    # yt-dlp: probe (metadata + boyut tahmini) + indirme
├── gallery.py       # gallery-dl: Instagram/TikTok fotoğraf indirme
├── media.py         # ffprobe metadata + Telegram JPEG önizlemesi
├── queue.py         # iş kuyruğu, işçiler, ilerleme, önbellek, Telegram gönderimi
├── keyboards.py     # inline klavyeler (video aksiyonları, admin paneli)
├── middlewares.py   # kullanıcı kaydı
├── utils.py         # URL/kalite ayrıştırma, önbellek anahtarı, biçimlendirme
└── handlers/
    ├── start.py     # /start /help /ayar /me
    ├── download.py  # link/caption/yanıt → kalite + boyut çözümleme → kuyruk
    └── admin.py     # admin komutları (promote/demote dahil)
```

Akış: `handlers/download.py` → `downloader.probe()` →
`handlers/download.py:_resolve_preset()` (en yakın kalite + boyut koruması) →
`queue.JobQueue.submit()` → işçi `downloader.download()` →
`media.inspect_video()` (boyut + önizleme) → `bot.send_video()` →
`database.cache_put()` + `database.record_download()`.
Instagram ve TikTok fotoğraf gönderilerinde yt-dlp başarısız olursa
`gallery.py` devreye girer.

## Test

`tests/smoke_test.py` sahte bir Bot nesnesiyle uçtan uca akışı dener (gerçek
ağdan küçük bir TikTok videosu indirir; varsayılan/istenen kalite, MP3, dosya
olarak gönderim, file_id önbelleği, aynı linkin tekilleştirilmesi, yeniden deneme
sahipliği, günlük limit, caption ve yanıt desteği, boyut koruması, videonun
genişlik/yükseklik/önizleme bilgisiyle gönderilmesi, gallery-dl ile fotoğraf
gönderimi (belge modu dahil), `/ayar`, geçici dosya temizliği, SSRF/özel adres
engeli, durum mesajlarının birleştirilmesi, `RetryAfter`, geçici ağ hataları,
başarılı gönderimin korunması ve DB/admin/önbellek adımlarını kontrol eder):

```bash
pip install -r requirements.txt
$env:PYTHONPATH="." ; python tests/smoke_test.py    # PowerShell
PYTHONPATH=. python tests/smoke_test.py             # bash

python -m unittest tests.test_netguard -v           # SSRF/connect regresyon testleri
python -m unittest tests.test_url_support -v        # yt-dlp URL eşleştirme testleri
python -m unittest tests.test_instagram_embed -v    # Instagram embed ayrıştırma testleri
python -m unittest tests.test_media -v              # ffprobe/önizleme testleri (ffmpeg gerekir)
python -m unittest tests.test_retry -v              # tek otomatik tekrar testleri
python -m unittest tests.test_database_migration -v # eski önbellek taşıma testleri
python -m unittest tests.test_qol -v                # aksiyon butonları
```

Duman testi kendi verisini `.tmp/` altında oluşturur ve Telegram'a gerçek istek
atmaz. Netguard testleri yalnızca yerel soketler kullanır (loopback izin listesi,
yönlendirme, DNS rebinding, fail-closed DNS ve gallery-dl alt süreç
önyüklemesi). URL destek testleri yalnızca yerel yt-dlp extractor eşleştirmesi
kullanır ve ağ isteği yapmaz.

## Sorun Giderme

| Belirti | Çözüm |
|---|---|
| `Bu içerik için giriş gerekiyor` | `data/cookies.txt` ekle, `docker compose restart bot` |
| `HTTP Error 403` / `429` | Çerezleri yenile, birkaç dakika bekle, VPS IP'si engellenmiş olabilir |
| `Sign in to confirm you're not a bot` (YouTube) | Çerez ekle veya `PROXY_URL` tanımla |
| `ffmpeg bulunamadı` | Docker imajında var; yerelde `apt install ffmpeg` / `winget install Gyan.FFmpeg` |
| İndirme `güvenlik politikası bu hedefi engelledi` hatası veriyor | Hedef özel/metadata adres, DNS başarısız veya `PROXY_URL` var ama `NETGUARD_ALLOW_PROXY=1` değil (proxy atlanır; doğrudan bağlantı başarısız olabilir); `docker compose logs -f bot` kontrol et |
| İndirmeler yalnızca firewall kapatınca çalışıyor | `deploy/ssrf-firewall.nft` kurallarını yeniden uygula; botun hâlâ `172.28.240.3` olduğunu ve yerel API izin kuralının eşleştiğini doğrula |
| İndirme çalışmıyor, başka site çalışıyor | `docker compose restart bot` (açılışta yt-dlp güncellenir) veya `docker compose build --no-cache` |
| Instagram/TikTok fotoğrafı inmiyor | `gallery-dl` kurulu mu? (`GALLERY_ENABLED=1`); özel gönderiler için `cookies.txt` gerekir |
| `Request Entity Too Large` | 50 MB limiti; daha düşük kalite seç veya yerel Bot API kur |
| Bot cevap vermiyor | `docker compose logs -f bot`; webhook varsa silinir, polling kullanılır |

## Notlar

- Videolar `dl-*` geçici klasöründe tutulur ve gönderimden sonra silinir;
  sunucuda birikmez. 6 saatten eski artık `dl-*` girdileri 30 dakikada bir çalışan
  temizlik göreviyle silinir; `DOWNLOAD_DIR` içindeki diğer dosyalara dokunulmaz.
  Takip edilen geçici klasörler kapanışta da temizlenir.
- Günlük indirme limiti hem bağlantı incelenmeden önce hem de iş kuyruğa girerken
  denetlenir ve yerel gece yarısında sıfırlanır.
- Tüm aşamalar tek bir durum mesajı düzenlenerek gösterilir; her aşama için yeni
  mesaj gönderilmez. Güncellemeler birleştirilir, yalnızca en son bekleyen durum
  yazılır, aynı metin tekrar gönderilmez ve aynı sohbetteki düzenlemeler minimum
  2,5 saniye aralıkla sırayla yapılır. `RetryAfter` yanıtlarında Telegram'ın
  bildirdiği süre kadar beklenir; geçici ağ/sunucu hatalarında sınırlı ve artan
  aralıklarla yeniden denenir. Başarılı gönderimden sonra durum mesajı silinir;
  sohbette sadece gönderilen medya kalır. Hata durumunda mesaj açıklama ve
  yeniden deneme butonuyla birlikte kalır.
- Bağlantı inceleme, video indirme ve galeri indirme işlemleri hata verirse bir
  kez otomatik tekrar denenir. İkinci hata normal hata mesajını ve manuel
  `Yeniden dene` butonunu gösterir. Desteklenmeyen linkler yine sessizce yok
  sayılır ve tekrar denenmez.
- Medya yüklemeleri `UPLOAD_TIMEOUT_SEC` kullanır (varsayılan 600 saniye).
  Telegram zamanında yanıt vermezse bot dosyayı otomatik olarak **tekrar
  göndermez**; çünkü dosya çoktan ulaşmış olabilir ve tekrar deneme kopya
  oluşturabilir. Bunun yerine "Gönderim yanıtı alınamadı" uyarısı gösterilir.
  Telegram flood (`RetryAfter`) yanıtları eskisi gibi bir kez tekrar denenir.
- Videolar `width`, `height`, `duration` ve ffmpeg ile üretilen JPEG önizleme
  bilgisiyle gönderilir. Bu, özellikle yaklaşık 10 MB'tan büyük dosyalarda
  Telegram'daki en-boy oranını korur. ffprobe/ffmpeg yoksa bot eski davranışa
  döner.
- Instagram fotoğraf/carousel ve TikTok fotoğraf gönderileri gallery-dl ile
  indirilir; video içeren gönderilerde kalite seçimi için yt-dlp kullanılır.
  Instagram gallery-dl'yi giriş sayfasına yönlendirirse herkese açık
  fotoğraf/carousel gönderileri embed sayfasından okunur; özel gönderiler için
  yine `cookies.txt` gerekir.
- `file_id` önbelleği SQLite'ta tutulur (`CACHE_DAYS`, varsayılan 30 gün).
  Telegram dosyayı reddederse önbellek otomatik temizlenir ve yeniden indirilir.
  Eski kayıtlar açılışta budanır; adminler `/cache` ile önbelleği inceleyebilir,
  budayabilir veya temizleyebilir. Tüm kayıtları silmek açık onay gerektirir.
  En-boy oranı düzeltmesinden önce oluşturulan video kayıtlarında boyut bilgisi
  yoktur; bu kayıtlar açılışta bir kez silinip doğru geometriyle yeniden
  indirilir. Ses ve belge kayıtları korunur.
- Canlı yayınlar desteklenmez.
- Bu botu yalnızca indirmeye hakkınız olan içerikler ve platformların kullanım
  koşullarına uygun şekilde kullanın. Sorumluluk kullanıcıya aittir.
