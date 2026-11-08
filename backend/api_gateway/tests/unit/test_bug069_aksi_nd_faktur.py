"""BUG-069 REOPEN (ROW p): detail faktur pembelian menawarkan aksi nota debit dari SERVER -- aksi_tersedia.nota_debit (SATU bentuk
schemas/aksi_tersedia) untuk faktur terposting TERMASUK LUNAS (jadi saldo kredit vendor); alasan bila tak tersedia. Penentu = blok
kepala yang SAMA dengan rakit_dari_tagihan (pratinjau ND dari faktur), jadi menu dan Tinjau tak bisa berbeda."""
import ast
import asyncio
import inspect
from decimal import Decimal

from app.routers import bills as RB, vendor_credits as RVC
from app.schemas.bills import BillDetailResponse
from app.services import nota_debit as ND

BID = "11111111-1111-4111-8111-111111111111"


def _bill(**kw):
    b = {"id": BID, "invoice_number": "BILL-2611-0003", "status_v2": "paid", "tax_inclusive": False, "dpp_manual": None,
         "invoice_discount_total": Decimal("0"), "cash_discount_total": Decimal("0")}
    b.update(kw)
    return b


class _K:
    def __init__(self, bill, ppn_baris=False):
        self.bill, self.ppn_baris = bill, ppn_baris

    async def fetchrow(self, sql, *a):
        return self.bill

    async def fetchval(self, sql, *a):
        return self.ppn_baris


def _alasan(bill, ppn_baris=False):
    return asyncio.run(ND.alasan_nd_dari_tagihan(_K(bill, ppn_baris), "t", BID))


def test_faktur_lunas_dan_terposting_menawarkan_nota_debit():
    for st in ("paid", "posted", "unpaid", "partial", "overdue"):
        assert _alasan(_bill(status_v2=st)) is None, st
    assert asyncio.run(ND.aksi_tersedia_tagihan(_K(_bill()), "t", BID)) == {"nota_debit": {"tersedia": True, "alasan": None}}


def test_draf_batal_dan_pajak_belum_didukung_beralasan():
    assert _alasan(_bill(status_v2="draft")) == "Faktur pembelian BILL-2611-0003 belum diposting atau sudah batal."
    assert _alasan(_bill(status_v2="void")) == "Faktur pembelian BILL-2611-0003 belum diposting atau sudah batal."
    assert "harga termasuk PPN" in _alasan(_bill(tax_inclusive=True))
    assert "DPP manual" in _alasan(_bill(dpp_manual=Decimal("1000")))
    assert "diskon dokumen" in _alasan(_bill(invoice_discount_total=Decimal("500")), ppn_baris=True)
    assert _alasan(_bill(invoice_discount_total=Decimal("500")), ppn_baris=False) is None
    assert _alasan(None) == "Faktur pembelian tidak ditemukan."
    a = asyncio.run(ND.aksi_tersedia_tagihan(_K(_bill(status_v2="void")), "t", BID))
    assert a["nota_debit"]["tersedia"] is False and a["nota_debit"]["alasan"]


def test_satu_penentu_dengan_pratinjau_nd_dari_faktur():
    # rakit_dari_tagihan (pratinjau/Tinjau) memakai blok kepala yang SAMA -> menu dan Tinjau tak bisa berbeda
    src = inspect.getsource(RVC.rakit_dari_tagihan)
    assert "blok_kepala_dari_tagihan(" in src and "ND_PAJAK_INKLUSIF_BELUM_DIDUKUNG" not in src
    assert "blok_kepala_dari_tagihan(" in inspect.getsource(ND.alasan_nd_dari_tagihan)


def test_detail_v1_dan_v2_mengirim_aksi_tersedia_bertipe():
    assert "aksi_tersedia" in BillDetailResponse.model_fields
    for fn in (RB.get_bill_detail, RB.get_bill_v2):
        pohon = ast.parse(inspect.getsource(fn).lstrip())
        dipanggil = {n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", None)
                     for n in ast.walk(pohon) if isinstance(n, ast.Call)}
        assert "aksi_tersedia_tagihan" in dipanggil, fn.__name__
        kunci = {k.value for n in ast.walk(pohon) if isinstance(n, ast.Dict) for k in n.keys if isinstance(k, ast.Constant)}
        assert "aksi_tersedia" in kunci, fn.__name__
