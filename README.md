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
- 🔁 Automatic one retry when a probe or download fails; if it still fails, the
  normal error and retry button are shown. The retry button edits the existing
  error message instead of sending a new one
- 📐 Correct Telegram aspect ratio: the real width, height, duration, and a small
  JPEG thumbnail are sent with every video, so larger videos are not shown as a
  square tile
- ⚙️ Per-user defaults via `/ayar`: target quality, audio-only links, captions
- 🔘 Quick actions under every video: `🎵 Get audio` · `🎚 Change quality` ·
  `📄 Send as file` · `🖼 Cover photo`, all served from the `file_id` cache
  without re-downloading when possible
- 📝 Links in media captions and in replied-to messages are processed too
- 🚦 Only URLs matched by a yt-dlp site extractor (or a site's official
  short-link host) are processed; generic web pages are ignored locally
  without a network probe or reply
- 🖼 Instagram photo / carousel and TikTok photo posts (via gallery-dl, with an
  Instagram embed fallback for public posts; sent as an album, `dosya` sends
  them as documents)
- 🚫 SSRF protection on every Python TCP `connect`: redirects, DNS rebinding,
  secondary media URLs, and cloud-metadata addresses are rejected; a host
  firewall config for container egress is included
- 🧵 Job queue: N concurrent downloads, per-user queue limit
- ⏱ Progress message (percentage, speed, ETA)
- 📉 Daily download limit and spam protection (cooldown)
- ⭐ Privilege promotion: `/promote @username` removes the user's daily limit
  (`/demote` restores it)
- 👤 User registration and download statistics (SQLite)
- 🛠 Admin panel: `/stats`, `/users`, `/cache`, `/broadcast`
- 📣 Broadcast reaches registered users **and groups** where the bot is present
- 🧹 Bot-owned temporary downloads (`dl-*`) are cleaned periodically and on graceful shutdown
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
| `UPLOAD_TIMEOUT_SEC` | `600` | Timeout for media upload requests (raise it for slow links or 2 GB local Bot API) |
| `MAX_HEIGHT` | `0` | Reject resolutions above this; links with no fitting format are rejected (0 = unlimited) |
| `MAX_DURATION_SEC` | `1800` | Maximum video duration (0 = unlimited) |
| `MAX_QUEUE_PER_USER` | `3` | Pending jobs per user |
| `DAILY_LIMIT` | `20` | Daily download limit, resets at local midnight (0 = unlimited) |
| `COOLDOWN_SEC` | `10` | Minimum seconds between two requests |
| `QUALITY_INSTAGRAM` | `1280` | Target for Instagram links without a quality |
| `QUALITY_TIKTOK` | `1012` | Target for TikTok links without a quality |
| `QUALITY_YOUTUBE` | `720` | Target for YouTube links without a quality |
| `QUALITY_DEFAULT` | `720` | Target for other platforms |
| `CACHE_DAYS` | `30` | `file_id` cache lifetime in days (0 = disabled) |
| `GALLERY_ENABLED` | `1` | Instagram/TikTok photo support (gallery-dl) |
| `GALLERY_LIMIT` | `10` | Maximum media per post (Telegram album limit is 10) |
| `AUTO_UPDATE_YTDLP` | `1` | Update yt-dlp + gallery-dl on Docker startup |
| `COOKIES_FILE` | — | Path to `cookies.txt` (for sites that require cookies) |
| `TELEGRAM_API_URL` | empty | Local Bot API server URL (its host is allowlisted automatically) |
| `PROXY_URL` | empty | HTTP(S)/SOCKS proxy for downloads (not used unless `NETGUARD_ALLOW_PROXY=1`) |
| `NETGUARD_ALLOW` | empty | Extra trusted `host:port` endpoints for the SSRF guard |
| `NETGUARD_ALLOW_PROXY` | `0` | Use `PROXY_URL`; only when the proxy blocks private/metadata egress itself |
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

## Network Egress / SSRF Protection

User-supplied URLs are untrusted. The bot checks every outbound Python TCP
connection, not only the first URL string:

- The destination IP is checked at `connect()` time. A redirect, a second
  media/segment request, or a DNS answer that changes after the first lookup is
  still rejected when it reaches a private, loopback, link-local, CGNAT,
  multicast, reserved, documentation, or cloud-metadata address.
- DNS validation failure is treated as a rejection (fail closed), never as
  permission to continue.
- yt-dlp is forced to use its native HLS downloader; gallery-dl runs with
  `--config-ignore` and receives the same guard in its subprocess through a
  `sitecustomize` bootstrap.
- The host in `TELEGRAM_API_URL` (including a local Bot API server) is
  allowlisted automatically. Add other trusted endpoints with `NETGUARD_ALLOW`.

The in-process guard cannot see traffic from separate native processes such as
`ffmpeg`, and an enabled proxy hides the final destination. **Apply the host
firewall to cover container egress:**

```bash
sudo nft -f deploy/ssrf-firewall.nft
sudo nft list table inet ssrf_egress
```

`docker-compose.yml` places the bot on `172.28.240.0/24` at `172.28.240.3`; the
local Bot API container is `172.28.240.2:8081`. The nftables rules allow only
that API endpoint and drop new connections from the bot to private/metadata
ranges for **all** processes, including `ffmpeg`. If this subnet conflicts with
your VPS, change it in both compose files and in the firewall file. Keep IPv6
disabled on this Docker network; if you enable it, also add the commented `ip6`
rules, otherwise IPv4-mapped IPv6 addresses could bypass the IPv4 rules.

Persist the rules according to your distribution, for example:

```bash
sudo sh -c 'nft list ruleset > /etc/nftables.conf'
sudo systemctl enable --now nftables
```

If the local Bot API runs on the host (`host.docker.internal:8081`), enable the
commented allow rule in `deploy/ssrf-firewall.nft` and add
`NETGUARD_ALLOW=172.28.240.1:8081` to `.env`.

If `PROXY_URL` or a standard `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY`
environment variable is set, the guard cannot validate the final destination.
Downloads therefore do **not** use the proxy by default and connect directly,
where every target is checked. Set `NETGUARD_ALLOW_PROXY=1` only if the proxy
itself blocks private/metadata egress, and apply equivalent rules at the proxy.
Do not use a proxy as an SSRF boundary.

If that proxy is on a private address (for example a host-local proxy at
`172.28.240.1:3128`), add a narrow allow rule before the private-range drop;
otherwise the host firewall blocks the proxy itself:

```nft
ip saddr 172.28.240.3 ip daddr 172.28.240.1 tcp dport 3128 accept
```

Keep the rule as narrow as possible. The proxy must enforce its own egress
policy; the bot's socket guard only sees the proxy connection.

Without Docker, apply an equivalent nftables `output` rule for the UID that runs
`python -m bot` (replace `1000`):

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

Also add equivalent `ip6` rules (or disable IPv6) and allow the local Bot API
port if you use one.

Remaining limitation: the host firewall is the only layer that covers native
libraries/subprocesses and proxy-tunneled destinations. Without it, an extractor
that invokes `ffmpeg` (or another native downloader) and a proxy can still reach
internal addresses. The in-app guard alone does not claim to cover those paths.
Host `forward` rules also cannot see the container's own loopback or `AF_UNIX`
sockets; the in-process guard blocks Python loopback, and the bot image does not
run local services. UDP/QUIC is covered by the host firewall only for private
destinations, not by the Python socket hook.

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
| `/stats` | admin | User, group and download statistics |
| `/users` | admin | Last 10 users (`⭐` = unlimited access) |
| `/cache` | admin | Cache count/size and cleanup buttons |
| `/cache temizle 7` | admin | Delete cache entries older than 7 days (minimum 1) |
| `/cache tum` | admin | Ask for confirmation, then clear the entire cache |
| `/broadcast <message>` | admin | Send an announcement to all users and recorded groups |
| `/promote @username` | admin | Grant unlimited daily downloads (replying to a message also works) |
| `/demote @username` | admin | Revoke unlimited access |

`/broadcast` also sends to groups where the bot is present. Groups are recorded
when the bot is added or sees activity in them; a group is removed from the list
when the bot leaves or is kicked.

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
├── gallery.py       # gallery-dl: Instagram/TikTok photo download
├── media.py         # ffprobe metadata + Telegram JPEG thumbnail
├── queue.py         # job queue, workers, progress, cache, Telegram delivery
├── keyboards.py     # inline keyboards (video actions, admin panel)
├── middlewares.py   # user registration
├── utils.py         # URL/quality parsing, cache key, formatting
└── handlers/
    ├── start.py     # /start /help /ayar /me
    ├── download.py  # link/caption/reply → quality + size resolution → queue
    └── admin.py     # admin commands (including promote/demote)
```

Flow: `handlers/download.py` → `downloader.probe()` →
`handlers/download.py:_resolve_preset()` (nearest quality + size guard) →
`queue.JobQueue.submit()` → worker `downloader.download()` →
`media.inspect_video()` (dimensions + thumbnail) → `bot.send_video()` →
`database.cache_put()` + `database.record_download()`.
For Instagram and TikTok photo posts, `gallery.py` takes over if yt-dlp
cannot handle the post.

## Tests

`tests/smoke_test.py` exercises the end-to-end flow with a fake Bot object (it
downloads a small TikTok video from the real network and checks default/requested
quality, MP3, document delivery, the file_id cache, in-flight deduplication,
retry ownership, daily-limit enforcement, caption and reply support, the size
guard, video width/height/thumbnail delivery, photo delivery via gallery-dl
(including document mode), `/ayar`, temporary-file cleanup, SSRF/private-address
blocking, status-message coalescing, `RetryAfter`, transient network handling,
successful-delivery protection, and the DB/admin/cache steps):

```bash
pip install -r requirements.txt
$env:PYTHONPATH="." ; python tests/smoke_test.py    # PowerShell
PYTHONPATH=. python tests/smoke_test.py             # bash

python -m unittest tests.test_netguard -v           # SSRF/connect regression tests
python -m unittest tests.test_url_support -v        # yt-dlp URL matching tests
python -m unittest tests.test_instagram_embed -v    # Instagram embed parser tests
python -m unittest tests.test_media -v              # ffprobe/thumbnail tests (ffmpeg required)
python -m unittest tests.test_retry -v              # one-retry behavior tests
python -m unittest tests.test_database_migration -v # old cache migration tests
python -m unittest tests.test_qol -v                # action buttons
```

The smoke test creates its own data under `.tmp/` and does not make real requests
to Telegram. The netguard tests use only local sockets (loopback allowlist,
redirect, DNS rebinding, fail-closed DNS, and the gallery-dl subprocess
bootstrap). The URL support tests use only local yt-dlp extractor matching and
make no network requests.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Bu içerik için giriş gerekiyor` (login required) | Add `data/cookies.txt`, then `docker compose restart bot` |
| `HTTP Error 403` / `429` | Refresh cookies, wait a few minutes; the VPS IP may be blocked |
| `Sign in to confirm you're not a bot` (YouTube) | Add cookies or configure `PROXY_URL` |
| `ffmpeg bulunamadı` (ffmpeg not found) | It is included in the Docker image; locally run `apt install ffmpeg` / `winget install Gyan.FFmpeg` |
| Download fails with `güvenlik politikası bu hedefi engelledi` | The target is private/metadata, DNS failed, or `PROXY_URL` is set without `NETGUARD_ALLOW_PROXY=1` (the proxy is bypassed; the direct connection may fail); check `docker compose logs -f bot` |
| Downloads work only after disabling the firewall | Re-apply `deploy/ssrf-firewall.nft`; make sure the bot still uses `172.28.240.3` and the local API allow rule matches |
| One site stops working while others work | `docker compose restart bot` (yt-dlp updates on startup) or `docker compose build --no-cache` |
| Instagram/TikTok photos are not downloading | Is `gallery-dl` installed? (`GALLERY_ENABLED=1`); private posts require `cookies.txt` |
| `Request Entity Too Large` | The 50 MB limit; choose a lower quality or set up a local Bot API server |
| Bot does not respond | `docker compose logs -f bot`; any webhook is removed and polling is used |

## Notes

- Videos are kept in a `dl-*` temporary directory and deleted after sending; they
  do not accumulate on the server. Leftover `dl-*` entries older than 6 hours are
  removed by a janitor every 30 minutes; other files in `DOWNLOAD_DIR` are left
  untouched. Tracked temporary directories are removed on graceful shutdown.
- The daily download limit is enforced both before probing and when a job enters
  the queue, and it resets at local midnight.
- All stages are shown by editing one status message, not by sending a new
  message for each stage. Updates are merged so only the latest pending state is
  sent, identical text is skipped, and edits are serialized per chat with a
  minimum interval (2.5 seconds) to avoid Telegram flood limits. `RetryAfter`
  responses are honored with the server-provided delay; temporary network/server
  errors use a small bounded backoff. After a successful send, the status
  message is deleted; only the delivered media remains in the chat. On error,
  the message stays with an explanation and a retry button.
- Probes, video downloads, and gallery downloads are attempted twice
  automatically. The second failure shows the normal error and the manual retry
  button. Unsupported URLs are still ignored silently without a retry.
- Media uploads use `UPLOAD_TIMEOUT_SEC` (600 seconds by default). If Telegram
  does not answer in time, the bot does **not** resend automatically, because the
  file may already have arrived and a retry could duplicate it. Instead it shows
  a "Gönderim yanıtı alınamadı" warning. Telegram flood (`RetryAfter`) responses
  are retried once as before.
- Videos are sent with `width`, `height`, `duration`, and a JPEG thumbnail
  generated by ffmpeg. This keeps the correct aspect ratio in Telegram,
  especially for files larger than about 10 MB. If ffprobe/ffmpeg is unavailable,
  the bot falls back to the previous behavior.
- Instagram photo/carousel and TikTok photo posts are downloaded with
  gallery-dl; for posts that contain video, yt-dlp is used for quality
  selection. If Instagram redirects gallery-dl to its login page, public
  photo/carousel posts are read from the Instagram embed page instead; private
  posts still require `cookies.txt`.
- The `file_id` cache is stored in SQLite (`CACHE_DAYS`, 30 days by default).
  If Telegram rejects a file, the cache entry is removed automatically and the
  media is downloaded again. Old entries are pruned at startup, and admins can
  inspect, prune, or clear the cache with `/cache`; clearing all entries requires
  explicit confirmation. Video entries created before the aspect-ratio update
  have no dimensions; they are removed once at startup and downloaded again so
  Telegram receives the correct geometry. Audio and document entries are kept.
- Live streams are not supported.
- Only use this bot for content you have the right to download and in
  compliance with the platforms' terms of service. You are responsible for how
  you use it.
