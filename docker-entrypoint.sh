#!/bin/sh
# Konteyner acilisinda yt-dlp / gallery-dl'yi gunceller.
# Platformlar sik degistigi icin "video indirilemiyor" hatalarinin buyuk kismi
# guncel olmayan yt-dlp'den kaynaklanir. Guncelleme basarisiz olursa mevcut
# surumle devam edilir, bot yine de acilir.
set -e

if [ "${AUTO_UPDATE_YTDLP:-1}" = "1" ]; then
    echo "[entrypoint] yt-dlp ve gallery-dl guncelleniyor..."
    if pip install --no-cache-dir --quiet --disable-pip-version-check \
        --timeout 10 --retries 1 --upgrade yt-dlp gallery-dl; then
        echo "[entrypoint] guncelleme tamam."
    else
        echo "[entrypoint] guncelleme basarisiz; mevcut surumler kullanilacak."
    fi
fi

exec "$@"
