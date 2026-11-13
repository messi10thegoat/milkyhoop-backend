"""BUG-216 (putusan pemilik 13 Nov; MASTER opsi C terbatas): alamat kirim PO opsional + bawaan snapshot; warehouse_id HANYA bila gudang dipilih eksplisit."""
import asyncio
import inspect
import uuid

import pytest

from app.routers import purchase_orders as PO
from app.schemas.purchase_orders import UpdatePurchaseOrderRequest
from app.services import grn_rencana as G
from app.services import po_alamat_kirim as PAK
from app.services import po_rencana as PR

T = "kaos-biru-konveksi"
G1, G2, GU = (uuid.uuid4() for _ in range(3))
GUDANG = {str(G1): {"id": G1, "name": "Toko", "address": "Jl. Toko 1", "city": "Bandung", "is_active": True},
          str(G2): {"id": G2, "name": "Kosong", "address": None, "city": None, "is_active": True},
          str(GU): {"id": GU, "name": "Utama", "address": "Jl. Utama 9", "city": "Jakarta", "is_active": False}}
UTAMA = {"id": GU, "name": "Utama", "address": "Jl. Utama 9", "city": "Jakarta"}
USAHA = "Jl. Usaha 7, Surabaya"


def jalan(aw):
    return asyncio.run(aw)


class Kon:
    def __init__(self, utama=UTAMA, usaha=USAHA, gudang=None):
        self.utama, self.usaha, self.gudang, self.sql = utama, usaha, GUDANG if gudang is None else gudang, []

    async def fetchrow(self, sql, *a):
        self.sql.append(sql)
        if "is_default AND is_active" in sql:
            return self.utama
        if "FROM warehouses WHERE id = $1" in sql:
            return self.gudang.get(str(a[0]))
        raise AssertionError(sql)

    async def fetchval(self, sql, *a):
        self.sql.append(sql)
        assert 'FROM "Tenant"' in sql and a[0] == T
        return self.usaha

    async def fetch(self, sql, *a):
        return []                                           # baris PO (rencana_ubah): tanpa baris


def tentukan(pilih=None, manual=None, **kw):
    periksa = kw.pop("periksa_gudang", True)
    return jalan(PAK.tentukan(Kon(**kw), T, pilih, manual, periksa_gudang=periksa))


def test_manual_menang_walau_gudang_dipilih_dan_gudang_tetap_disimpan_bila_dipilih():
    a = tentukan(None, "  Jl. Manual 1 ")
    assert (a["ship_to_address"], a["sumber"], a["warehouse_id"]) == ("Jl. Manual 1", "Diisi manual", None)
    b = tentukan(G1, "Jl. Manual 2")
    assert (b["ship_to_address"], b["sumber"], b["warehouse_id"]) == ("Jl. Manual 2", "Diisi manual", G1)


def test_gudang_dipilih_beralamat_alamat_gudang_dan_disimpan():
    a = tentukan(G1)
    assert a["ship_to_address"] == "Jl. Toko 1\nBandung" and a["sumber"] == "Gudang Toko" and a["warehouse_id"] == G1 and a["blocks"] == []


def test_gudang_dipilih_tanpa_alamat_jatuh_ke_alamat_usaha_gudang_tetap_disimpan():
    a = tentukan(G2)
    assert (a["ship_to_address"], a["sumber"], a["warehouse_id"]) == (USAHA, "Alamat usaha", G2)


def test_tanpa_pilihan_gudang_utama_aktif_beralamat_dan_TIDAK_disimpan_ke_kolom():
    a = tentukan(None, None)
    assert a["ship_to_address"] == "Jl. Utama 9\nJakarta" and a["sumber"] == "Gudang Utama"
    assert a["warehouse_id"] is None                                                       # KUNCI: bawaan tak disimpan -> GRN tetap ikut gudang bawaan tenant terkini
    assert tentukan(None, "")["warehouse_id"] is None and tentukan(None, "   ")["sumber"] == "Gudang Utama"   # blank = bawaan


def test_tanpa_pilihan_utama_tanpa_alamat_atau_tanpa_utama_alamat_usaha_dan_tanpa_semua_kosong():
    tanpa_alamat = {"id": GU, "name": "Utama", "address": None, "city": None}
    assert tentukan(None, None, utama=tanpa_alamat)["sumber"] == "Alamat usaha"
    assert tentukan(None, None, utama=None)["ship_to_address"] == USAHA
    k = tentukan(None, None, utama=None, usaha=None)
    assert (k["ship_to_address"], k["sumber"], k["warehouse_id"]) == (None, None, None)
    assert tentukan(None, None, utama=None, usaha="  ")["ship_to_address"] is None            # alamat usaha kosong spasi = tak ada


def test_gudang_dipilih_tak_sah_tak_ada_nonaktif_diblok_dan_periksa_false_melonggarkan_nonaktif():
    assert [b[0] for b in tentukan("bukan-uuid")["blocks"]] == ["GUDANG_TIDAK_SAH"]
    assert [(b[0], b[2]) for b in tentukan(uuid.uuid4())["blocks"]] == [("GUDANG_TIDAK_DITEMUKAN", 422)]
    assert [b[0] for b in tentukan(GU)["blocks"]] == ["GUDANG_NONAKTIF"]
    assert tentukan(GU, periksa_gudang=False)["blocks"] == []                                  # gudang tersimpan yang kemudian nonaktif tak menghalangi ubah alamat


def test_sumber_tersimpan_diturunkan_saat_baca():
    s = lambda ship, wid, **kw: jalan(PAK.sumber_tersimpan(Kon(**kw), T, ship, wid))  # noqa: E731
    assert s("Jl. Toko 1\nBandung", G1) == "Gudang Toko"                                       # sama dgn gudang PO
    assert s("Jl. Utama 9\nJakarta", None) == "Gudang Utama"                                   # tanpa gudang PO: dibanding gudang utama
    assert s(USAHA, G1) == "Alamat usaha" and s(USAHA, None) == "Alamat usaha"
    assert s("Jl. Lain 3", G1) == "Diisi manual" and s(None, G1) is None and s("  ", None) is None


# ---------------------------------------------------------------- ubah / pratinjau ubah (rencana_ubah == penulis)
def po(status="draft", wid=None, ship=None):
    return {"id": uuid.uuid4(), "status": status, "warehouse_id": wid, "ship_to_address": ship, "po_number": "PO-1", "total_amount": 0}


def rencana(p, kon=None, **isi):
    return jalan(PR.rencana_ubah(kon or Kon(), T, p, UpdatePurchaseOrderRequest(**isi)))


def test_ubah_ganti_gudang_tanpa_alamat_ikut_alamat_gudang_baru_dan_perubahan_memuat_keduanya():
    r = rencana(po(wid=None, ship="Lama"), warehouse_id=str(G1))
    assert r["blocks"] == [] and r["alamat_kirim_sumber"] == "Gudang Toko"
    assert r["data_efektif"] == {"warehouse_id": str(G1), "ship_to_address": "Jl. Toko 1\nBandung"}
    assert {p["field"] for p in r["perubahan"]} == {"warehouse_id", "ship_to_address"}


def test_ubah_alamat_manual_menang_gudang_tak_berubah_dan_blank_isi_ulang_bawaan():
    r = rencana(po(wid=G1, ship="Lama"), ship_to_address="Jl. Baru 5")
    assert r["data_efektif"] == {"ship_to_address": "Jl. Baru 5", "warehouse_id": str(G1)} and r["alamat_kirim_sumber"] == "Diisi manual"
    assert [p["field"] for p in r["perubahan"]] == ["ship_to_address"]                            # gudang sama: bukan perubahan
    b = rencana(po(wid=G1, ship="Jl. Baru 5"), ship_to_address="")
    assert b["data_efektif"]["ship_to_address"] == "Jl. Toko 1\nBandung" and b["alamat_kirim_sumber"] == "Gudang Toko"   # blank = bawaan gudang PO


def test_ubah_gudang_null_mengosongkan_dan_isi_ulang_bawaan_dan_gudang_sama_tanpa_perubahan():
    r = rencana(po(wid=G1, ship="Jl. Toko 1\nBandung"), warehouse_id=None)
    assert r["data_efektif"]["warehouse_id"] is None and r["data_efektif"]["ship_to_address"] == "Jl. Utama 9\nJakarta"
    n = rencana(po(wid=G1, ship="X"), warehouse_id=str(G1))
    assert n["blocks"] == [] and n["perubahan"] == [] and n["alamat_kirim_sumber"] is None   # tak ada perubahan gudang -> alamat tak disentuh


def test_ubah_gudang_hanya_saat_draf_alamat_manual_boleh_saat_terkirim():
    r = rencana(po(status="sent", wid=None), warehouse_id=str(G1))
    assert [b["code"] for b in r["blocks"]] == ["PO_GUDANG_HANYA_DRAF"]
    assert rencana(po(status="sent", wid=G1), ship_to_address="Jl. Manual")["blocks"] == []
    assert [b["code"] for b in rencana(po(), warehouse_id=str(uuid.uuid4()))["blocks"]] == ["GUDANG_TIDAK_DITEMUKAN"]
    assert rencana(po(wid=GU, ship="Jl. Utama 9\nJakarta"), ship_to_address="Baru")["blocks"] == []   # gudang tersimpan nonaktif tak menghalangi ubah alamat


# ---------------------------------------------------------------- GRN tak berubah (pagar MASTER)
class KonGRN:
    def __init__(self):
        self.row = {"id": G1, "name": "Toko", "is_active": True}

    async def fetchrow(self, sql, *a):
        assert "FROM warehouses WHERE id = $1" in sql
        return self.row if a[0] == G1 else None


def test_grn_po_tanpa_gudang_mengikuti_bawaan_tenant_terkini_po_dengan_gudang_memakai_gudang_po(monkeypatch):
    bawaan = {"id": GU, "name": "Utama kini", "is_active": True}

    async def b(conn, tid):
        return bawaan
    monkeypatch.setattr(G.gudang_bawaan, "bawaan", b)
    assert jalan(G._gudang(KonGRN(), T, None, None)) is bawaan                                # PO tanpa gudang: bawaan tenant TERKINI
    assert jalan(G._gudang(KonGRN(), T, None, G1))["id"] == G1                                # PO dengan gudang dipilih: gudang itu
    assert jalan(G._gudang(KonGRN(), T, G2, G1)) is None                                      # diminta menang atas gudang PO (tetap bisa diganti di GRN)


def test_titik_pasang_buat_ubah_detail_dan_tes_205():
    c = inspect.getsource(PO.create_purchase_order)
    assert "_alamat = await PAK.tentukan(conn, ctx[\"tenant_id\"], body.warehouse_id, body.ship_to_address)" in c
    assert "$16, $17, $18)" in c and re.search(r'terms,\s*_alamat\["warehouse_id"\],', c) and re.search(r'^\s*_alamat\["ship_to_address"\],', c, re.M)
    assert '"alamat_kirim_sumber": _alamat["sumber"],' in c and "body.ship_to_address,\n" not in c
    u = inspect.getsource(PO.update_purchase_order)
    assert 'update_data = _rc["data_efektif"]' in u and 'field in ("vendor_id", "warehouse_id")' in u
    d = inspect.getsource(PO.get_purchase_order)
    assert '"alamat_kirim_sumber": await PAK.sumber_tersimpan(' in d and '"warehouse_id": str(po["warehouse_id"])' in d
    p = inspect.getsource(PO.preview_edit_purchase_order)
    assert 'out["alamat_kirim_sumber"] = r["alamat_kirim_sumber"]' in p


import re  # noqa: E402  (dipakai tes di atas)
