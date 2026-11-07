"""ROW (m) ember laju PRATINJAU (8 Nov 2026). Dulu _bucket memasukkan SEMUA non-GET ke ember tulis (100/60 dtk per
pengguna); pratinjau CW (/calculate, stok-baris, price-lists/resolve, ...) sudah +-2 POST per suntingan baris, jadi kasir
grosir yang mengetik cepat menghabiskan ember dan SIMPAN sungguhan kena 429. Kini pratinjau nol tulis = ember sendiri,
DAFTAR PUTIH templat rute persis (bukan awalan/akhiran longgar); ember tulis tak berubah."""
import asyncio
import os
import re

import pytest

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")  # impor app.main butuh satu penyedia LLM

from app.middleware import rate_limit_middleware as RL
from app.middleware.rate_limit_middleware import InMemoryRateLimiter, RateLimitMiddleware


def _rl(monkeypatch):
    monkeypatch.setattr(RL.settings, "RATE_LIMIT_REQUESTS", 100)
    monkeypatch.setattr(RL.settings, "RATE_LIMIT_WINDOW", 60)
    monkeypatch.setattr(RL.settings, "RATE_LIMIT_PREVIEW_REQUESTS", 600)
    rl = RateLimitMiddleware.__new__(RateLimitMiddleware)
    rl.auth_paths = set()
    rl._redis_limiter = None
    rl._memory_limiter = InMemoryRateLimiter()
    return rl


def _kena(rl, path, n, method="POST"):
    async def jalan():
        return [(await rl._check_rate_limit("user:u1", path, method))[0] for _ in range(n)]
    return sum(asyncio.run(jalan()))


def test_150_calculate_per_menit_nol_429_tulis_101_kena(monkeypatch):
    rl = _rl(monkeypatch)
    assert _kena(rl, "/api/sales-invoices/calculate", 150) == 0
    assert _kena(rl, "/api/inventory/stok-baris", 150) == 0
    assert _kena(rl, "/api/price-lists/resolve/sales-invoices", 150) == 0
    # pratinjau tak memakan ember tulis: 100 SIMPAN masih lolos, ke-101 = 429
    assert _kena(rl, "/api/sales-invoices", 100) == 0
    assert _kena(rl, "/api/sales-invoices", 1) == 1


def test_ember_pratinjau_tetap_berbatas(monkeypatch):
    rl = _rl(monkeypatch)
    assert rl._get_limits("/api/bills/calculate", "POST") == (600, 60)
    assert _kena(rl, "/api/bills/calculate", 601) == 1


@pytest.mark.parametrize("path", [
    "/api/sales-invoices", "/api/sales-invoices/v2", "/api/bills", "/api/sales-invoices/3f2a/post",
    "/api/bills/3f2a/edit", "/api/receive-payments/3f2a/post", "/api/sales-invoices/calculate/x",
    "/api/sales-invoices/calculatex", "/api/x/sales-invoices/calculate", "/api/sales-invoices/a/b/post/preview",
    "/api/payroll-runs/3f2a/calculate", "/api/bom/3f2a/recalculate", "/api/price-lists/resolve",
    "/api/price-lists/resolve/a/b", "/api/inventory/stok-baris/x", "/api/sales-invoices/calculate/",
])
def test_kontrol_path_tulis_mirip_tetap_ember_tulis(monkeypatch, path):
    rl = _rl(monkeypatch)
    assert rl._bucket(path, "POST") == "write"
    assert rl._get_limits(path, "POST") == (100, 60)


def test_metode_lain_tak_masuk_pratinjau(monkeypatch):
    rl = _rl(monkeypatch)
    assert rl._bucket("/api/sales-invoices/calculate", "GET") == "read"
    for m in ("PUT", "PATCH", "DELETE"):
        assert rl._bucket("/api/bills/3f2a/edit/preview", m) == "write"


def test_daftar_putih_hidup_dan_tak_menelan_rute_lain():
    """Setiap templat = rute POST NYATA di app.main (bukan path mati); dan pola daftar putih tak cocok dengan rute POST
    lain mana pun (parameter [^/]+ tak boleh menelan rute tulis yang belum diukur nol tulis)."""
    from starlette.routing import Match
    from app.main import app

    post = {r.path for r in app.routes if "POST" in (getattr(r, "methods", None) or set())}
    assert set(RL.PRATINJAU_NOL_TULIS) <= post, set(RL.PRATINJAU_NOL_TULIS) - post
    contoh = lambda p: re.sub(r"\{[^}]+\}", "3f2a", p)
    for p in post - set(RL.PRATINJAU_NOL_TULIS):
        assert not RL._POLA_PRATINJAU.match(contoh(p)), p
    for p in RL.PRATINJAU_NOL_TULIS:  # path contoh dimenangkan rute templat itu sendiri (bukan ditelan rute lain)
        scope = {"type": "http", "method": "POST", "path": contoh(p), "root_path": "", "headers": []}
        menang = next(r for r in app.routes if r.matches(scope)[0] == Match.FULL)
        assert menang.path == p, (p, menang.path)
