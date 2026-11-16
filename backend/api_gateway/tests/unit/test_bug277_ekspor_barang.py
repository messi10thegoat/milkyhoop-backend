"""BUG-277 (17 Nov 2026): Ekspor Barang xlsx/csv di jalur SENDIRI (GET /items/impor/ekspor): 14 kolom templat Impor + Stok kini/Aktif/Deskripsi (diabaikan saat impor ulang), aman dari rumus, baca-saja, berpagar tenant; /items/export lama TAK berubah."""
import asyncio
import io
import json
import pathlib
import re
from datetime import date

import pytest
from fastapi import HTTPException

from app.services import ekspor_barang as EB
from app.services import impor_barang as IB

APP = pathlib.Path(__file__).resolve().parents[2] / "app"
KEY = [k for k, _, _ in IB.KOLOM]
NK = len(KEY)          # 14 (tanpa V502) atau 15 (dengan kolom Merek, BUG-260): tes mengikuti KOLOM, bukan angka tetap
XK = {k: i for i, k in enumerate(KEY)}


def r(nama="Minyak 2L", kode="BRG-1", tipe="goods", dasar="Botol", jual=35000, beli=31000, kat="Minyak", barcode="899", status="active", konv=None, stok=24, desk="Goreng"):
    return {"stok_kini": stok, "deskripsi": desk, "id": "x", "item_code": kode, "nama_produk": nama, "item_type": tipe, "base_unit": dasar, "sales_price": jual, "purchase_price": beli,
            "kategori": kat, "barcode": barcode, "status": status, "konversi": json.dumps(konv or [])}


KV = [{"u": "Karton", "f": "12", "j": "410000", "b": "365000"}, {"u": "Palet", "f": "240", "j": "8000000", "b": "7000000"}]


def test_judul_empat_belas_templat_lalu_tiga_informatif():
    assert EB.judul()[:NK] == [j for _, j, _ in IB.KOLOM] and EB.judul()[NK:] == ["Stok kini (satuan dasar)", "Aktif", "Deskripsi"] and len(EB.judul()) == NK + 3


def test_baris_satu_barang_dengan_konversi_pertama_dan_penghitung_lewat():
    b, lewat = EB.baris_ekspor({**r(), "konversi": KV}, KV)
    peta = dict(zip([k for k, _, _ in IB.KOLOM], b))
    assert (peta["nama"], peta["kode"], peta["tipe"], peta["satuan_dasar"]) == ("Minyak 2L", "BRG-1", "barang", "Botol")
    assert (peta["harga_jual"], peta["harga_beli"], peta["kategori"], peta["barcode"]) == (35000.0, 31000.0, "Minyak", "899")
    assert (peta["satuan_besar"], peta["isi"], peta["harga_jual_besar"], peta["harga_beli_besar"]) == ("Karton", 12.0, 410000.0, 365000.0)
    assert peta["stok_awal"] is None and peta["harga_pokok_awal"] is None and lewat == 1
    assert b[NK:] == ["24 Botol", "Ya", "Goreng"] and len(b) == NK + 3


def test_kolom_informatif_stok_teks_aktif_dan_jasa_tanpa_stok():
    assert EB.baris_ekspor(r(stok="12.500"), [])[0][NK] == "12.5 Botol" and EB.baris_ekspor(r(stok=0), [])[0][NK] == "0 Botol"
    assert EB.baris_ekspor(r(tipe="service", stok=5), [])[0][NK] is None and EB.baris_ekspor(r(tipe="non_inventory"), [])[0][NK] is None
    assert EB.baris_ekspor(r(status="inactive"), [])[0][NK + 1] == "Tidak" and EB.baris_ekspor(r(desk="=cmd"), [])[0][NK + 2] == "'=cmd"


@pytest.mark.parametrize("tipe, teks", [("goods", "barang"), ("non_inventory", "non stok"), ("service", "jasa")])
def test_tipe_teks_dikenal_impor(tipe, teks):
    b, _ = EB.baris_ekspor(r(tipe=tipe), [])
    assert b[2] == teks and IB._TIPE[teks] == tipe            # ekspor -> impor bolak-balik


def test_tanpa_konversi_kolom_besar_kosong():
    b, lewat = EB.baris_ekspor(r(jual=None, beli=None), [])
    assert all(v is None for v in b[XK["harga_jual"]:XK["harga_beli_besar"] + 1]) and lewat == 0


@pytest.mark.parametrize("v, hasil", [("=1+1", "'=1+1"), ("+62812", "'+62812"), ("-5", "'-5"), ("@SUM(A1)", "'@SUM(A1)"), ("\tx", "'\tx"), ("\rx", "'\rx"),
                                       ("Biasa", "Biasa"), ("", ""), (None, None), (12.5, 12.5)])
def test_aman_dari_rumus(v, hasil):
    assert EB.aman(v) == hasil


def test_nama_dan_kode_berawalan_rumus_diberi_apostrof_di_baris():
    b, _ = EB.baris_ekspor(r(nama='=HYPERLINK("http://x","klik")', kode="+X", kat="@kat", barcode="-1"), [])
    assert b[0].startswith("'=") and b[1] == "'+X" and b[XK["kategori"]] == "'@kat" and b[XK["barcode"]] == "'-1"


def test_csv_judul_bom_angka_bersih_dan_baris():
    baris, _, _ = EB.siapkan([{**r(), "konversi": json.dumps(KV)}])
    isi = EB.bangun_csv(baris)
    assert isi.startswith(b"\xef\xbb\xbf")
    rows = list(__import__("csv").reader(io.StringIO(isi.decode("utf-8-sig"))))
    assert rows[0] == EB.judul() and rows[1][XK["harga_jual"]] == "35000" and rows[1][XK["isi"]] == "12" and rows[1][XK["stok_awal"]] == "" and rows[1][NK:] == ["24 Botol", "Ya", "Goreng"] and len(rows) == 2


def test_xlsx_bisa_dibaca_balik_oleh_pembaca_impor_dan_petunjuk_menyebut_yang_dilewati():
    baris, banyak, lewat = EB.siapkan([{**r(), "konversi": json.dumps(KV)}, r(nama="Teh", kode="BRG-2", dasar="Pcs", konv=None)])
    assert (banyak, lewat) == (1, 1)
    isi = EB.bangun_xlsx(baris, banyak, lewat)
    out = IB.baca_xlsx(isi)
    rows = out["baris"]
    assert [x["nama"] for x in rows] == ["Minyak 2L", "Teh"] and rows[0]["satuan_besar"] == "Karton" and str(rows[0]["isi"]) in ("12", "12.0")
    # round-trip: tiga kolom informatif DIABAIKAN (hanya disebut "tidak dipakai"), tak menjadi medan impor
    assert set(out["tak_dipakai"]) == {"Stok kini (satuan dasar)", "Aktif", "Deskripsi"} and "stok_awal" not in rows[0] and "24 Botol" not in rows[0].values()
    from openpyxl import load_workbook
    ws = load_workbook(io.BytesIO(isi))["Petunjuk"]
    assert "1 barang punya satuan besar lain yang tidak ikut (1 satuan)" in " ".join(str(c.value) for row in ws.iter_rows() for c in row)


def test_sql_berpagar_tenant_tanpa_hapus_dan_hanya_baca():
    assert "p.tenant_id = $1" in EB.SQL_BARANG and "p.deleted_at IS NULL" in EB.SQL_BARANG and "uc.tenant_id = p.tenant_id" in EB.SQL_BARANG and "uc.is_active = true" in EB.SQL_BARANG and "il.tenant_id = p.tenant_id" in EB.SQL_BARANG
    assert not re.search(r"\b(INSERT|UPDATE|DELETE)\b", EB.SQL_BARANG)


class Pool:
    def __init__(self, rows):
        self.rows, self.sql = rows, []

    def acquire(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def fetch(self, sql, *a):
        self.sql.append((sql, a))
        return self.rows

    async def fetchval(self, sql, *a):
        return date(2026, 11, 17)


class Req:
    class state:
        user = {"tenant_id": "t1", "user_id": "u1"}
    headers = {}


def panggil(rows, **kw):
    from app.routers import items as RI
    pool = Pool(rows)

    async def gp():
        return pool
    RI.get_pool = gp
    RI.get_user_context = lambda request: {"tenant_id": "t1", "user_id": "u1"}

    async def td(conn, tid):
        return date(2026, 11, 17)
    RI.tanggal_dokumen = td
    args = dict(format="xlsx", status="semua", kategori=None, search=None)
    args.update(kw)
    return asyncio.run(RI.ekspor_barang_impor(Req(), **args)), pool


def test_rute_xlsx_default_header_dan_nama_berkas():
    resp, pool = panggil([r()])
    assert resp.media_type.endswith("spreadsheetml.sheet") and "barang-2026-11-17.xlsx" in resp.headers["content-disposition"] and resp.headers["x-ekspor-jumlah"] == "1"
    assert pool.sql[0][1][0] == "t1" and "ORDER BY lower(p.nama_produk), p.id" in pool.sql[0][0]


def test_rute_csv_filter_status_kategori_cari_berparameter_bukan_string():
    resp, pool = panggil([r()], format="csv", status="aktif", kategori=" Minyak ", search="goreng 2L")
    sql, a = pool.sql[0]
    assert resp.media_type.startswith("text/csv") and a[0] == "t1" and a[1] == "active" and a[2] == "Minyak" and a[3:] == ("%goreng%", "%2L%")
    assert "p.status = $2" in sql and "p.kategori = $3" in sql and "ILIKE $4" in sql and "Minyak" not in sql and "goreng" not in sql
    assert panggil([r()], status="nonaktif")[1].sql[0][1][1] == "inactive"


@pytest.mark.parametrize("kw", [dict(format="pdf"), dict(status="semua2")])
def test_rute_parameter_tak_sah_422(kw):
    with pytest.raises(HTTPException) as e:
        panggil([r()], **kw)
    assert e.value.status_code == 422


def test_rute_batas_baris():
    with pytest.raises(HTTPException) as e:
        panggil([r()] * (EB.MAKS_BARIS + 1))
    assert e.value.status_code == 422 and e.value.detail["code"] == "TERLALU_BANYAK"
    assert f"LIMIT {EB.MAKS_BARIS + 1}" in panggil([r()])[1].sql[0][0]


def test_rute_lama_tak_berubah_dan_rute_baru_sendiri_sebelum_rute_id():
    src = (APP / "routers" / "items.py").read_text()
    lama = src.split('@router.get("/items/export")')[1].split("# ====")[0]
    assert '"Selling Price"' in lama and "Export format not supported" in lama and "ekspor_barang" not in lama        # kontrak lama utuh (putusan MASTER)
    assert '@router.get("/items/impor/ekspor")' in src and src.index('@router.get("/items/impor/ekspor")') < src.index('@router.get("/items/{item_id}", ')
    assert "SELECT" not in src.split("async def ekspor_barang_impor")[1].split("@router")[0]               # kueri hanya di services (satu tempat)


def test_merek_ikut_bila_kolom_merek_ada_di_templat_impor():
    b, _ = EB.baris_ekspor({**r(), "merek": "Sania"}, [])
    if "merek" in XK:
        assert b[XK["merek"]] == "Sania"
    else:
        assert "Sania" not in b
    assert "(to_jsonb(p) ->> 'merek') AS merek" in EB.SQL_BARANG      # aman sebelum/sesudah V502 (kolom products.merek)
