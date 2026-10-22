# 🎬 Telegram Video Downloader Bot

**Languages:** English · [Türkçe](README.tr.md)

A bot that downloads videos from Instagram, YouTube, TikTok, X (Twitter),
Facebook and hundreds of other sites supported by yt-dlp, then sends them back
through Telegram.

Built with Python 3.12 + [aiogram 3](https://github.com/aiogram/aiogram) +
[yt-dlp](https://github.com/yt-dlp/yt-dlp) and ready to run with Docker.

## Features

- 🔗 Send a link → the bot downloads it in the best available quality and sends it back
- 🎯 Smart quality: add `1080p` / `720p` / `4k` to the link for the nearest available
  quality; without a quality it uses the platform default (Instagram 1280p,
  TikTok 1012p, YouTube 720p)
- 📦 Size guard: if the selected quality would exceed `MAX_UPLOAD_MB`, it automatically
  steps down to the next lower quality
- 🎵 Audio only with `mp3` / `ses` (`ses` is Turkish for "audio")
- 📄 Send as a file with `dosya` / `belge` / `file` / `fl`
- ⚡️ `file_id` cache: requesting the same video again sends it instantly without re-downloading
- 🧵 Duplicate links: sending the same link while it is still downloading is
  ignored, so no second download or status message is created
- 🔁 Retry button on failed downloads; retrying edits the existing error message
  instead of sending a new one
- ⚙️ Per-user defaults via `/ayar`: target quality, audio-only links, captions
- 🔘 `🔗 Source` · `🎵 Get audio` buttons under videos
- 📝 Links in media captions and in replied-to messages are processed too
- 🖼 Instagram photo / carousel posts (via gallery-dl, sent as an album)
- 🧵 Job queue: N concurrent downloads, per-user queue limit
- ⏱ Progress message (percentage, speed, ETA)
- 📉 Daily download limit and spam protection (cooldown)
- ⭐ Privilege promotion: `/promote @username` removes the user's daily limit
  (`/demote` restores it)
- 👤 User registration and download statistics (SQLite)
- 🛠 Admin panel: `/stats`, `/users`, `/cache`, `/broadcast`
- 🧹 Temporary files are cleaned periodically and on graceful shutdown
- 🍪 `cookies.txt` support for Instagram/TikTok
- 🔄 Automatic yt-dlp + gallery-dl update on Docker startup (`AUTO_UPDATE_YTDLP=1`)
- 🐳 ffmpeg comes preinstalled in the Docker image

## Quick Start (Docker / VPS)

### 1. Get a bot token

In Telegram, open [@BotFather](https://t.me/BotFather) → `/newbot` → copy the token.

To find your own Telegram user ID, you can use
[@userinfobot](https://t.me/userinfobot) (needed for admin commands).

### 2. Copy the project to your server

```bash
git clone <repo> video-bot && cd video-bot
# or upload the files with scp/rsync
```

### 3. Configure

```bash
cp .env.example .env
nano .env
```

At minimum, fill in:

```env
BOT_TOKEN=123456789:AA...
ADMIN_IDS=123456789
```

### 4. Run

```bash
docker compose up -d --build
docker compose logs -f bot
```

If you see `Bot calisiyor: @your_username (id=...)` in the logs, it is ready.
Send `/start` in Telegram to test it.

### 5. Keep it up to date

yt-dlp adapts to platform changes very frequently. The Docker image updates
`yt-dlp` and `gallery-dl` automatically on every startup
(`AUTO_UPDATE_YTDLP=1`, the default). If an update fails, the bot keeps running
with the current version.

To disable automatic updates, add `AUTO_UPDATE_YTDLP=0` to `.env`.
In that case, rebuild the image from time to time:

```bash
docker compose build --no-cache && docker compose up -d
```

## Running Without Docker

Linux:

```bash
sudo apt update && sudo apt install -y ffmpeg python3-venv
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # edit .env
export DOWNLOAD_DIR=./data/downloads DB_PATH=./data/bot.db
python -m bot
```

Windows (PowerShell):

```powershell
winget install Gyan.FFmpeg          # ffmpeg is required
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env         # edit .env
$env:DOWNLOAD_DIR=".\data\downloads"; $env:DB_PATH=".\data\bot.db"
python -m bot
```

> The `DOWNLOAD_DIR`, `DB_PATH` and `COOKIES_FILE` paths in `.env` are intended
> for Docker (`/data/...`). When running locally, change them to `.\data\...`
> or pass them as environment variables as shown above.

## Configuration

All settings are read from `.env` ([bot/config.py](bot/config.py)):

| Variable | Default | Description |
|---|---|---|
| `BOT_TOKEN` | — | **Required.** BotFather token |
| `ADMIN_IDS` | empty | Comma-separated admin IDs |
| `CONCURRENCY` | `2` | Number of concurrent downloads |
| `MAX_UPLOAD_MB` | `50` | Maximum size that can be sent to Telegram |
| `MAX_HEIGHT` | `0` | Hide resolutions above this from the format list (0 = unlimited) |
| `MAX_DURATION_SEC` | `1800` | Maximum video duration (0 = unlimited) |
| `MAX_QUEUE_PER_USER` | `3` | Pending jobs per user |
| `DAILY_LIMIT` | `20` | Daily download limit (0 = unlimited) |
| `COOLDOWN_SEC` | `10` | Minimum seconds between two requests |
| `QUALITY_INSTAGRAM` | `1280` | Target for Instagram links without a quality |
| `QUALITY_TIKTOK` | `1012` | Target for TikTok links without a quality |
| `QUALITY_YOUTUBE` | `720` | Target for YouTube links without a quality |
| `QUALITY_DEFAULT` | `720` | Target for other platforms |
| `CACHE_DAYS` | `30` | `file_id` cache lifetime in days (0 = disabled) |
| `GALLERY_ENABLED` | `1` | Instagram photo/carousel support (gallery-dl) |
| `GALLERY_LIMIT` | `10` | Maximum media per post (Telegram album limit is 10) |
| `AUTO_UPDATE_YTDLP` | `1` | Update yt-dlp + gallery-dl on Docker startup |
| `COOKIES_FILE` | — | Path to `cookies.txt` (for sites that require cookies) |
| `TELEGRAM_API_URL` | empty | Local Bot API server URL |
| `PROXY_URL` | empty | HTTP(S)/SOCKS proxy for downloads |
| `DOWNLOAD_DIR` | `./data/downloads` | Temporary download directory |
| `DB_PATH` | `./data/bot.db` | SQLite database |
| `LOG_LEVEL` | `INFO` | Log level |

## Cookies for Instagram / TikTok

When Instagram (and sometimes TikTok or YouTube) asks for a login, the bot says
"This content requires a login". The fix is to export a cookie file from your
browser.

1. Install the **"Get cookies.txt LOCALLY"** extension (Chrome/Firefox).
2. Log in to Instagram → extension → **Export as Netscape** for `instagram.com`.
3. Save the file on the server as `data/cookies.txt`:
   `scp cookies.txt user@server:~/video-bot/data/cookies.txt`
4. `docker compose restart bot`

> Tip: cookies belong to your personal account. Use a separate account for the
> bot if possible; cookies expire over time (usually after a few weeks) and must
> be refreshed.

## Videos Over 50 MB (Local Bot API Server)

The Telegram cloud API does not allow bots to upload files larger than 50 MB.
To raise the limit to 2 GB, you can run your own Bot API server:

1. https://my.telegram.org → API development tools → get `api_id` and `api_hash`.
2. Add them to `.env`:

```env
TELEGRAM_API_ID=123456
TELEGRAM_API_HASH=abcdef1234567890
```

3. Start with the ready-made override file:

```bash
docker compose -f docker-compose.yml -f docker-compose.local-api.yml up -d --build
```

This file starts the `aiogram/telegram-bot-api` image, connects the bot to it,
and sets `MAX_UPLOAD_MB` to `2000`.

## Usage

1. Send a link to the bot: `https://www.instagram.com/reel/...`
2. The bot picks the quality automatically and starts downloading.
3. The progress message is updated; when the video is ready it is sent. If
   sending succeeds, the status message is deleted (only the media remains in
   the chat).
4. Use the buttons under the video to open the source or get its audio.

Requesting the same video a second time sends it **instantly** from the
`file_id` cache. It also works when the link is in a post caption or when you
reply to a message with a link: reply to the post and just type `720p`.

Each request uses a single status message: it is edited in place while the link
is probed, downloaded and sent, then deleted after the media arrives. Rapid
updates are coalesced, unchanged text is not sent again, and status edits in one
chat are serialized with a short minimum interval. Progress is refreshed at most
every 10 seconds. If the same link is sent again while it is still queued or
downloading, no second job or status message is created. Failed jobs keep their
existing message and show a `🔁 Yeniden dene` (retry) button; retrying reuses
that same message.

Use `/ayar` to save personal defaults:

```text
/ayar                     # show current settings
/ayar kalite 1080         # default target quality (0 = platform default)
/ayar ses aç              # links without a quality are downloaded as audio
/ayar ses kapat           # audio only when mp3/ses is written
/ayar caption kapat       # send media without captions
```

An explicit quality in the message always overrides the saved default. With
audio-only mode enabled, write `720p` (or another quality) to request video.

### Quality rules

| Message | Result |
|---|---|
| `https://...` | Platform default: Instagram **1280p**, TikTok **1012p**, YouTube **720p**, others 720p |
| `https://... 1080p` | Nearest available quality (e.g. **1280p** if that is the only option) |
| `https://... 720p` | Nearest available quality to 720p |
| `https://... 4k` | Nearest available quality to 4K (2160p) |
| `https://... mp3` | Audio only (MP3 192 kbps) |
| `https://... dosya` | Send the downloaded media as a Telegram document (`dosya`, `belge`, `file`, `fl`) |

The quality suffix can be written as `1080`, `1080p` or `1080 p`; all three are
equivalent.

Defaults can be changed with the `QUALITY_*` variables in `.env` or per user with
`/ayar`. If the estimated size of the selected quality exceeds `MAX_UPLOAD_MB`,
the bot steps down to a lower quality before downloading; if none fit, it shows
a clear error. At most 3 links are processed per message.

### Commands

| Command | Who | Description |
|---|---|---|
| `/start` | everyone | Welcome message |
| `/help` | everyone | Platforms and limits |
| `/ayar` | everyone | Personal defaults: quality, audio-only, captions |
| `/me` | everyone | Your own usage statistics |
| `/admin` | admin | Admin panel with buttons |
| `/stats` | admin | User/download statistics |
| `/users` | admin | Last 10 users (`⭐` = unlimited access) |
| `/cache` | admin | Cache count/size and cleanup buttons |
| `/cache temizle 7` | admin | Delete cache entries older than 7 days |
| `/cache tum` | admin | Clear the entire cache |
| `/broadcast <message>` | admin | Send an announcement to everyone |
| `/promote @username` | admin | Grant unlimited daily downloads (replying to a message also works) |
| `/demote @username` | admin | Revoke unlimited access |

Users promoted with `/promote` are not subject to the daily download limit.
The privilege remains until `/demote` is used. The user sees
`⭐ Sınırsız indirme hakkı: aktif` (unlimited download access: active) in `/me`.

Promoting by username requires the user to have messaged the bot at least once
(so the bot can resolve the username to an ID). Alternatively, reply to the
user's message with `/promote`. The `t.me/username` format is supported too,
and usernames are matched case-insensitively.

## Project Structure

```
bot/
├── __main__.py      # entry point, dispatcher/middleware setup
├── config.py        # .env → Settings (+ per-platform quality defaults)
├── database.py      # SQLite: users, downloads, file_id cache
├── downloader.py    # yt-dlp: probe (metadata + size estimate) + download
├── gallery.py       # gallery-dl: Instagram photo/carousel download
├── queue.py         # job queue, workers, progress, cache, Telegram delivery
├── keyboards.py     # inline keyboards (video actions, admin panel)
├── middlewares.py   # user registration
├── utils.py         # URL/quality parsing, cache key, formatting
└── handlers/
    ├── start.py     # /start /help /ayar /me
    ├── download.py  # link/caption/reply → quality + size resolution → queue
    └── admin.py     # admin commands (including promote/demote)
```

Flow: `handlers/download.py` → `downloader.probe()` → `utils.nearest_height()`
(+ size guard) → `queue.JobQueue.submit()` → worker `downloader.download()`
→ `bot.send_video()` → `database.cache_put()` + `database.record_download()`.
For Instagram photo posts, `gallery.py` takes over if yt-dlp fails.

## Tests

`tests/smoke_test.py` exercises the end-to-end flow with a fake Bot object (it
downloads a small TikTok video from the real network and checks default/requested
quality, MP3, document delivery, the file_id cache, in-flight deduplication,
retry, caption and reply support, the size guard, photo delivery via gallery-dl,
`/ayar`, temporary-file cleanup, status-message coalescing, `RetryAfter`,
transient network handling, and the DB/admin/cache steps):

```bash
pip install -r requirements.txt
$env:PYTHONPATH="." ; python tests/smoke_test.py    # PowerShell
PYTHONPATH=. python tests/smoke_test.py             # bash
```

The test creates its own data under `.tmp/` and does not make real requests to
Telegram.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Bu içerik için giriş gerekiyor` (login required) | Add `data/cookies.txt`, then `docker compose restart bot` |
| `HTTP Error 403` / `429` | Refresh cookies, wait a few minutes; the VPS IP may be blocked |
| `Sign in to confirm you're not a bot` (YouTube) | Add cookies or configure `PROXY_URL` |
| `ffmpeg bulunamadı` (ffmpeg not found) | It is included in the Docker image; locally run `apt install ffmpeg` / `winget install Gyan.FFmpeg` |
| One site stops working while others work | `docker compose restart bot` (yt-dlp updates on startup) or `docker compose build --no-cache` |
| Instagram photos are not downloading | Is `gallery-dl` installed? (`GALLERY_ENABLED=1`); private posts require `cookies.txt` |
| `Request Entity Too Large` | The 50 MB limit; choose a lower quality or set up a local Bot API server |
| Bot does not respond | `docker compose logs -f bot`; any webhook is removed and polling is used |

## Notes

- Videos are kept in a temporary directory and deleted after sending; they do
  not accumulate on the server. Leftover files older than 6 hours are removed by
  a janitor every 30 minutes, and tracked temporary directories are removed on
  graceful shutdown.
- All stages are shown by editing one status message, not by sending a new
  message for each stage. Updates are merged so only the latest pending state is
  sent, identical text is skipped, and edits are serialized per chat with a
  minimum interval (2.5 seconds) to avoid Telegram flood limits. `RetryAfter`
  responses are honored with the server-provided delay; temporary network/server
  errors use a small bounded backoff. After a successful send, the status
  message is deleted; only the delivered media remains in the chat. On error,
  the message stays with an explanation and a retry button.
- Instagram photo/carousel posts are downloaded with gallery-dl; for posts that
  contain video, yt-dlp is used for quality selection.
- The `file_id` cache is stored in SQLite (`CACHE_DAYS`, 30 days by default).
  If Telegram rejects a file, the cache entry is removed automatically and the
  media is downloaded again. Old entries are pruned at startup, and admins can
  inspect or clear the cache with `/cache`.
- Live streams are not supported.
- Only use this bot for content you have the right to download and in
  compliance with the platforms' terms of service. You are responsible for how
  you use it.
