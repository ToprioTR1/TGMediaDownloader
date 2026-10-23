"""gallery-dl alt sürecinde SSRF korumasını başlatan önyükleme.

Python, başlangıçta ``site`` modülü üzerinden ``sitecustomize`` arar. Bu dosya
yalnızca gallery-dl alt sürecinin PYTHONPATH'ine eklenir; ana bot sürecinde
etkisi yoktur. Kurulum başarısız olursa süreç bilinçli olarak sonlandırılır:
güvenlik koruması olmadan indirmeye devam etmek yerine iş başarısız olur.
"""

from __future__ import annotations

import os
import sys

if os.getenv("NETGUARD_ENFORCE") == "1":
    try:
        from bot.netguard import install_from_env

        install_from_env()
    except BaseException as exc:  # noqa: BLE001 - alt süreci güvenli kapat
        sys.stderr.write(f"netguard bootstrap failed: {exc}\n")
        raise SystemExit(97) from exc
