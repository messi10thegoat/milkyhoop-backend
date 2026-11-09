"""aksi_tersedia DETAIL kelompok BELI (gelombang 3, 10 Nov 2026; kontrak backend/docs/KONTRAK-jualbeli-aksi-standar-so-20261109.md §9): PO, GRN,
Faktur Pembelian, Pembayaran Keluar, Nota Debit, Uang Muka Pembelian. Diuji: kunci persis, satu transaksi READ ONLY, blok isian diabaikan, kalimat
yang diekstrak dipakai penulis (teks lama identik), petunjuk lama tetap (FPB aksi_tersedia.nota_debit level atas), detail memuat aksi_tersedia.
Paritas nyata vs penulis + kunci RO = scripts/ops/uji-salinan-beli-aksi.sh."""
import asyncio
import inspect
import uuid
from decimal import Decimal

import pytest

from app.routers import bill_payments as BP
from app.routers import bills as BL
from app.routers import goods_receipts as GR
from app.routers import purchase_orders as PO
from app.routers import vendor_credits as VC
from app.routers import vendor_deposits as VD
from app.services import aksi_detail as AD
from app.services import bayar_keluar_rencana as BKR
from app.services import uang_muka_po as UPO
from app.services import uang_muka_vendor_rencana as UVR

T = "kaos-biru-konveksi"
ID = uuid.uuid4()


def run(c):
    return asyncio.run(c)


class Kon:
    def __init__(self, baris=(), row=None, nilai=None):
        self.tx, self.baris, self.row, self.nilai = [], list(baris), row, nilai

    def transaction(self, **kw):
        kon = self

        class Tx:
            async def __aenter__(self):
                kon.tx.append(kw)

            async def __aexit__(self, *e):
                return False
        return Tx()

    async def fetchrow(self, sql, *a):
        return self.row

    async def fetch(self, sql, *a):
        return self.baris

    async def fetchval(self, sql, *a):
        return self.nilai


# ---------------------------------------------------------------- kunci persis (KUNCI §9–§14)
def test_kunci_persis():
    assert PO.AKSI_DETAIL_PO == ("pokirim", "pohapus", "poterima", "potagih", "potutup", "pobatal", "poum")
    assert GR.AKSI_DETAIL_GRN == ("grntagih", "grnbatal")
    assert BL.AKSI_DETAIL_FPB == ("fpbayar", "fpposting", "fphapus", "fpbatal", "fpnd")
    assert BP.AKSI_DETAIL_PK == ("pkposting", "pkhapus", "pkbatal")
    assert VC.AKSI_DETAIL_ND == ("ndposting", "ndterapkan", "ndkembali", "ndlepas", "ndbatal")
    assert VD.AKSI_DETAIL_UMP == ("umposting", "umhapus", "umterapkan", "umlepas", "umkembalikan", "umbatal")


def test_alasan_blok_terima_pengumpul_blok():
    class Pengumpul:
        daftar = [{"code": "ALASAN_WAJIB", "message": "isian"}, {"code": "SUDAH_DIBATALKAN", "message": "batal"}]
    assert AD.alasan_blok(Pengumpul(), None, kecuali=("ALASAN_WAJIB",)) == "batal"


# ---------------------------------------------------------------- kalimat diekstrak / baru
def test_rincian_lebih_teks_penulis_identik():
    from app.services import teks_galat as tg
    r = {"total": Decimal("1000"), "dibayar_dimuka": Decimal("1000"), "sisa": Decimal("0")}
    lama = (f"Jumlah {tg.rp(5)} melebihi sisa yang masih dapat dibayar di muka. Nilai pesanan {tg.rp(r['total'])}, "
            f"sudah dibayar di muka {tg.rp(r['dibayar_dimuka'])}, sisa yang masih dapat dibayar di muka {tg.rp(max(r['sisa'], UPO.NOL))}.")
    assert UPO.pesan_lebih(5, r) == lama
    assert lama.endswith(UPO.rincian_lebih(r))


def test_penerapan_um_tak_ada_dipakai_penulis():
    assert UVR.PENERAPAN_UM_TAK_ADA == "Penerapan uang muka tidak ditemukan."
    assert 'blok("PENERAPAN_TIDAK_DITEMUKAN", PENERAPAN_UM_TAK_ADA, 404)' in inspect.getsource(UVR.rencana_lepas)


def test_kalimat_tagihan_lunas_hanya_aksi_tersedia():
    assert BKR.kalimat_tagihan_lunas("INV-1") == "Faktur pembelian INV-1 sudah lunas."
    assert "kalimat_tagihan_lunas" not in inspect.getsource(BKR.rencana_bayar_tagihan)  # penulis tak berubah


# ---------------------------------------------------------------- perakitan (penentu dipalsukan)
@pytest.mark.parametrize("sisa,harap", [(Decimal("0"), "Faktur pembelian INV-9 sudah lunas."), (Decimal("10"), None)])
def test_fpbayar_lunas_dan_isian(monkeypatch, sisa, harap):
    async def bayar(c, tid, bid, req, pratinjau):
        assert req == {"amount": 0} and pratinjau is True
        return {"blocks": [{"code": "NOMINAL_TIDAK_POSITIF", "message": "isian"}, {"code": "REKENING_WAJIB", "message": "isian"}],
                "bill": {"invoice_number": "INV-9"}, "remaining": sisa}

    async def kosong(*a, **k):
        return {"blocks": []}

    async def nd(c, tid, bid):
        return {"nota_debit": {"tersedia": False, "alasan": "sudah ada ND"}}
    from app.services import tagihan_aksi as TA
    monkeypatch.setattr(BKR, "rencana_bayar_tagihan", bayar)
    monkeypatch.setattr(TA, "rencana_posting", kosong)
    monkeypatch.setattr(TA, "rencana_void", kosong)
    monkeypatch.setattr(TA, "ref_hapus", kosong)
    monkeypatch.setattr(TA, "blok_hapus", lambda b, r: [])
    monkeypatch.setattr(BL._ND, "aksi_tersedia_tagihan", nd)
    k = Kon()
    h = run(BL.aksi_tersedia_fpb(k, {"tenant_id": T}, ID))
    assert k.tx == [{"readonly": True}] and list(h) == list(BL.AKSI_DETAIL_FPB)
    assert h["fpbayar"] == {"tersedia": harap is None, "alasan": harap}
    assert h["fpnd"] == {"tersedia": False, "alasan": "sudah ada ND"}  # penentu YANG SAMA dgn aksi_tersedia.nota_debit level atas


@pytest.mark.parametrize("apps,blok,harap", [([], {}, "tak_ada"), ([{"id": 1}, {"id": 2}], {1: [{"code": "X", "message": "x"}], 2: []}, None),
                                             ([{"id": 1}], {1: [{"code": "SUDAH_DILEPAS", "message": "dilepas"}]}, "dilepas")])
def test_umlepas_penerapan_mana_pun(monkeypatch, apps, blok, harap):
    async def lepas(c, ctx, vd, app, hari, kunci, pratinjau):
        assert kunci is False and pratinjau is True
        return {"blocks": blok[app]}

    async def kosong(*a, **k):
        return {"blocks": [{"code": "VOID_REASON_REQUIRED", "message": "isian"}]}

    async def hari(c, tid):
        import datetime
        return datetime.date(2026, 11, 10)
    monkeypatch.setattr(UVR, "rencana_lepas", lepas)
    for n in ("rencana_posting", "rencana_hapus", "rencana_terapkan", "rencana_kembali", "rencana_batal"):
        monkeypatch.setattr(UVR, n, kosong)
    monkeypatch.setattr(VD, "_tanggal_dokumen", hari)
    h = run(VD.aksi_tersedia_ump(Kon(baris=apps, row={"id": ID}), {"tenant_id": T}, ID))
    assert h["umlepas"]["alasan"] == {"tak_ada": UVR.PENERAPAN_UM_TAK_ADA, None: None, "dilepas": "dilepas"}[harap]
    assert h["umbatal"]["tersedia"] is True      # VOID_REASON_REQUIRED = isian
    assert h["umterapkan"]["tersedia"] is True   # kode di luar KEADAAN_SALDO_UMP diabaikan


@pytest.mark.parametrize("sisa,harap_ada", [(Decimal("0"), True), (Decimal("100"), False)])
def test_poum_plafon_habis(monkeypatch, sisa, harap_ada):
    async def buat(c, ctx, body, pratinjau):
        assert body.amount == 0 and body.purchase_order_id == ID and pratinjau is True
        return {"blocks": [{"code": "REKENING_WAJIB", "message": "isian"}]}

    async def rinci(c, tid, po_id, kunci=False):
        assert kunci is False
        return {"total": Decimal("100"), "dibayar_dimuka": Decimal("100") - sisa, "sisa": sisa}

    async def kosong(*a, **k):
        return {"blocks": []}

    async def hari(c, tid):
        return None
    monkeypatch.setattr(UVR, "rencana_buat", buat)
    monkeypatch.setattr(UPO, "rincian_po", rinci)
    for n in ("rencana_kirim", "rencana_terima", "rencana_tagih", "rencana_tutup", "rencana_batal"):
        monkeypatch.setattr(PO.PR, n, kosong)
    monkeypatch.setattr(PO.PR, "rencana_hapus", lambda po: {"blocks": []})
    monkeypatch.setattr(PO, "tanggal_dokumen", hari)
    h = run(PO.aksi_tersedia_po(Kon(row={"id": ID, "vendor_id": uuid.uuid4()}), {"tenant_id": T}, ID))
    assert h["poum"]["tersedia"] is (not harap_ada)
    if harap_ada:
        assert h["poum"]["alasan"] == UPO.rincian_lebih({"total": Decimal("100"), "dibayar_dimuka": Decimal("100"), "sisa": Decimal("0")})


def test_grnbatal_alasan_isian_dan_ulang(monkeypatch):
    async def void(c, tid, gr, alasan, hari, kunci):
        assert kunci is False and alasan is None
        return {"blocks": [{"code": "ALASAN_WAJIB", "message": "isian"}], "ulang": False}

    async def tagih(c, po, items, kunci):
        assert kunci is False
        return {"blocks": [{"code": "TAK_ADA_SISA_TAGIH", "message": "Tidak ada baris yang belum ditagih."}]}

    async def hari(c, tid):
        return None
    from app.services import po_rencana as PR
    monkeypatch.setattr(GR.G, "rencana_void", void)
    monkeypatch.setattr(PR, "rencana_tagih", tagih)
    monkeypatch.setattr(GR, "tanggal_dokumen", hari)
    h = run(GR.aksi_tersedia_grn(Kon(row={"id": ID}), {"tenant_id": T}, ID))
    assert h == {"grntagih": {"tersedia": False, "alasan": "Tidak ada baris yang belum ditagih."},
                 "grnbatal": {"tersedia": True, "alasan": None}}


# ---------------------------------------------------------------- detail memuat aksi_tersedia (+ petunjuk lama)
def test_detail_memuat_aksi_tersedia_dan_petunjuk_lama():
    from app.schemas.aksi_tersedia import AksiTersediaPeta
    from app.schemas.bill_payments import BillPaymentDetail
    from app.schemas.purchase_orders import PurchaseOrderDetail
    from app.schemas.vendor_credits import VendorCreditDetail
    from app.schemas.vendor_deposits import VendorDepositDetailResponse
    for kelas in (PurchaseOrderDetail, BillPaymentDetail, VendorCreditDetail, VendorDepositDetailResponse):
        assert kelas.model_fields["aksi_tersedia"].annotation == AksiTersediaPeta
    for f, nama in ((PO.get_purchase_order, "aksi_tersedia_po"), (GR.get_goods_receipt, "aksi_tersedia_grn"),
                    (BP.get_bill_payment, "aksi_tersedia_pk"), (VC.get_vendor_credit, "aksi_tersedia_nd"),
                    (VD.get_vendor_deposit, "aksi_tersedia_ump")):
        assert f"await {nama}(" in inspect.getsource(f), nama
    for f in (BL.get_bill_detail, BL.get_bill_v2):  # FPB: di DALAM data + level atas lama tetap
        s = inspect.getsource(f)
        assert 'bill["aksi_tersedia"] = await aksi_tersedia_fpb(conn, ctx, bill_id)' in s
        assert '"aksi_tersedia": aksi_t}' in s and "_ND.aksi_tersedia_tagihan(conn" in s
