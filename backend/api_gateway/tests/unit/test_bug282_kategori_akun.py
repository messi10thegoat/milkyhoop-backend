"""BUG-282 (V505): akun bawaan per KATEGORI barang -- disalin ke barang BARU; pratinjau == penulis; aksi_tersedia gabung/pindahkan_barang satu penentu dgn rencana."""
import asyncio
import inspect
import json
import pathlib
import re
from types import SimpleNamespace

from app.services import kategori_akun as KAK
from app.services import kategori_aksi as KA
from app.services import kategori_daftar as KD

APP = pathlib.Path(__file__).resolve().parents[2] / "app"
A_JUAL = {"id": "11111111-1111-1111-1111-111111111111", "account_code": "4-10100", "name": "Penjualan", "account_type": "REVENUE", "is_active": True, "is_header": False}
A_PERS = {"id": "22222222-2222-2222-2222-222222222222", "account_code": "1-10600", "name": "Persediaan Barang", "account_type": "ASSET", "is_active": True, "is_header": False}
A_HPP = {"id": "33333333-3333-3333-3333-333333333333", "account_code": "5-10100", "name": "HPP", "account_type": "COGS", "is_active": True, "is_header": False}
A_BELI = {"id": "44444444-4444-4444-4444-444444444444", "account_code": "6-10100", "name": "Beban Pembelian", "account_type": "EXPENSE", "is_active": True, "is_header": False}
AKUN = {a["id"]: a for a in (A_JUAL, A_PERS, A_HPP, A_BELI)}


def kat(**kw):
    d = {"id": "c-1", "name": "Minuman", "akun_penjualan": None, "akun_persediaan": None, "akun_hpp": None, "akun_pembelian": None}
    d.update(kw)
    return d


def kode(r):
    return [b["code"] for b in r["blocks"]]


def test_rencana_akun_sah_dan_peringatan():
    r = KAK.rencana_akun(kat(), {"penjualan": A_JUAL["id"], "persediaan": A_PERS["id"], "hpp": A_HPP["id"], "pembelian": A_BELI["id"]}, AKUN)
    assert r["ok"] and [p["jenis"] for p in r["rencana"]["perubahan"]] == ["penjualan", "persediaan", "hpp", "pembelian"]
    assert r["rencana"]["perubahan"][0]["ke"] == "4-10100 Penjualan" and r["rencana"]["perubahan"][0]["dari"] is None
    assert "barang dan jurnal yang sudah ada tidak berubah" in r["peringatan"][0]


def test_rencana_akun_blok_tipe_nonaktif_header_tak_ada():
    assert kode(KAK.rencana_akun(kat(), {"penjualan": A_HPP["id"]}, AKUN)) == ["AKUN_TIPE_SALAH"]
    assert kode(KAK.rencana_akun(kat(), {"persediaan": A_JUAL["id"]}, AKUN)) == ["AKUN_TIPE_SALAH"]
    assert kode(KAK.rencana_akun(kat(), {"persediaan": A_HPP["id"]}, AKUN)) == ["AKUN_TIPE_SALAH"]
    kosong = {**A_JUAL, "is_active": False}
    assert kode(KAK.rencana_akun(kat(), {"penjualan": kosong["id"]}, {kosong["id"]: kosong})) == ["AKUN_NONAKTIF"]
    hdr = {**A_JUAL, "is_header": True}
    assert kode(KAK.rencana_akun(kat(), {"penjualan": hdr["id"]}, {hdr["id"]: hdr})) == ["AKUN_HEADER"]
    assert kode(KAK.rencana_akun(kat(), {"penjualan": "55555555-5555-5555-5555-555555555555"}, AKUN)) == ["AKUN_TAK_DITEMUKAN"]
    b = KAK.rencana_akun(kat(), {"penjualan": A_HPP["id"]}, AKUN)["blocks"][0]
    assert b["field"] == "penjualan"


def test_rencana_akun_pembelian_boleh_beban_atau_persediaan():
    assert KAK.rencana_akun(kat(), {"pembelian": A_BELI["id"]}, AKUN)["ok"]
    assert KAK.rencana_akun(kat(), {"pembelian": A_PERS["id"]}, AKUN)["ok"]
    assert kode(KAK.rencana_akun(kat(), {"pembelian": A_JUAL["id"]}, AKUN)) == ["AKUN_TIPE_SALAH"]


def test_rencana_akun_tak_ada_perubahan_kosongkan_dan_jenis_asing():
    lama = kat(akun_penjualan=json.dumps({"id": A_JUAL["id"], "kode": "4-10100", "nama": "Penjualan", "aktif": True}))
    assert kode(KAK.rencana_akun(lama, {"penjualan": A_JUAL["id"]}, AKUN)) == ["TIDAK_ADA_PERUBAHAN"]
    r = KAK.rencana_akun(lama, {"penjualan": None}, AKUN)
    assert r["ok"] and [{x: v for x, v in p.items() if x in ("jenis", "dari", "ke", "ke_id")} for p in r["rencana"]["perubahan"]] == [{"jenis": "penjualan", "dari": "4-10100 Penjualan", "ke": None, "ke_id": None}]
    assert kode(KAK.rencana_akun(kat(), {"penjualan": None}, AKUN)) == ["TIDAK_ADA_PERUBAHAN"]
    assert kode(KAK.rencana_akun(kat(), {"warna": "x"}, AKUN)) == ["AKUN_JENIS_TAK_DIKENAL"] and kode(KAK.rencana_akun(kat(), {}, AKUN)) == ["AKUN_TAK_DIISI"]
    assert kode(KAK.rencana_akun(None, {"penjualan": A_JUAL["id"]}, AKUN)) == ["KATEGORI_TAK_ADA"]


def test_bentuk_dan_teks_di_baris_daftar():
    r = {"id": "c-1", "name": "Minuman", "created_at": None, "is_active": True, "is_default": False, "parent_id": None, "parent_name": None, "jumlah_anak": 0, "description": None,
         "jumlah_barang": 3, "jumlah_aktif": 3,
         "akun_penjualan": json.dumps({"id": A_JUAL["id"], "kode": "4-10100", "nama": "Penjualan", "aktif": True}), "akun_persediaan": None, "akun_hpp": None, "akun_pembelian": None}
    b = KD.baris(r)
    assert b["akun"]["penjualan"]["text"] == "4-10100 Penjualan" and b["akun"]["hpp"] is None and b["akun_text"] == "Penjualan 4-10100 Penjualan"
    r.pop("akun_penjualan")   # baris tanpa kolom akun (mis. kategori baru dibuat) tetap utuh
    assert KD.baris(r)["akun_text"] == "Tanpa akun bawaan"


# ---- aksi_tersedia gabung / pindahkan_barang = SATU penentu dgn rencana
def test_aksi_gabung_dan_pindahkan_barang_alasan_sama_dgn_rencana():
    a = KD.aksi_tersedia(5, 5, "Minuman", True, False, 0)
    assert a["gabung"] == {"tersedia": True, "alasan": None} and a["pindahkan_barang"] == {"tersedia": True, "alasan": None} and a["atur_akun"]["tersedia"]
    b = KD.aksi_tersedia(0, 0, "Kosong", True, False, 0)
    assert b["pindahkan_barang"]["tersedia"] is False
    assert b["pindahkan_barang"]["alasan"] == KA.rencana_pindah(None, {"id": "t", "name": "T", "is_active": True}, 0, 0, [], False)["blocks"][0]["message"]
    d = KD.aksi_tersedia(3, 3, "Umum", True, True, 0)
    sumber = {"id": "c-1", "name": "Umum", "is_default": True, "jumlah_anak": 0, "jumlah_barang": 3}
    assert d["gabung"]["alasan"] == KA.rencana_gabung(sumber, {"id": "c-2", "name": "X", "is_active": True})["blocks"][0]["message"]
    e = KD.aksi_tersedia(3, 3, "Induk", True, False, 2)
    sumber2 = {"id": "c-1", "name": "Induk", "is_default": False, "jumlah_anak": 2, "jumlah_barang": 3}
    assert e["gabung"]["alasan"] == KA.rencana_gabung(sumber2, {"id": "c-2", "name": "X", "is_active": True})["blocks"][0]["message"] and "2 sub kategori" in e["gabung"]["alasan"]
    assert list(a)[:6] == ["ubah", "nonaktifkan", "aktifkan", "jadikan_bawaan", "pindahkan_barang", "gabung"]


# ---- salin ke barang BARU
class Konn:
    def __init__(self, kategori, akun=None):
        self.kategori, self.akun, self.sql = kategori, akun or AKUN, []

    async def fetchrow(self, sql, *a):
        self.sql.append(sql)
        return self.kategori

    async def fetch(self, sql, tid, ids):
        self.sql.append(sql)
        return [self.akun[i] for i in ids if i in self.akun]


def body(**kw):
    d = dict(kategori="Minuman", item_type="goods", track_inventory=True, sales_account_id=None, purchase_account_id=None, inventory_account_id=None, cogs_account_id=None)
    d.update(kw)
    return SimpleNamespace(**d)


KAT = {"sales_account_id": A_JUAL["id"], "inventory_account_id": A_PERS["id"], "cogs_account_id": A_HPP["id"], "purchase_account_id": A_BELI["id"]}


def test_salin_mengisi_yang_kosong_dan_eksplisit_menang():
    b = body(sales_account_id="eksplisit")
    async def jalan():
        r_ = await KAK.salin_ke_barang(Konn(KAT), "t", b)
        return r_, KAK.terakhir()   # ContextVar: dibaca di TASK yang sama seperti pratinjau Buat Barang
    r, ter = asyncio.run(jalan())
    assert b.sales_account_id == "eksplisit" and b.inventory_account_id == A_PERS["id"] and b.cogs_account_id == A_HPP["id"] and b.purchase_account_id == A_BELI["id"]
    assert r == ["persediaan", "hpp", "pembelian"] and ter == {"kategori": "Minuman", "jenis": r}


def test_salin_jasa_atau_tanpa_stok_tak_menyalin_persediaan_dan_hpp():
    b = body(item_type="service", track_inventory=False)
    asyncio.run(KAK.salin_ke_barang(Konn(KAT), "t", b))
    assert b.inventory_account_id is None and b.cogs_account_id is None and b.sales_account_id == A_JUAL["id"] and b.purchase_account_id == A_BELI["id"]


def test_salin_kategori_tanpa_akun_atau_tak_ada_atau_barang_tanpa_kategori_tak_mengubah_apa_pun():
    for kat_, bd in ((dict.fromkeys(KAT), body()), (None, body()), (KAT, body(kategori=None)), (KAT, body(kategori="  "))):
        b = bd
        async def jalan():
            await KAK.salin_ke_barang(Konn(kat_), "t", b)
            return KAK.terakhir()
        assert asyncio.run(jalan()) is None
        assert (b.sales_account_id, b.purchase_account_id, b.inventory_account_id, b.cogs_account_id) == (None,) * 4


def test_salin_mengabaikan_akun_kategori_yang_kini_nonaktif_header_atau_tipe_salah():
    akun = {**AKUN, A_JUAL["id"]: {**A_JUAL, "is_active": False}, A_PERS["id"]: {**A_PERS, "is_header": True}, A_HPP["id"]: {**A_HPP, "account_type": "REVENUE"}}
    b = body()
    asyncio.run(KAK.salin_ke_barang(Konn(KAT, akun), "t", b))
    assert b.sales_account_id is None and b.inventory_account_id is None and b.cogs_account_id is None and b.purchase_account_id == A_BELI["id"]


def test_salin_tenant_filter_di_sql():
    k = Konn(KAT)
    asyncio.run(KAK.salin_ke_barang(k, "t", body()))
    assert all("tenant_id = $1" in s for s in k.sql)


# ---- kabel
def test_siapkan_buat_barang_menyalin_sebelum_bawaan_role():
    src = inspect.getsource(__import__("app.routers.items", fromlist=["x"])._siapkan_buat_barang)
    assert "KAK.salin_ke_barang(" in src and src.index("KAK.salin_ke_barang(") > src.index("kategori_bawaan(") and src.index("KAK.salin_ke_barang(") < src.index("_auto_default_product_accounts(")


def test_izin_rute_dan_inventaris_rute_tulis():
    mw = (APP / "middleware" / "permission_middleware.py").read_text()
    assert re.search(r"categories/\[\^/\]\+/\(rename\|status\|default\|merge\|parent\|accounts\)\(/preview\)\?\$", mw)
    inv = (APP.parent / "tests" / "unit" / "inventaris_rute_tulis.txt").read_text()
    assert "POST /api/items/categories/{category_id}/accounts\n" in inv and "POST /api/items/categories/{category_id}/accounts/preview\n" in inv


def test_migrasi_v505_aditif_dengan_pembalik():
    m = pathlib.Path(__file__).resolve().parents[3] / "migrations"
    s = (m / "V505__item_categories_akun.sql").read_text()
    assert s.count("ADD COLUMN IF NOT EXISTS") == 4 and "DROP" not in s and "NOT NULL" not in s
    assert (m / "V505__item_categories_akun_ROLLBACK.sql").read_text().count("DROP COLUMN IF EXISTS") == 4
