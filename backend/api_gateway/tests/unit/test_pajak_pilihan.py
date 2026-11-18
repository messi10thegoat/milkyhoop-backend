"""Pajak pilihan SATU penentu (18 Nov 2026): PS.pajak_sah/daftar_pajak dipakai penulis barang (nama_pajak_item) dan GET /items/pajak-pilihan. Sah = milik tenant, aktif, arah cocok / arah kosong."""
import asyncio
import pathlib
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.services import pajak_sah as PS

APP = pathlib.Path(__file__).resolve().parents[2] / "app"


def T(i, kode, nama, rate, direction, aktif=True):
    return {"id": f"00000000-0000-0000-0000-{i:012d}", "code": kode, "name": nama, "rate": rate, "direction": direction, "is_active": aktif}


PAJAK = [T(1, "NOL", "Tanpa Pajak", 0, None), T(2, "PPH23", "PPh 23", 2, None), T(3, "PPN-IN", "PPN Masukan 11%", 11, "input"), T(4, "PPN-OUT", "PPN Keluaran 11%", 11, "output"),
         T(5, "PPN-OFF", "PPN lama", 11, "output", aktif=False), T(6, "PPN12", "PPN 12", 12, "output")]


class Conn:
    def __init__(self, rows):
        self.rows, self.sql = rows, []

    async def fetch(self, sql, *a):
        self.sql.append(sql)
        return [r for r in sorted(self.rows, key=lambda r: r["code"]) if r["is_active"]]

    async def fetchrow(self, sql, tax_id, tenant):
        return next((r for r in self.rows if r["id"] == str(tax_id)), None)

    async def close(self):
        pass


def daftar(jenis):
    return asyncio.run(PS.daftar_pajak(Conn(PAJAK), "t", jenis))


def test_penjualan_hanya_output_dan_arah_kosong():
    assert [p["kode"] for p in daftar("penjualan")] == ["NOL", "PPH23", "PPN-OUT", "PPN12"]


def test_pembelian_hanya_input_dan_arah_kosong():
    assert [p["kode"] for p in daftar("pembelian")] == ["NOL", "PPH23", "PPN-IN"]


def test_nonaktif_tak_pernah_muncul():
    assert all(p["kode"] != "PPN-OFF" for j in PS.JENIS for p in daftar(j))


def test_bentuk_dan_text_tanpa_tarif_dobel():
    p = {x["kode"]: x for x in daftar("penjualan")}
    assert set(p["PPN-OUT"]) == {"id", "kode", "nama", "tarif", "text"}
    assert p["PPN-OUT"]["text"] == "PPN Keluaran 11%" and p["PPN-OUT"]["tarif"] == "11"      # nama sudah memuat tarif -> tak digandakan
    assert p["PPH23"]["text"] == "PPh 23 (2%)" and p["PPH23"]["tarif"] == "2"
    assert p["PPN12"]["text"] == "PPN 12 (12%)" and p["NOL"]["text"] == "Tanpa Pajak" and p["NOL"]["tarif"] == "0"


@pytest.mark.parametrize("jenis,arah", list(PS.JENIS.items()))
def test_uji_silang_daftar_sama_dengan_penulis(jenis, arah):
    """tiap pajak: ada di daftar <=> nama_pajak_item (penulis) menerimanya; yang ditolak membawa frasa alasan."""
    from app.routers import items as IT
    ids = {p["id"] for p in daftar(jenis)}
    conn = Conn(PAJAK)
    for tc in PAJAK:
        try:
            nama = asyncio.run(IT.nama_pajak_item(conn, "t", tc["id"], "f", arah))
            diterima = True
            assert nama == tc["name"]
        except HTTPException as e:
            diterima = False
            assert e.detail["code"] == "PAJAK_ITEM_TIDAK_SAH" and e.detail["field"] == "f"
        assert diterima == (tc["id"] in ids), (jenis, tc["code"])


def test_pesan_penulis_lama_tetap():
    from app.routers import items as IT
    for tid, arah, frasa in ((PAJAK[4]["id"], "output", "tidak aktif"), (PAJAK[2]["id"], "output", "bukan pajak penjualan"), (PAJAK[3]["id"], "input", "bukan pajak pembelian"),
                             ("00000000-0000-0000-0000-0000000000ff", "output", "tidak ditemukan")):
        with pytest.raises(HTTPException) as e:
            asyncio.run(IT.nama_pajak_item(Conn(PAJAK), "t", tid, "sales_tax_id", arah))
        assert e.value.status_code == 422 and e.value.detail["message"] == f"Pajak item {frasa}."


def test_sql_menyaring_tenant_dan_aktif():
    c = Conn(PAJAK)
    asyncio.run(PS.daftar_pajak(c, "t", "penjualan"))
    assert "tenant_id = $1" in c.sql[0] and "is_active = true" in c.sql[0]


def test_rute_jenis_tak_dikenal_dan_bentuk(monkeypatch):
    from app.routers import items as IT
    with pytest.raises(HTTPException) as e:
        asyncio.run(IT.pajak_pilihan(None, jenis="x"))
    assert e.value.status_code == 422 and e.value.detail["code"] == "PAJAK_JENIS_TAK_DIKENAL" and e.value.detail["field"] == "jenis"

    async def konek():
        return Conn(PAJAK)
    monkeypatch.setattr(IT, "get_db_connection", konek)
    monkeypatch.setattr(IT, "get_tenant_id", lambda r: "t")
    r = asyncio.run(IT.pajak_pilihan(SimpleNamespace(), jenis="pembelian"))
    assert [p["kode"] for p in r["pajak"]] == ["NOL", "PPH23", "PPN-IN"]


def test_rute_sebelum_rute_id_dan_izin():
    s = (APP / "routers" / "items.py").read_text()
    assert s.index('"/items/pajak-pilihan"') < s.index('@router.get("/items/{item_id}", response_model=ItemDetailResponse)')
    from app.middleware.permission_middleware import izin_untuk_jalur
    assert izin_untuk_jalur("/api/items/pajak-pilihan", "GET")[1] == "R"


def test_penulis_memakai_penentu_bersama():
    s = (APP / "routers" / "items.py").read_text()
    assert "PS.alasan_tolak(arah, dict(tc) if tc else None)" in s
