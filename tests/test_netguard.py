r"""SSRF korumasının bağlantı katmanı regresyon testleri.

Çalıştırma:
    .\.venv\Scripts\python.exe -m unittest tests.test_netguard -v
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ["BOT_TOKEN"] = "123456:TESTTESTTESTTESTTESTTESTTESTTESTTES"
os.environ["ADMIN_IDS"] = "42"
os.environ["COOKIES_FILE"] = ""

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot import netguard  # noqa: E402
from bot.config import Settings  # noqa: E402
from bot.downloader import Downloader  # noqa: E402
from bot.gallery import GalleryDownloader  # noqa: E402
from bot.utils import is_blocked_url  # noqa: E402


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header(
                "Location", f"http://127.0.0.1:{self.server.other_port}/secret"
            )
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = b"ok"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        return


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    other_port = 0


class NetguardTests(unittest.TestCase):
    def tearDown(self) -> None:
        netguard.uninstall()

    # ------------------------------------------------------------------ adres

    def test_public_and_blocked_address_classification(self) -> None:
        blocked = (
            "127.0.0.1",
            "10.1.2.3",
            "172.16.5.5",
            "192.168.1.1",
            "169.254.169.254",
            "168.63.129.16",
            "100.100.100.200",
            "192.0.0.192",
            "0.0.0.0",
            "224.0.0.1",
            "255.255.255.255",
            "::1",
            "fe80::1",
            "fd00:ec2::254",
            "2001:db8::1",
            "::ffff:127.0.0.1",
            "::127.0.0.1",
            "64:ff9b::7f00:1",
            "64:ff9b::a83f:8110",
            "2002:7f00:1::",
        )
        for value in blocked:
            with self.subTest(value=value):
                self.assertFalse(netguard.is_public_ip(value))
                self.assertIsNotNone(netguard.blocked_reason(value))

        for value in ("1.1.1.1", "8.8.8.8", "2606:4700:4700::1111", "64:ff9b::808:808"):
            with self.subTest(value=value):
                self.assertTrue(netguard.is_public_ip(value))
                self.assertIsNone(netguard.blocked_reason(value))

    # --------------------------------------------------------------- connect

    def test_loopback_and_localhost_are_blocked_at_connect(self) -> None:
        with netguard.active():
            for address in (
                ("127.0.0.1", 9),
                ("localhost", 9),
                ("::ffff:127.0.0.1", 9),
                ("169.254.169.254", 80),
            ):
                with self.subTest(address=address):
                    with self.assertRaises(netguard.BlockedAddressError):
                        socket.create_connection(address, timeout=1)

            # Standart dışı sayısal biçim: create_connection bunu kendi
            # getaddrinfo çağrısında reddedebilir; doğrudan connect kancası da
            # çözücüye gitmeden engellemelidir.
            sock = socket.socket()
            try:
                with self.assertRaises(netguard.BlockedAddressError):
                    sock.connect(("2130706433", 9))
            finally:
                sock.close()

    def test_connect_ex_returns_error_for_blocked_address(self) -> None:
        with netguard.active():
            sock = socket.socket()
            try:
                self.assertNotEqual(sock.connect_ex(("127.0.0.1", 9)), 0)
            finally:
                sock.close()

    def _loopback_server(self) -> tuple[socket.socket, int, threading.Thread]:
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]

        def accept() -> None:
            try:
                try:
                    conn, _ = server.accept()
                except OSError:
                    return
                with conn:
                    conn.sendall(b"ok")
            finally:
                server.close()

        thread = threading.Thread(target=accept, daemon=True)
        thread.start()
        return server, port, thread

    def test_allowlisted_loopback_still_works(self) -> None:
        _, port, thread = self._loopback_server()
        try:
            with netguard.active(allow=[f"127.0.0.1:{port}"]):
                sock = socket.create_connection(("127.0.0.1", port), timeout=2)
                with sock:
                    self.assertEqual(sock.recv(2), b"ok")
        finally:
            thread.join(timeout=2)

    def test_proxy_is_disabled_by_default_and_can_be_opted_in(self) -> None:
        proxy = "http://127.0.0.1:9"

        with patch.dict(
            os.environ,
            {
                "PROXY_URL": proxy,
                "HTTP_PROXY": proxy,
                "HTTPS_PROXY": proxy,
                "NETGUARD_ALLOW_PROXY": "0",
            },
            clear=False,
        ):
            netguard.uninstall()
            settings = Settings.load()
            downloader = Downloader(settings)
            gallery = GalleryDownloader(settings)
            self.assertFalse(netguard.proxy_allowed())
            # yt-dlp'de boş proxy, ortam proxy'lerini de kapatır.
            self.assertEqual(downloader._base_opts().get("proxy"), "")
            self.assertIn("proxy-env=false", gallery._common_args())
            self.assertNotIn("HTTP_PROXY", os.environ)
            self.assertNotIn("HTTPS_PROXY", os.environ)

            child = netguard.child_env()
            self.assertNotIn("HTTP_PROXY", child)
            self.assertNotIn("HTTPS_PROXY", child)
            self.assertEqual(child["NETGUARD_ALLOW_PROXY"], "0")

        with patch.dict(
            os.environ,
            {
                "PROXY_URL": proxy,
                "NETGUARD_ALLOW_PROXY": "1",
            },
            clear=False,
        ):
            netguard.uninstall()
            settings = Settings.load()
            downloader = Downloader(settings)
            gallery = GalleryDownloader(settings)
            self.assertTrue(netguard.proxy_allowed())
            self.assertEqual(downloader._base_opts().get("proxy"), proxy)
            self.assertIn(proxy, gallery._common_args())
            self.assertEqual(
                netguard.child_env()["NETGUARD_ALLOW_PROXY"],
                "1",
            )

    def test_proxy_endpoint_is_only_allowlisted_when_opted_in(self) -> None:
        server, port, thread = self._loopback_server()
        proxy = f"http://127.0.0.1:{port}"
        try:
            netguard.uninstall()
            netguard.install(proxy=proxy, allow_proxy=False)
            with self.assertRaises(netguard.BlockedAddressError):
                socket.create_connection(("127.0.0.1", port), timeout=2)
        finally:
            server.close()
            thread.join(timeout=2)

        server, port, thread = self._loopback_server()
        proxy = f"http://127.0.0.1:{port}"
        try:
            netguard.uninstall()
            netguard.install(proxy=proxy, allow_proxy=True)
            sock = socket.create_connection(("127.0.0.1", port), timeout=2)
            with sock:
                self.assertEqual(sock.recv(2), b"ok")
        finally:
            server.close()
            thread.join(timeout=2)

    # --------------------------------------------------- redirect / rebinding

    def test_redirect_to_unlisted_loopback_target_is_blocked(self) -> None:
        first = _Server(("127.0.0.1", 0), _Handler)
        second = _Server(("127.0.0.1", 0), _Handler)
        first.other_port = second.server_address[1]
        threads = [
            threading.Thread(target=srv.serve_forever, daemon=True)
            for srv in (first, second)
        ]
        for thread in threads:
            thread.start()
        port = first.server_address[1]
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with netguard.active(allow=[f"127.0.0.1:{port}"]):
                self.assertEqual(opener.open(f"http://127.0.0.1:{port}/ok", timeout=3).read(), b"ok")
                with self.assertRaises(urllib.error.URLError) as caught:
                    opener.open(f"http://127.0.0.1:{port}/redirect", timeout=3).read()
                self.assertIsInstance(caught.exception.reason, netguard.BlockedAddressError)
        finally:
            for srv in (first, second):
                srv.shutdown()
                srv.server_close()

    def test_dns_rebinding_is_checked_at_connect_time(self) -> None:
        real_getaddrinfo = socket.getaddrinfo
        calls = 0

        def fake_getaddrinfo(host, port, *args, **kwargs):
            nonlocal calls
            if host == "rebind.example":
                calls += 1
                address = "1.1.1.1" if calls == 1 else "127.0.0.1"
                return [
                    (
                        socket.AF_INET,
                        socket.SOCK_STREAM,
                        socket.IPPROTO_TCP,
                        "",
                        (address, port or 9),
                    )
                ]
            return real_getaddrinfo(host, port, *args, **kwargs)

        netguard.uninstall()
        with patch("socket.getaddrinfo", side_effect=fake_getaddrinfo):
            with netguard.active():
                # İlk denetim genel adres görür (eski tek seferlik kontrol).
                self.assertFalse(asyncio.run(is_blocked_url("http://rebind.example/video")))
                # Bağlantı anında adres değişir; connect kancası yakalar.
                with self.assertRaises(netguard.BlockedAddressError):
                    socket.create_connection(("rebind.example", 9), timeout=1)
        self.assertGreaterEqual(calls, 2)

    # ------------------------------------------------------------ DNS / alt süreç

    def test_dns_failure_fails_closed(self) -> None:
        netguard.uninstall()
        with patch(
            "socket.getaddrinfo",
            side_effect=socket.gaierror(-2, "geçici DNS hatası"),
        ):
            self.assertTrue(asyncio.run(is_blocked_url("http://dnsfail.example/video")))

    def test_child_process_bootstrap_installs_guard(self) -> None:
        env = netguard.child_env()
        proc = subprocess.run(
            [
                sys.executable,
                "-c",
                "import socket; socket.create_connection(('127.0.0.1', 9), timeout=1)",
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("BlockedAddressError", proc.stderr)

    # -------------------------------------------------------------- indiriciler

    def test_ytdlp_uses_native_hls_downloader(self) -> None:
        settings = Settings.load()
        downloader = Downloader(settings)
        opts = downloader._base_opts()
        self.assertTrue(opts.get("hls_prefer_native"))
        self.assertNotIn("external_downloader", opts)

    def test_gallery_commands_ignore_external_config(self) -> None:
        settings = Settings.load()
        gallery = GalleryDownloader(settings)
        gallery.available = True
        captured: dict[str, list[str]] = {}

        def fake_run(cmd: list[str], timeout: int) -> SimpleNamespace:
            captured["cmd"] = cmd
            return SimpleNamespace(returncode=0, stdout="[]", stderr="")

        with patch.object(gallery, "_run", side_effect=fake_run):
            gallery.probe("https://www.instagram.com/p/abc/")
        self.assertIn("--config-ignore", captured["cmd"])
        self.assertIn("proxy-env=false", captured["cmd"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
