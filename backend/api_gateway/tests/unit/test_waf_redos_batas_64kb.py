"""Tiket ReDoS WAF: teks dipindai dibatasi 64 KB; badan 1 MB selesai cepat."""
import time

from app.middleware.waf_middleware import WAFMiddleware as W


def _w():
    w = W.__new__(W)
    w.strict_mode = True
    return w


def test_batas_64kb():
    assert W.MAX_INSPEKSI_BODY == 64 * 1024


def test_badan_1mb_pola_terburuk_cepat():
    w = _w()
    for s in (" " * (1 << 20), "SELECT " + "a " * (1 << 19), "-" * (1 << 20),
              "OR " + "1" * (1 << 20), " on" * (1 << 19), "<script " * (1 << 17)):
        t = time.time()
        w._detect_threat(s[: W.MAX_INSPEKSI_BODY], "Body")
        assert time.time() - t < 0.1


def test_ancaman_awal_tetap_terdeteksi():
    assert _w()._detect_threat("x UNION SELECT 1", "Body") == "SQL Injection"
