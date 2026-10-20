"""Tiket PENJUALAN-SERAGAM (21 Okt 2026): daftar Pengiriman diurut DETERMINISTIK -- seri (tanggal/status sama) dipecah f.id.
Dulu ORDER BY tanpa pembeda kedua: urutan baris seri berbeda antar-proses (terlihat di harness lama vs baru), jadi halaman bisa menggandakan/melewatkan baris."""
import asyncio
import re

from app.routers import deliveries as DL


class _Req:
    class state:
        user = {"tenant_id": "t1", "user_id": "0bccdb25-fdf0-4e99-9024-b9a20846f76c"}


def test_order_by_selalu_ada_pembeda_id(monkeypatch):
    sql = []

    class Konn:
        async def execute(self, q, *a): pass
        async def fetchrow(self, q, *a): return {"total": 0}
        async def fetchval(self, q, *a): return None
        async def fetch(self, q, *a):
            sql.append(q)
            return []

    class _A:
        async def __aenter__(s): return Konn()
        async def __aexit__(s, *a): return False

    class _P:
        def acquire(s): return _A()

    async def gp(): return _P()
    monkeypatch.setattr(DL, "get_pool", gp)

    async def tk(c, t, jenis, dokumen, kunci="id"):
        return dokumen
    import app.services.kode_order as KO
    monkeypatch.setattr(KO, "tempel_kode", tk)
    for sb in ("delivery_date", "delivery_number", "customer_name", "status", "created_at", "tak_dikenal"):
        for so in ("asc", "desc"):
            sql.clear()
            asyncio.run(DL.list_deliveries(_Req(), status=None, customer_id=None, search=None, date_from=None, date_to=None, sort_by=sb,
                                           sort_order=so, page=1, per_page=20, skip=0, offset=0, aksi=None))
            utama = next(" ".join(q.split()) for q in sql if "ORDER BY" in q)
            assert re.search(r"ORDER BY \S+ (ASC|DESC), f\.id LIMIT", utama), utama
