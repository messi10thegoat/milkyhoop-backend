"""K-2b (17 Nov 2026, permintaan FE): GET /items/categories/{id}/history ADITIF oleh_nama (rantai nama_pengguna) + waktu_text (zona tenant); waktu/oleh lama utuh."""
import asyncio
import pathlib
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.services import kategori_aksi as KA
from app.utils import tanggal_tenant as TT

APP = pathlib.Path(__file__).resolve().parents[2] / "app"
T1 = datetime(2026, 11, 17, 7, 5, tzinfo=timezone.utc)      # 15:05 WITA / 14:05 WIB
T2 = datetime(2026, 11, 1, 16, 59, tzinfo=timezone.utc)     # lewat tengah malam di WITA -> 2 Nov 00:59


class Konn:
    def __init__(self, rows, nama=None):
        self.rows, self.nama, self.sql = rows, nama or {}, []

    async def fetch(self, sql, *a):
        self.sql.append((sql, a))
        if "FROM audit_logs" in sql:
            return self.rows
        assert 'FROM "User"' in sql
        return [{"id": k, "nama": v} for k, v in self.nama.items() if k in a[0]]


def baris(ev, waktu, uid, md=None):
    return {"eventType": ev, "metadata": md or {}, "createdAt": waktu, "userId": uid}


def jalan(conn, zona="Asia/Makassar"):
    async def z(c, t):
        return ZoneInfo(zona)
    asli, TT.zona_tenant = TT.zona_tenant, z
    try:
        return asyncio.run(KA.riwayat(conn, "t1", "11111111-1111-1111-1111-111111111111", 50))
    finally:
        TT.zona_tenant = asli


def test_aditif_oleh_nama_dan_waktu_text_zona_tenant_medan_lama_utuh():
    c = Konn([baris("ITEM_CATEGORY_CREATED", T1, "u1"), baris("ITEM_CATEGORY_RENAMED", T2, "u2")], {"u1": "Anton", "u2": "Budi"})
    r = jalan(c)
    assert (r[0]["oleh"], r[0]["oleh_nama"], r[0]["waktu"], r[0]["waktu_text"]) == ("u1", "Anton", T1.isoformat(), "17 Nov 2026 15:05")
    assert (r[1]["oleh_nama"], r[1]["waktu_text"]) == ("Budi", "2 Nov 2026 00:59") and r[0]["teks"] == "Kategori dibuat"
    assert jalan(c, "Asia/Jakarta")[0]["waktu_text"] == "17 Nov 2026 14:05"


def test_pengguna_tak_dikenal_atau_kosong_nama_none_dan_satu_kueri_nama():
    c = Konn([baris("ITEM_CATEGORY_CREATED", T1, "hantu"), baris("ITEM_CATEGORY_RENAMED", T2, None), baris("ITEM_CATEGORY_RENAMED", T2, "hantu")], {})
    r = jalan(c)
    assert [x["oleh_nama"] for x in r] == [None, None, None] and r[1]["oleh"] is None
    assert len([s for s, _ in c.sql if 'FROM "User"' in s]) == 1                      # satu kueri, bukan per baris


def test_waktu_kosong_dan_chain_nama_dari_helper_bersama():
    assert KA.waktu_teks(None, ZoneInfo("Asia/Makassar")) is None
    src = (APP / "services" / "kategori_aksi.py").read_text()
    assert "NP.nama_untuk(conn, [r[\"userId\"] for r in rows])" in src and "tenant_id = $1 AND entity_type = 'item_category'" in src
