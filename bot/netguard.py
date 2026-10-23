"""Süreç genelinde giden TCP bağlantılarını denetleyen SSRF koruması.

Kullanıcıdan gelen URL'yi bir kez DNS ile denetlemek yeterli değildir:
yt-dlp/gallery-dl yönlendirmeleri izler, aynı alan adını yeniden çözer ve
ikincil medya URL'lerine bağlanır. Bu modül, her TCP ``connect`` çağrısında
gerçek hedef IP'yi denetler. Böylece:

* DNS rebinding (denetimden sonra adres değişmesi),
* HTTP yönlendirmeleri,
* aynı indirmenin ikincil medya/parça istekleri,
* sayısal/localhost benzeri alan adları

aynı kapıdan geçer ve özel/loopback/link-local/rezerve adresler reddedilir.

Kapsam ve sınırlar
------------------
Bu koruma Python ağ yığınını (socket, urllib, requests, yt-dlp, gallery-dl)
kapsar. ffmpeg gibi ayrı süreçlerle yapılan indirmeler bu kancayı atlar. Bu
nedenle üretimde ek olarak host/konteyner güvenlik duvarı kuralları
uygulanmalıdır; ``deploy/ssrf-firewall.nft`` ve README'deki "Network egress"
bölümüne bakın.

Proxy kullanılıyorsa hedefi gören taraf proxy'dir. Güvenli varsayılan olarak
indiriciler proxy'yi kullanmaz; proxy'yi kullanmak için
``NETGUARD_ALLOW_PROXY=1`` gerekir. Bu durumda proxy'nin kendi çıkış
kurallarının özel/metadata ağlarını engellemesi operatörün sorumluluğundadır.
Proxy izinli değilken standart ``HTTP_PROXY``/``HTTPS_PROXY``/``ALL_PROXY``
değişkenleri süreçten ve indirici alt süreçlerinden kaldırılır.
"""

from __future__ import annotations

import errno
import ipaddress
import logging
import os
import re
import socket
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent
_BOOT_DIR = Path(__file__).resolve().parent / "netguard_boot"


class BlockedAddressError(OSError):
    """Hedef adres güvenlik politikası tarafından reddedildi."""


# IANA özel kullanım / dokümantasyon / metadata aralıkları. ``is_global``
# çoğunu kapsar; liste yeni Python sürümlerindeki farklara ve bilinen bulut
# metadata adreslerine karşı savunma katmanıdır.
_V4_DENY = tuple(
    ipaddress.ip_network(net)
    for net in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "100.64.0.0/10",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "168.63.129.16/32",   # Azure WireServer / DNS / health probe
        "192.0.0.0/24",       # Oracle metadata 192.0.0.192 dahil
        "192.0.2.0/24",
        "192.88.99.0/24",
        "192.168.0.0/16",
        "198.18.0.0/15",
        "198.51.100.0/24",
        "203.0.113.0/24",
        "224.0.0.0/4",
        "240.0.0.0/4",
        "255.255.255.255/32",
    )
)
_V6_DENY = tuple(
    ipaddress.ip_network(net)
    for net in (
        "::/128",
        "::1/128",
        "::/96",              # kullanımdan kalkan IPv4-uyumlu IPv6
        "100::/64",
        "2001::/23",          # Teredo/benchmark/ORCHID/AMT/AS112
        "2001:db8::/32",
        "2002::/16",          # 6to4 (özel IPv4 gömebilir, kullanımdan kalktı)
        "3fff::/20",          # dokümantasyon
        "5f00::/16",          # segment routing SID
        "64:ff9b:1::/48",     # yerel NAT64
        "fc00::/7",
        "fe80::/10",
        "ff00::/8",
    )
)
_NAT64 = ipaddress.ip_network("64:ff9b::/96")

_ALLOWED: set[tuple[str, int | None]] = set()
_ALLOW_SPECS: list[str] = []
_ALLOW_PROXY = False
_ACTIVE = False
_PATCHED = False

_PROXY_ENV_NAMES = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)

_ORIG_CONNECT = socket.socket.connect
_ORIG_CONNECT_EX = socket.socket.connect_ex
_ORIG_GETADDRINFO = socket.getaddrinfo
_ORIG_SOCKETPAIR = socket.socketpair
_NUMERIC_HOST_RE = re.compile(r"^(?:0x[0-9a-f]+|[0-9.]+)$", re.IGNORECASE)
_LOCAL = threading.local()

_RESOLVE_CACHE: dict[str, tuple[float, frozenset[str]]] = {}
_RESOLVE_TTL = 30.0
_LOCK = threading.RLock()


# --------------------------------------------------------------------- adresler


def _normalize(value: object) -> tuple[str, ipaddress._BaseAddress] | None:
    """IP metnini kanonik hale getirir; IPv4-mapped IPv6'yı IPv4'e indirger."""
    text = str(value).strip().split("%", 1)[0]
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return None
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return str(ip), ip


def _embedded_v4(ip: ipaddress._BaseAddress) -> ipaddress.IPv4Address | None:
    """6to4/NAT64 içine gömülü IPv4 adresini (varsa) döndürür."""
    if ip.version != 6:
        return None
    if ip in _NAT64:
        return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    # 2002::/16 zaten _V6_DENY içinde; yine de gömülü adresi raporlayalım.
    if ip in ipaddress.ip_network("2002::/16"):
        return ipaddress.IPv4Address((int(ip) >> 80) & 0xFFFFFFFF)
    return None


def blocked_reason(value: object) -> str | None:
    """Adres genel internete açık değilse kısa bir neden döndürür."""
    normalized = _normalize(value)
    if normalized is None:
        return "geçersiz IP"
    text, ip = normalized

    embedded = _embedded_v4(ip)
    if embedded is not None:
        if not is_public_ip(str(embedded)):
            return f"gömülü özel IPv4 ({embedded})"
        if ip in _NAT64:
            # NAT64 taşıyıcısı ``is_reserved`` olarak işaretlenebilir; gömülü
            # IPv4 genel internete açıksa hedef de geneldir.
            return None

    if ip.is_loopback:
        return "loopback"
    if ip.is_link_local:
        return "link-local"
    if ip.is_private:
        return "özel ağ"
    if ip.is_multicast:
        return "multicast"
    if ip.is_reserved:
        return "rezerve"
    if ip.is_unspecified:
        return "belirtilmemiş"
    if not ip.is_global:
        return "genel olmayan adres"
    if ip.version == 4 and any(ip in network for network in _V4_DENY):
        return "özel amaçlı IPv4 ağı"
    if ip.version == 6 and any(ip in network for network in _V6_DENY):
        return "özel amaçlı IPv6 ağı"
    # Son bir kez normalize edilmiş metin üzerinden kontrol (tip tutarlılığı).
    if not is_public_ip(text):
        return "genel olmayan adres"
    return None


def is_public_ip(value: object) -> bool:
    """Yalnızca genel internete açık, özel amaçlı olmayan adreslerde True."""
    normalized = _normalize(value)
    if normalized is None:
        return False
    _, ip = normalized

    embedded = _embedded_v4(ip)
    if embedded is not None:
        if not is_public_ip(str(embedded)):
            return False
        if ip in _NAT64:
            return True

    if (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or not ip.is_global
    ):
        return False
    if ip.version == 4 and any(ip in network for network in _V4_DENY):
        return False
    if ip.version == 6 and any(ip in network for network in _V6_DENY):
        return False
    return True


# --------------------------------------------------------------- izin listesi


def _normalize_host(host: str) -> str:
    host = host.strip().rstrip(".").lower()
    normalized = _normalize(host)
    return normalized[0] if normalized is not None else host


def _parse_endpoint(spec: str) -> tuple[str, int | None] | None:
    """``host[:port]`` veya ``scheme://host[:port]`` biçimini ayrıştırır."""
    text = str(spec).strip()
    if not text:
        return None

    if "://" in text:
        try:
            parsed = urlsplit(text)
        except ValueError:
            return None
        host = parsed.hostname
        if not host:
            return None
        try:
            port = parsed.port
        except ValueError:
            return None
        if port is None:
            scheme = parsed.scheme.lower()
            if scheme in {"https", "wss"}:
                port = 443
            elif scheme in {"socks4", "socks4a", "socks5", "socks5h"}:
                port = 1080
            else:
                port = 80
        return _normalize_host(host), port

    if text.startswith("["):
        host, separator, rest = text[1:].partition("]")
        if not separator:
            return None
        port: int | None = None
        if rest:
            if not rest.startswith(":"):
                return None
            raw_port = rest[1:]
            if not raw_port.isdigit():
                return None
            port = int(raw_port)
    else:
        host, separator, raw_port = text.rpartition(":")
        if separator and raw_port.isdigit():
            port = int(raw_port)
        else:
            host = text
            port = None

    if not host:
        return None
    if port is not None and not (0 < port < 65536):
        return None
    return _normalize_host(host), port


def _endpoint_spec(endpoint: tuple[str, int | None]) -> str:
    """İzin hedefini kimlik bilgisi içermeyen ``host[:port]`` biçiminde yazar."""
    host, port = endpoint
    if port is None:
        return f"[{host}]" if ":" in host else host
    if ":" in host:
        return f"[{host}]:{port}"
    return f"{host}:{port}"


def _resolve_host(host: str) -> frozenset[str]:
    now = time.monotonic()
    with _LOCK:
        cached = _RESOLVE_CACHE.get(host)
        if cached is not None:
            # Başarısız/boş sonucu kısa süre önbellekle: geçici DNS hatası
            # yüzünden yerel Bot API gibi izinli bir uç 30 sn boyunca
            # engellenmesin.
            ttl = _RESOLVE_TTL if cached[1] else 5.0
            if now - cached[0] < ttl:
                return cached[1]

    addresses: set[str] = set()
    try:
        infos = _ORIG_GETADDRINFO(host, None, 0, socket.SOCK_STREAM)
    except OSError as exc:
        # İzin listesindeki bir ad çözülemiyorsa o kural eşleşmez; bağlantı
        # genel adres değilse yine reddedilir (fail closed).
        log.warning("NETGUARD: izin listesi DNS çözümü başarısız (%s): %s", host, exc)
    else:
        for *_, sockaddr in infos:
            normalized = _normalize(sockaddr[0])
            if normalized is not None:
                addresses.add(normalized[0])

    result = frozenset(addresses)
    with _LOCK:
        _RESOLVE_CACHE[host] = (now, result)
    return result


def _endpoint_matches(ip_text: str, port: int, endpoint: tuple[str, int | None]) -> bool:
    host, allowed_port = endpoint
    if allowed_port is not None and allowed_port != port:
        return False
    if _normalize(host) is not None:
        return host == ip_text
    return ip_text in _resolve_host(host)


def _check_peer(ip_text: str, port: int) -> str | None:
    """Bağlantı hedefi için ``None`` (izinli) veya engel nedenini döndürür."""
    normalized = _normalize(ip_text)
    if normalized is None:
        return "geçersiz IP"
    canonical, _ = normalized

    with _LOCK:
        allowed = tuple(_ALLOWED)

    for endpoint in allowed:
        if _endpoint_matches(canonical, port, endpoint):
            return None
    return blocked_reason(canonical)


# -------------------------------------------------------------------- kancalar


def _connect_candidates(sock: socket.socket, address: Any) -> list[Any]:
    host = address[0]
    if isinstance(host, bytes):
        try:
            host = host.decode("idna")
        except UnicodeDecodeError:
            host = host.decode("ascii", "replace")
    text_host = str(host)

    if _normalize(text_host) is not None:
        return [address]

    # 127.1, 2130706433, 0x7f000001 gibi standart dışı sayısal biçimleri
    # çözücüye bırakmadan reddet. Bazı platformlarda bunlar loopback'e
    # çözülebilir; bazılarında ise gaierror oluşur. İkisi de güvenli değildir,
    # bu yüzden açıkça engelliyoruz.
    if _NUMERIC_HOST_RE.fullmatch(text_host):
        return []

    # Kütüphane çözülmemiş bir adı doğrudan connect'e verdiyse burada çözüp
    # yalnızca doğrulanmış IP'ye bağlanırız. Böylece C katmanında ikinci bir
    # DNS çözümü/rebinding mümkün olmaz.
    infos = _ORIG_GETADDRINFO(
        text_host,
        address[1],
        sock.family,
        socket.SOCK_STREAM,
        sock.proto,
    )
    return [sockaddr for *_, sockaddr in infos]


def _bypass_active() -> bool:
    return getattr(_LOCAL, "bypass", 0) > 0


def _socketpair(*args: Any, **kwargs: Any) -> tuple[socket.socket, socket.socket]:
    """Windows'ta socketpair loopback TCP kullanır; bu iç kullanımı muaf tut."""
    _LOCAL.bypass = getattr(_LOCAL, "bypass", 0) + 1
    try:
        return _ORIG_SOCKETPAIR(*args, **kwargs)
    finally:
        _LOCAL.bypass -= 1


def _guard_active(sock: socket.socket) -> bool:
    if not _ACTIVE or _bypass_active():
        return False
    if sock.family not in (socket.AF_INET, socket.AF_INET6):
        return False
    # UDP/DNS ve AF_UNIX trafiği kapsam dışı; indirmeler TCP'dir.
    return (sock.type & 0xF) == socket.SOCK_STREAM


def _connect(sock: socket.socket, address: Any) -> None:
    if not _guard_active(sock):
        return _ORIG_CONNECT(sock, address)

    candidates = _connect_candidates(sock, address)
    blocked: list[str] = []
    for sockaddr in candidates:
        ip_text = str(sockaddr[0]).split("%", 1)[0]
        port = int(sockaddr[1])
        reason = _check_peer(ip_text, port)
        if reason is None:
            return _ORIG_CONNECT(sock, sockaddr)
        blocked.append(f"{ip_text}:{port} ({reason})")

    detail = ", ".join(blocked) if blocked else "çözümlenen adres yok"
    log.warning("NETGUARD: bağlantı engellendi: %s -> %s", address[0], detail)
    raise BlockedAddressError(
        errno.EACCES,
        f"güvenlik politikası bu hedefi engelledi: {address[0]}:{address[1]}",
    )


def _connect_ex(sock: socket.socket, address: Any) -> int:
    if not _guard_active(sock):
        return _ORIG_CONNECT_EX(sock, address)

    candidates = _connect_candidates(sock, address)
    blocked: list[str] = []
    for sockaddr in candidates:
        ip_text = str(sockaddr[0]).split("%", 1)[0]
        port = int(sockaddr[1])
        reason = _check_peer(ip_text, port)
        if reason is None:
            return _ORIG_CONNECT_EX(sock, sockaddr)
        blocked.append(f"{ip_text}:{port} ({reason})")

    detail = ", ".join(blocked) if blocked else "çözümlenen adres yok"
    log.warning("NETGUARD: bağlantı engellendi: %s -> %s", address[0], detail)
    return errno.EACCES


def _ensure_patched() -> None:
    global _ORIG_CONNECT, _ORIG_CONNECT_EX, _ORIG_GETADDRINFO
    global _ORIG_SOCKETPAIR, _PATCHED
    if _PATCHED:
        return
    # Testlerin/başka bir kütüphanenin daha önce kurduğu kancayı koruyarak
    # yalnızca kendi sarmalayıcımızı ekleriz.
    if socket.socket.connect is not _connect:
        _ORIG_CONNECT = socket.socket.connect
    if socket.socket.connect_ex is not _connect_ex:
        _ORIG_CONNECT_EX = socket.socket.connect_ex
    if socket.socketpair is not _socketpair:
        _ORIG_SOCKETPAIR = socket.socketpair
    _ORIG_GETADDRINFO = socket.getaddrinfo
    socket.socket.connect = _connect  # type: ignore[method-assign]
    socket.socket.connect_ex = _connect_ex  # type: ignore[method-assign]
    socket.socketpair = _socketpair  # type: ignore[assignment]
    _PATCHED = True


def install(
    *,
    allow: Iterable[str] = (),
    allow_proxy: bool = False,
    proxy: str | None = None,
) -> None:
    """Korumayı kurar (veya izin listesini günceller).

    ``allow_proxy=False`` iken indiriciler proxy kullanmaz; ``proxy`` yalnızca
    yapılandırmanın varlığını bildirmek için dikkate alınır. ``allow_proxy=True``
    ise proxy uçları izin listesine eklenir; hedef denetimi proxy'nin çıkış
    kurallarına kalır.
    """
    global _ACTIVE, _ALLOW_PROXY

    endpoints: list[tuple[str, int | None]] = []
    specs: list[str] = []
    for raw in allow:
        text = str(raw).strip()
        if not text:
            continue
        endpoint = _parse_endpoint(text)
        if endpoint is None:
            log.warning("NETGUARD: geçersiz izin hedefi yok sayıldı: %s", text)
            continue
        endpoints.append(endpoint)
        specs.append(text)

    proxy_specs = [proxy] if proxy else []
    proxy_specs.extend(os.getenv(name, "").strip() for name in _PROXY_ENV_NAMES)
    proxy_specs = [spec for spec in proxy_specs if spec]
    if proxy_specs:
        if allow_proxy:
            for spec in proxy_specs:
                endpoint = _parse_endpoint(spec)
                if endpoint is None:
                    log.warning(
                        "NETGUARD: geçersiz proxy adresi yok sayıldı: %s", spec
                    )
                    continue
                if endpoint not in endpoints:
                    endpoints.append(endpoint)
                allow_spec = _endpoint_spec(endpoint)
                if allow_spec not in specs:
                    specs.append(allow_spec)
            log.warning(
                "NETGUARD: proxy açıkça izinli. Hedef denetimi artık proxy'nin "
                "çıkış kurallarına bağlıdır; proxy'nin özel/metadata ağlarına "
                "erişimini kapatın."
            )
        else:
            log.warning(
                "NETGUARD: proxy yapılandırılmış ancak NETGUARD_ALLOW_PROXY=1 "
                "olmadığı için indirmelerde kullanılmayacak; doğrudan bağlantı "
                "denenecek. Proxy gerekliyse çıkış kurallarını kısıtlayıp "
                "NETGUARD_ALLOW_PROXY=1 yapın."
            )

    with _LOCK:
        _ensure_patched()
        _ACTIVE = True
        _ALLOWED.clear()
        _ALLOWED.update(endpoints)
        _ALLOW_SPECS[:] = specs
        _ALLOW_PROXY = allow_proxy
        _RESOLVE_CACHE.clear()


def install_for_settings(settings: Any) -> None:
    """Ayarlardan Telegram API ve proxy uçlarını dikkate alarak kurar."""
    allow = _env_list("NETGUARD_ALLOW")
    api_url = getattr(settings, "api_url", None)
    if api_url:
        allow.append(str(api_url))
    allow_proxy = _env_bool("NETGUARD_ALLOW_PROXY", False)
    install(
        allow=allow,
        allow_proxy=allow_proxy,
        proxy=getattr(settings, "proxy", None),
    )
    if not allow_proxy:
        _strip_proxy_env()


def ensure_for_settings(settings: Any) -> None:
    if not is_installed():
        install_for_settings(settings)


def install_from_env() -> None:
    """Alt süreçte (sitecustomize) ortam değişkenlerinden kurar."""
    allow_proxy = _env_bool("NETGUARD_ALLOW_PROXY", False)
    install(
        allow=_env_list("NETGUARD_ALLOW"),
        allow_proxy=allow_proxy,
    )
    if not allow_proxy:
        _strip_proxy_env()


def proxy_allowed() -> bool:
    """İndiricilerin proxy kullanmasına açıkça izin verilmiş mi?"""
    return _ALLOW_PROXY


def is_installed() -> bool:
    return _PATCHED and _ACTIVE


def uninstall() -> None:
    """Kancaları kaldırır (testler ve gömülü kullanım için)."""
    global _ACTIVE, _ALLOW_PROXY, _PATCHED
    with _LOCK:
        if _PATCHED:
            socket.socket.connect = _ORIG_CONNECT  # type: ignore[method-assign]
            socket.socket.connect_ex = _ORIG_CONNECT_EX  # type: ignore[method-assign]
            socket.socketpair = _ORIG_SOCKETPAIR  # type: ignore[assignment]
            _PATCHED = False
        _ACTIVE = False
        _ALLOWED.clear()
        _ALLOW_SPECS.clear()
        _ALLOW_PROXY = False
        _RESOLVE_CACHE.clear()


def _snapshot() -> tuple[
    bool,
    bool,
    set[tuple[str, int | None]],
    list[str],
    bool,
]:
    with _LOCK:
        return (
            _PATCHED,
            _ACTIVE,
            set(_ALLOWED),
            list(_ALLOW_SPECS),
            _ALLOW_PROXY,
        )


def _restore(
    previous: tuple[
        bool,
        bool,
        set[tuple[str, int | None]],
        list[str],
        bool,
    ],
) -> None:
    global _ACTIVE, _ALLOW_PROXY
    patched, active, allowed, specs, allow_proxy = previous
    if not patched:
        uninstall()
        return
    with _LOCK:
        _ACTIVE = active
        _ALLOWED.clear()
        _ALLOWED.update(allowed)
        _ALLOW_SPECS[:] = specs
        _ALLOW_PROXY = allow_proxy
        _RESOLVE_CACHE.clear()


@contextmanager
def active(
    *,
    allow: Iterable[str] = (),
    allow_proxy: bool = False,
    proxy: str | None = None,
) -> Iterator[None]:
    """Testler için geçici olarak korumayı etkinleştirir."""
    previous = _snapshot()
    install(allow=allow, allow_proxy=allow_proxy, proxy=proxy)
    try:
        yield
    finally:
        _restore(previous)


# ----------------------------------------------------------------- alt süreç


def child_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """gallery-dl alt sürecine korumayı taşıyan ortamı üretir."""
    env = dict(os.environ if base is None else base)
    pythonpath = [str(_BOOT_DIR), str(_ROOT)]
    existing = env.get("PYTHONPATH")
    if existing:
        pythonpath.append(existing)
    env["PYTHONPATH"] = os.pathsep.join(pythonpath)
    env["NETGUARD_ENFORCE"] = "1"
    with _LOCK:
        env["NETGUARD_ALLOW"] = ",".join(_ALLOW_SPECS)
        env["NETGUARD_ALLOW_PROXY"] = "1" if _ALLOW_PROXY else "0"
        allowed = _ALLOW_PROXY
    if not allowed:
        # İndirici alt süreçte de proxy kullanmasın. Aksi halde hedefi proxy
        # görür ve soket katmanındaki IP denetimi atlanır. Windows'ta anahtar
        # eşleşmesi büyük/küçük harfe duyarlı olabileceği için normalize et.
        proxy_names = {name.upper() for name in _PROXY_ENV_NAMES}
        for key in list(env):
            if key.upper() in proxy_names:
                env.pop(key, None)
    return env


# -------------------------------------------------------------------- yardımcı


def _strip_proxy_env() -> None:
    """Proxy izinli değilken süreçteki standart proxy değişkenlerini kaldır."""
    proxy_names = {name.upper() for name in _PROXY_ENV_NAMES}
    for key in list(os.environ):
        if key.upper() in proxy_names:
            os.environ.pop(key, None)


def _env_list(name: str) -> list[str]:
    return [part.strip() for part in os.getenv(name, "").split(",") if part.strip()]


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}
