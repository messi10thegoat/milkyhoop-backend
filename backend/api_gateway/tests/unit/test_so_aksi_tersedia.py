"""aksi_tersedia DETAIL SO (gelombang 3 J1a, 9 Nov 2026; kontrak backend/docs/KONTRAK-jualbeli-aksi-standar-so-20261109.md §2/§5/§6).
Diuji: kunci persis (jenis tulis/tulis·kilat), satu transaksi READ ONLY, blok isian bukan "tak tersedia", penjaga yang diekstrak
dipanggil PENULIS juga (teks lama identik), saklar kunci tutup (kunci=True = jalur lama), kirim dua penulis. Paritas nyata vs penulis
dan pg_locks = scripts/ops/uji-salinan-so-aksi.sh."""
import asyncio
import inspect
import uuid

import pytest
from fastapi import HTTPException

from app.routers import customer_deposits as CD
from app.routers import proformas as PF
from app.routers import sales_orders as SO
from app.services import aksi_detail as AD
from app.services import kode_order as KO
from app.services import so_ubah_terkonfirmasi as SUT

T = "kaos-biru-konveksi"
OID = uuid.uuid4()


def run(c):
    return asyncio.run(c)


# ---------------------------------------------------------------- kunci + helper
def test_kunci_persis_urut_dokumen_kunci():
    assert SO.AKSI_DETAIL_SO == ("konfirmasi", "ubahkepala", "batal", "hapus", "faktur", "dp", "kirim", "bayar", "proforma", "tutup",
                                 "terbitfaktur", "fasekirim", "fasetersedia", "faseubah", "spk")
    assert "ubah" not in SO.AKSI_DETAIL_SO and "wa" not in SO.AKSI_DETAIL_SO  # navigasi = FE


def test_alasan_blok_abaikan_isian():
    blok = [{"code": "RP_ACCOUNT_REQUIRED", "message": "Pilih rekening penerimaan."},
            {"code": "RP_NOTHING_DUE", "message": "Semua faktur pesanan ini sudah lunas."}]
    assert AD.alasan_blok(blok, SO.KEADAAN_BAYAR) == "Semua faktur pesanan ini sudah lunas."
    assert AD.alasan_blok(blok[:1], SO.KEADAAN_BAYAR) is None
    assert AD.alasan_blok(blok[:1], None) == "Pilih rekening penerimaan."
    assert AD.alasan_blok([{"code": "X", "detail": {"code": "X", "message": "m"}}], None) == "m"


def test_alasan_galat_sinkron_dan_async():
    def sinkron():
        raise HTTPException(400, "teks")

    async def asinkron():
        raise HTTPException(409, {"code": "K", "message": "pesan"})
    assert run(AD.alasan_galat(sinkron)) == "teks"
    assert run(AD.alasan_galat(asinkron)) == "pesan"
    assert run(AD.alasan_galat(lambda: None)) is None


class KonRO:
    def __init__(self):
        self.tx = []

    def transaction(self, **kw):
        kon = self

        class Tx:
            async def __aenter__(self):
                kon.tx.append(kw)

            async def __aexit__(self, *e):
                return False
        return Tx()


def test_nilai_satu_transaksi_read_only():
    k = KonRO()

    async def a(c):
        return None

    async def b(c):
        return "alasan b"
    h = run(AD.nilai(k, [("a", a), ("b", b)]))
    assert k.tx == [{"readonly": True}]
    assert list(h) == ["a", "b"]
    assert h == {"a": {"tersedia": True, "alasan": None}, "b": {"tersedia": False, "alasan": "alasan b"}}


# ---------------------------------------------------------------- penjaga yang diekstrak (penulis memanggil yang SAMA)
@pytest.mark.parametrize("status", ["draft", "cancelled", "invoiced", "completed"])
def test_penjaga_tagih_so_teks_lama(status):
    from app.services import teks_galat as tg
    with pytest.raises(HTTPException) as e:
        SO.penjaga_tagih_so({"status": status})
    assert (e.value.status_code, e.value.detail) == (400, tg.tak_bisa_status("so", status, "ditagih"))


@pytest.mark.parametrize("status", ["confirmed", "partial_shipped", "shipped", "partial_invoiced"])
def test_penjaga_tagih_so_lolos(status):
    SO.penjaga_tagih_so({"status": status})


def test_penulis_to_invoice_memakai_penjaga_dan_sql_sama():
    src = inspect.getsource(SO._buat_faktur_dari_so)
    assert "penjaga_tagih_so(order)" in src and "SQL_BARIS_SISA_TAGIH" in src and "TAK_ADA_TAGIH" in src
    assert '("draft", "cancelled", "invoiced", "completed")' not in src


def test_penjaga_dp_status_dan_rincian_plafon_teks_lama():
    from app.services import teks_galat as tg
    with pytest.raises(HTTPException) as e:
        CD.penjaga_status_dp({"status": "draft", "order_number": "SO-1"})
    assert e.value.detail["code"] == "SO_NOT_ACCEPTING_DEPOSIT"
    CD.penjaga_status_dp({"status": "confirmed", "order_number": "SO-1"})
    lama = (f"Jumlah {tg.rp(5)} melebihi sisa yang masih dapat diterima. Nilai pesanan {tg.rp(10)}, sudah diterima "
            f"{tg.rp(8)}, sisa yang masih dapat diterima {tg.rp(2)}.")
    assert f"Jumlah {tg.rp(5)} melebihi sisa yang masih dapat diterima. {CD.rincian_plafon(10, 8, 2)}" == lama
    src = inspect.getsource(CD.assert_deposit_within_order_total)
    assert "penjaga_status_dp(order)" in src and "rincian_plafon(" in src


def test_blok_so_proforma():
    assert PF.blok_so_proforma({"status": "confirmed"}) is None
    b = PF.blok_so_proforma({"status": "draft"})
    assert b["code"] == "SO_NOT_BILLABLE" and b["message"].endswith("dengan proforma. Konfirmasi pesanannya dulu.")
    assert "blok_so_proforma(order)" in inspect.getsource(PF._rencana_buat)


def test_penjaga_spk():
    s = {"label": "No. Job", "enabled": True}
    assert KO.penjaga_spk(s, {"order_code": None, "status": "confirmed"}) is None
    assert KO.penjaga_spk(s, {"order_code": "J-1", "status": "cancelled"}) is None  # berkode = idempoten (can_proceed)
    assert KO.penjaga_spk(s, {"order_code": None, "status": "draft"})["code"] == "SPK_STATUS_TIDAK_SAH"
    assert KO.pesan_spk_nonaktif(s) == "No. Job belum dinyalakan di Pengaturan."
    src = inspect.getsource(KO.terbitkan_spk)
    assert "penjaga_spk(s, so)" in src and "pesan_spk_nonaktif(s)" in src
    assert "penjaga_spk(s, so)" in inspect.getsource(KO.perkiraan_spk)


@pytest.mark.parametrize("status,flag,kode", [("draft", False, "SO_IS_DRAFT"), ("confirmed", False, "SO_EDIT_NOT_ENABLED"),
                                              ("cancelled", True, "SO_NOT_EDITABLE"), ("confirmed", True, None)])
def test_penjaga_ubah_urutan_lama(monkeypatch, status, flag, kode):
    async def f(conn, tid):
        return flag
    monkeypatch.setattr(SUT, "flag_aktif", f)
    if kode is None:
        run(SUT.penjaga_pratinjau(None, T, status))
    else:
        with pytest.raises(HTTPException) as e:
            run(SUT.penjaga_pratinjau(None, T, status))
        assert e.value.detail["code"] == kode
    assert "penjaga_status(so[\"status\"])" in inspect.getsource(SUT.ubah)
    assert "_sut.penjaga_pratinjau(conn, ctx[\"tenant_id\"], so[\"status\"])" in inspect.getsource(SO.preview_edit_sales_order)


# ---------------------------------------------------------------- saklar kunci tutup
class KonTutup:
    def __init__(self):
        self.exe = []

    async def execute(self, sql, *a):
        self.exe.append(sql)

    async def fetchrow(self, sql, *a):
        return {"id": OID, "status": "confirmed", "order_number": "SO-1"}

    async def fetch(self, sql, *a):
        return []


@pytest.mark.parametrize("kunci,harap", [(True, 1), (False, 0)])
def test_tutup_saklar_kunci(monkeypatch, kunci, harap):
    async def kosong(*a, **k):
        return []
    monkeypatch.setattr(CD, "linked_so_deposits", kosong)
    k = KonTutup()
    r = run(SO._rencana_tutup_so(k, {"tenant_id": T}, str(OID), None, kunci=kunci))
    assert sum("pg_advisory_xact_lock" in x for x in k.exe) == harap
    assert r["blocks"] == []
    assert inspect.signature(SO._rencana_tutup_so).parameters["kunci"].default is True  # penulis & pratinjau = jalur lama


# ---------------------------------------------------------------- perakitan aksi_tersedia_so (penentu dipalsukan)
@pytest.fixture
def palsu(monkeypatch):
    from app.services import sj_so as SJ, so_fase_lokasi as FL, so_pelunasan as PL, so_pengiriman as PG
    st = {"sj": False, "kirim": [], "sj_blok": [], "bayar": [], "tutup": [], "batal": [], "hapus": [], "fase": [],
          "dp_terima": 0, "spk": {"enabled": True, "label": "No. Job"}, "draf": False, "sisa": True}

    async def konfirmasi(conn, ctx, oid, kunci):
        assert kunci is False
        return {}

    async def batal(conn, ctx, oid, reason):
        return {"blocks": st["batal"]}

    async def hapus(conn, ctx, oid):
        return st["hapus"]

    async def tutup(conn, ctx, oid, reason, kunci=True):
        assert kunci is False and reason is None
        return {"blocks": st["tutup"]}

    async def kirim(conn, ctx, oid, body):
        return {"blocks": st["kirim"]}

    async def sj_aktif(conn, tid):
        return st["sj"]

    async def sj_rencana(conn, ctx, oid, body, kunci=False):
        assert kunci is False
        return {"blocks": st["sj_blok"]}

    async def bayar(conn, ctx, oid, body):
        return {"blocks": st["bayar"]}

    async def fase(conn, ctx, oid, f, g, kunci=False):
        assert kunci is False
        return {"blocks": st["fase"]}

    async def penjaga(conn, tid, status):
        return None

    async def terima(conn, tid, oid, exclude_id=None):
        return st["dp_terima"]

    async def setelan(conn, tid):
        return st["spk"]
    monkeypatch.setattr(SO, "rencana_konfirmasi_so", konfirmasi)
    monkeypatch.setattr(SO, "_rencana_batal_so", batal)
    monkeypatch.setattr(SO, "rencana_hapus_so", hapus)
    monkeypatch.setattr(SO, "_rencana_tutup_so", tutup)
    monkeypatch.setattr(PG, "rencana_kirim_so", kirim)
    monkeypatch.setattr(SJ, "aktif", sj_aktif)
    monkeypatch.setattr(SJ, "rencana", sj_rencana)
    monkeypatch.setattr(PL, "rencana_pelunasan_so", bayar)
    monkeypatch.setattr(FL, "rencana", fase)
    monkeypatch.setattr(SUT, "penjaga_pratinjau", penjaga)
    monkeypatch.setattr(CD, "received_total_for_order", terima)
    monkeypatch.setattr(KO, "muat_setelan", setelan)

    class Kon(KonRO):
        async def fetchval(self, sql, *a):
            if "sales_order_items" in sql:
                return st["sisa"]
            assert "status = 'draft'" in sql and a == (T, OID)
            return st["draf"]
    return st, Kon()


def _so(status="confirmed", total=100):
    return {"id": OID, "status": status, "order_number": "SO-1", "total_amount": total, "order_code": None}


def test_rakit_semua_tersedia_dan_isian_diabaikan(palsu):
    st, k = palsu
    st["draf"] = True
    st["tutup"] = [{"code": "SO_NOT_FULLY_INVOICED", "message": "alasan wajib"}]          # isian
    st["kirim"] = [{"code": "SO_FULFILL_STOCK_SHORT", "message": "stok"}]                # isian (qty bisa diubah)
    st["bayar"] = [{"code": "RP_ACCOUNT_REQUIRED", "message": "Pilih rekening penerimaan."}]
    st["fase"] = [{"code": "FASE_GUDANG_PASANGAN", "message": "pasangan"}]
    h = run(SO.aksi_tersedia_so(k, {"tenant_id": T}, _so()))
    assert list(h) == list(SO.AKSI_DETAIL_SO) and k.tx == [{"readonly": True}]
    assert all(v == {"tersedia": True, "alasan": None} for v in h.values()), h


def test_rakit_tak_tersedia_alasan_penentu(palsu):
    st, k = palsu
    st.update(sisa=False, dp_terima=100, spk={"enabled": False, "label": "No. Job"},
              tutup=[{"code": "SO_REVENUE_NOT_RECOGNIZED", "message": "tertahan"}],
              bayar=[{"code": "RP_NOTHING_DUE", "message": "Semua faktur pesanan ini sudah lunas."}],
              batal=[{"code": "SO_HAS_SHIPMENTS_OR_INVOICES", "message": "sudah kirim"}],
              hapus=[{"code": "SO_BUKAN_DRAF", "message": "Hanya pesanan berstatus Draf yang bisa dihapus."}],
              fase=[{"code": "SO_STATUS_TIDAK_BOLEH", "message": "tutup"}],
              kirim=[{"code": "SO_FULFILL_NO_INVOICE", "message": "belum faktur"}])
    h = run(SO.aksi_tersedia_so(k, {"tenant_id": T}, _so()))
    harap = {"faktur": SO.TAK_ADA_TAGIH, "tutup": "tertahan", "bayar": "Semua faktur pesanan ini sudah lunas.", "batal": "sudah kirim",
             "hapus": "Hanya pesanan berstatus Draf yang bisa dihapus.", "fasekirim": "tutup", "fasetersedia": "tutup", "faseubah": "tutup",
             "kirim": "belum faktur", "terbitfaktur": SO.SI_BUKAN_DRAF, "spk": "No. Job belum dinyalakan di Pengaturan.",
             "dp": CD.rincian_plafon(100.0, 100, 0.0)}
    for kunci, alasan in harap.items():
        assert h[kunci] == {"tersedia": False, "alasan": alasan}, kunci


def test_rakit_status_draf(palsu):
    st, k = palsu
    h = run(SO.aksi_tersedia_so(k, {"tenant_id": T}, _so("draft")))
    for kunci in ("faktur", "dp", "proforma", "spk"):
        assert h[kunci]["tersedia"] is False, kunci


@pytest.mark.parametrize("sj,sj_blok,harap", [
    (False, [], False),                                                            # flag mati: hanya jalur faktur
    (True, [], True),                                                              # SJ tanpa faktur terbuka
    (True, [{"code": "SJ_SO_TANPA_SISA", "message": "tanpa sisa"}], False),
    (True, [{"code": "SJ_SO_GUDANG_WAJIB", "message": "Pilih gudang asal barang."}], True),   # isian
])
def test_kirim_dua_penulis(palsu, sj, sj_blok, harap):
    st, k = palsu
    st.update(sj=sj, sj_blok=sj_blok, kirim=[{"code": "SO_FULFILL_NO_INVOICE", "message": "belum faktur"}])
    h = run(SO.aksi_tersedia_so(k, {"tenant_id": T}, _so()))["kirim"]
    assert h["tersedia"] is harap
    if not harap:
        assert h["alasan"] == ("tanpa sisa" if sj else "belum faktur")


def test_detail_memuat_aksi_tersedia_di_dalam_data():
    from app.schemas.sales_orders import SalesOrderDetail
    from app.schemas.aksi_tersedia import AksiTersediaPeta
    assert SalesOrderDetail.model_fields["aksi_tersedia"].annotation == AksiTersediaPeta
    assert "aksi_tersedia=_aksi_tersedia" in inspect.getsource(SO.get_sales_order_detail)
