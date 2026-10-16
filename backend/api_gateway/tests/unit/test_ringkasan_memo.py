"""PERF-DAFTAR-SO (16 Okt 2026): memo SATU PERMINTAAN ringkasan_pesanan (Law 1/16/29: tiap SO tetap dihitung dari jurnal; hasil sama
tak dihitung ulang). Kesetaraan di data nyata: scripts/ops/uji-nyata-memo-daftar-so.py."""
import asyncio
import uuid
from decimal import Decimal as D

from app.routers import sales_orders as SOR
from app.services import proforma_terbayar as PT

T = "t1"


def _pasang(monkeypatch):
    panggilan = []

    async def hitung(conn, tenant_id, so_ids):
        panggilan.append(sorted(so_ids))
        return {s: {"invoiced": D("1"), "tertutup": D(str(len(panggilan)))} for s in so_ids}

    monkeypatch.setattr(PT, "_hitung_ringkasan_pesanan", hitung)
    return panggilan


def test_tanpa_memo_perilaku_lama_hitung_tiap_kali(monkeypatch):
    p = _pasang(monkeypatch)
    a, b = uuid.uuid4(), uuid.uuid4()

    async def uji():
        await PT.ringkasan_pesanan(None, T, [a, b])
        await PT.ringkasan_pesanan(None, T, [a])
    asyncio.run(uji())
    assert len(p) == 2


def test_memo_hitung_hanya_yang_belum_dan_hasil_salinan(monkeypatch):
    p = _pasang(monkeypatch)
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    @PT.memo_ringkasan
    async def permintaan():
        r1 = await PT.ringkasan_pesanan(None, T, [a, b])
        r2 = await PT.ringkasan_pesanan(None, T, [b, c, None])
        r3 = await PT.ringkasan_pesanan(None, T, [a, b, c])
        r1[a]["tertutup"] = D("999")           # pemanggil boleh mengubah -> tak mencemari memo
        r4 = await PT.ringkasan_pesanan(None, T, [a])
        return r1, r2, r3, r4
    r1, r2, r3, r4 = asyncio.run(permintaan())
    assert p == [sorted([a, b]), [c]]                      # b tak dihitung ulang
    assert set(r2) == {b, c} and set(r3) == {a, b, c}
    assert r3[a]["tertutup"] == D("1") and r4[a]["tertutup"] == D("1")   # nilai asli, bukan 999
    assert r2[c]["tertutup"] == D("2")


def test_memo_tidak_bocor_antar_permintaan_dan_antar_tenant(monkeypatch):
    p = _pasang(monkeypatch)
    a = uuid.uuid4()

    @PT.memo_ringkasan
    async def permintaan(tenant):
        await PT.ringkasan_pesanan(None, tenant, [a])
        await PT.ringkasan_pesanan(None, tenant, [a])
    asyncio.run(permintaan("t1"))
    asyncio.run(permintaan("t1"))     # permintaan baru = memo baru
    assert len(p) == 2
    asyncio.run(PT.ringkasan_pesanan(None, "t1", [a]))   # di luar dekorator: tanpa memo
    assert len(p) == 3

    @PT.memo_ringkasan
    async def dua_tenant():
        await PT.ringkasan_pesanan(None, "ta", [a])
        await PT.ringkasan_pesanan(None, "tb", [a])
    asyncio.run(dua_tenant())
    assert len(p) == 5               # kunci memo = (tenant, so)


def test_hanya_handler_baca_daftar_dan_summary_dibungkus():
    # bungkus() meninggalkan __wrapped__; handler TULIS tak boleh ikut (nilai basi dalam transaksi tulis)
    assert hasattr(SOR.list_sales_orders, "__wrapped__") and hasattr(SOR.get_sales_order_summary, "__wrapped__")
    for nama in dir(SOR):
        f = getattr(SOR, nama)
        if callable(f) and hasattr(f, "__wrapped__") and nama not in ("list_sales_orders", "get_sales_order_summary"):
            raise AssertionError(f"handler lain terbungkus memo: {nama}")
