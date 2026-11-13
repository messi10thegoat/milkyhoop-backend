"""BUG-204 (putusan pemilik 13 Nov 2026): aturan baris Penyesuaian Stok dari SATU penentu (SS.susun_baris) untuk pratinjau DAN tulis: jenis menentukan tanda; non-stok,
selisih 0, barang ganda, fisik wajib/negatif DITOLAK (blok berkode); harga pokok 0 = isyarat. Paritas nyata = uji salinan."""
import asyncio
import uuid
from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from app.services import stok_sesuai as SS
from app.services.bayar_keluar_rencana import Blok
from app.services.galat_berkode import GalatBerkode

T = "kaos-biru-konveksi"
G = uuid.uuid4()
A, B, C = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


def jalan(aw):
    return asyncio.run(aw)


class Kon:
    """Tiruan asyncpg: produk A ber-stok (stok gudang 5, harga pokok 1000), B ber-stok TANPA harga pokok, C non-stok."""
    BRG = {A: ("Kaos", 1000), B: ("Benang", 0), C: ("Jasa jahit", 500)}

    async def fetchrow(self, sql, *a):
        if "FROM products p" in sql:
            n, h = self.BRG[a[0]]
            return {"id": a[0], "name": n, "unit": "Pcs", "code": "K", "purchase_price": h}
        if "SELECT item_type, track_inventory FROM products" in sql:
            return {"item_type": "service" if a[0] == C else "goods", "track_inventory": a[0] != C}
        return None

    async def fetchval(self, sql, *a):
        if "SELECT average_cost FROM inventory_ledger" in sql:
            return self.BRG[a[1]][1]
        if "warehouse_id = $3" in sql:
            return D("5")
        return D("5")


def baris(pid, qty=None, fisik=None):
    return SimpleNamespace(product_id=str(pid), quantity_adjustment=D(str(qty if qty is not None else 0)), physical_quantity=None if fisik is None else D(str(fisik)), reason_detail=None)


def susun(tipe, items, pratinjau=True):
    bl, isy = Blok(pratinjau), []
    hasil = jalan(SS.susun_baris(Kon(), T, items, tipe, G, bl, isy))
    return hasil, bl.daftar, isy


def kode(bl):
    return [b["code"] for b in bl]


@pytest.mark.parametrize("tipe,qty,ok", [("increase", 2, True), ("increase", -2, False), ("decrease", -2, True), ("decrease", 2, False), ("damaged", -1, True),
                                         ("damaged", 1, False), ("expired", -3, True), ("expired", 3, False)])
def test_jenis_menentukan_tanda(tipe, qty, ok):
    h, bl, _ = susun(tipe, [baris(A, qty)])
    if ok:
        assert bl == [] and h[0]["quantity_adjustment"] == D(str(qty))
    else:
        assert kode(bl) == ["PENYESUAIAN_TANDA_TIDAK_COCOK"] and h == []
        assert "harus" in bl[0]["message"] and "Kaos" in bl[0]["message"]


def test_kalimat_tanda_per_arah():
    assert "positif (menambah stok)" in susun("increase", [baris(A, -1)])[1][0]["message"]
    assert "negatif (mengurangi stok)" in susun("damaged", [baris(A, 1)])[1][0]["message"] and "Rusak" in susun("damaged", [baris(A, 1)])[1][0]["message"]


@pytest.mark.parametrize("tipe", ["increase", "decrease", "damaged", "expired"])
def test_selisih_nol_ditolak(tipe):
    h, bl, _ = susun(tipe, [baris(A, 0)])
    assert kode(bl) == ["PENYESUAIAN_SELISIH_NOL"] and h == [] and "nol" in bl[0]["message"]


def test_hitung_ulang_fisik_wajib_negatif_dan_selisih_nol():
    assert kode(susun("recount", [baris(A, 0)])[1]) == ["PENYESUAIAN_FISIK_WAJIB"]                    # hitung ulang lewat physical_quantity
    assert kode(susun("recount", [baris(A, 0, fisik=-1)])[1]) == ["PENYESUAIAN_FISIK_NEGATIF"]
    assert kode(susun("recount", [baris(A, 0, fisik=5)])[1]) == ["PENYESUAIAN_SELISIH_NOL"]            # fisik == stok sistem (5)
    h, bl, _ = susun("recount", [baris(A, 0, fisik=3)])
    assert bl == [] and h[0]["quantity_adjustment"] == D("-2") and h[0]["system_quantity"] == D("5") and h[0]["physical_quantity"] == D("3")
    h2, bl2, _ = susun("recount", [baris(A, 0, fisik=0)])                                               # fisik 0 sah (stok dikosongkan)
    assert bl2 == [] and h2[0]["quantity_adjustment"] == D("-5")


def test_barang_non_stok_ditolak():
    h, bl, _ = susun("increase", [baris(C, 1)])
    assert kode(bl) == ["PENYESUAIAN_BARANG_NON_STOK"] and h == [] and bl[0]["status"] == 409 and "tidak melacak stok" in bl[0]["message"]


def test_barang_ganda_ditolak_baris_kedua():
    h, bl, _ = susun("increase", [baris(A, 1), baris(A, 2)])
    assert kode(bl) == ["PENYESUAIAN_BARANG_GANDA"] and len(h) == 1 and "baris 2" in bl[0]["message"]


def test_harga_pokok_nol_isyarat_bukan_blok():
    h, bl, isy = susun("increase", [baris(B, 4)])
    assert bl == [] and h[0]["unit_cost"] == 0 and h[0]["total_value"] == D("0")
    assert isy == ["Barang Benang belum punya harga pokok; nilai Rp 0."]
    assert susun("increase", [baris(A, 4)])[2] == []                                                    # harga ada -> tanpa isyarat


def test_pratinjau_mengumpulkan_semua_blok_tulis_melempar_blok_pertama():
    items = [baris(A, -1), baris(C, 1), baris(B, 0)]
    _, bl, _ = susun("increase", items)
    assert kode(bl) == ["PENYESUAIAN_TANDA_TIDAK_COCOK", "PENYESUAIAN_BARANG_NON_STOK", "PENYESUAIAN_SELISIH_NOL"]
    with pytest.raises(GalatBerkode) as e:                                                              # tulis = blok pertama, kode + kalimat SAMA dgn pratinjau
        susun("increase", items, pratinjau=False)
    assert (e.value.code, e.value.status_code, e.value.detail) == (bl[0]["code"], 422, bl[0]["message"])


def test_rencana_buat_dan_ubah_membawa_isyarat(monkeypatch):
    async def g(conn, tid, wid, bl):
        return {"id": G, "name": "Utama", "is_active": True}
    monkeypatch.setattr(SS, "gudang", g)
    body = SimpleNamespace(warehouse_id=None, adjustment_type="increase", items=[baris(B, 2)])
    r = jalan(SS.rencana_buat(Kon(), T, body, Blok(True)))
    assert r["isyarat"] == ["Barang Benang belum punya harga pokok; nilai Rp 0."] and r["blocks"] == []


# ---------------- total_value pratinjau buat/ubah (FE tak menjumlah)
class _Pool:
    def acquire(self):
        class Ac:
            async def __aenter__(s):
                return s

            async def __aexit__(s, *e):
                return False

            def transaction(s, readonly=False):
                class Tx:
                    async def __aenter__(s2):
                        return None

                    async def __aexit__(s2, *e):
                        return False
                return Tx()
        return Ac()


def _pratinjau(r):
    async def rencana(conn):
        return r
    return jalan(SS.pratinjau(_Pool(), T, rencana, {}))["data"]


def _baris(nilai):
    return {"product_id": A, "product_name": "Kaos", "unit": "Pcs", "quantity_before": D("5"), "quantity_adjustment": D("1"), "quantity_after": D("6"),
            "unit_cost": 1000, "total_value": D(str(nilai))}


DASAR = {"blocks": [], "gudang": None, "baris": [], "saldo": [], "isyarat": []}


def test_pratinjau_buat_memuat_total_value_jumlah_nilai_baris():
    d = _pratinjau({**DASAR, "baris_draf": [_baris(1000), _baris(500)]})
    assert d["total_value"] == "1500.00" and [b["total_value"] for b in d["baris_draf"]] == ["1000.00", "500.00"]


def test_pratinjau_ubah_tanpa_items_total_tersimpan_dan_dengan_items_jumlah_baru():
    assert _pratinjau({**DASAR, "baris_draf": None, "sa": {"total_value": 3000}})["total_value"] == "3000.00"
    assert _pratinjau({**DASAR, "baris_draf": [_baris(250)], "sa": {"total_value": 3000}})["total_value"] == "250.00"


def test_pratinjau_ditolak_atau_bukan_buat_ubah_tanpa_total_value():
    assert "total_value" not in _pratinjau({**DASAR, "blocks": [{"code": "X", "message": "x"}], "baris_draf": [_baris(1000)]})
    assert "total_value" not in _pratinjau({**DASAR, "gerak": []})                  # posting/batal/hapus: tanpa kunci baris_draf
