"""U-AP-SEN (putusan pemilik LANGSUNG 2 Nov 2026 "samakan Penjualan"): bayar tagihan (Pembelian) menerima SEN -- Decimal 2 desimal,
sisa tanpa int() (pola SI 6b). Nilai BULAT: JSON masukan/keluaran byte-identik dengan int lama (UangMasuk/UangJson) -> perilaku hari ini
tetap (dibuktikan SETARA perekam di gerbang). Kolom bill_payment_allocations.remaining_before/after -> numeric(18,2) (V473)."""
import ast
import asyncio
import inspect
import json
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas import bill_payments as SBP
from app.schemas import bills as SB
from app.services import bayar_keluar_rencana as BKR

MIG = Path(__file__).resolve().parents[3] / "migrations"


def test_rupiah_desimal_sen_utuh_bulat_eksponen_nol():
    assert BKR.rupiah_desimal(Decimal("274.50")) == Decimal("274.50") and str(BKR.rupiah_desimal(Decimal("274.50"))) == "274.50"
    assert str(BKR.rupiah_desimal(Decimal("100000.00"))) == "100000"
    assert BKR.rupiah_desimal(None) == 0 and BKR.rupiah_desimal(0) == 0


def _bayar(**k):
    return SB.RecordPaymentRequest(payment_method="transfer", bank_account_id=uuid4(), **k)


def test_masukan_bayar_tagihan_terima_sen_tolak_lebih_dua_desimal():
    assert _bayar(amount="274.50").amount == Decimal("274.50")
    assert _bayar(amount=1000, pph_amount="2.75").pph_amount == Decimal("2.75")
    with pytest.raises(ValidationError):
        _bayar(amount="1.005")
    with pytest.raises(ValidationError):
        _bayar(amount=0)
    # bulat: JSON byte-identik dengan int lama (sidik idempotensi /bills/{id}/payments tak bergeser)
    d = _bayar(amount=50000).model_dump(mode="json")
    assert json.dumps(d["amount"]) == "50000" and json.dumps(d["pph_amount"]) == "0"
    assert json.dumps(_bayar(amount="274.50").model_dump(mode="json")["amount"]) == "274.5"


def test_masukan_bill_payments_terima_sen():
    a = SBP.BillAllocationInput(bill_id=str(uuid4()), amount_applied="274.50")
    assert a.amount_applied == Decimal("274.50")
    f = SBP.CreateBillPaymentRequest.model_fields
    for k in ("total_amount", "discount_amount", "bank_fee_amount"):
        assert Decimal in getattr(f[k].annotation, "__args__", (f[k].annotation,)) or f[k].annotation is not int, k
    u = SBP.UpdateBillPaymentRequest.model_fields
    for k in ("total_amount", "discount_amount", "bank_fee_amount"):
        assert "int]" not in str(u[k].annotation) and u[k].annotation is not int, k
    with pytest.raises(ValidationError):
        SBP.BillAllocationInput(bill_id=str(uuid4()), amount_applied="1.001")


def test_respons_bulat_int_bersen_float():
    r = SBP.BillAllocationResponse(id="a", bill_id="b", bill_number="PB-1", bill_amount=Decimal("1000.00"),
                                   remaining_before=Decimal("274.50"), amount_applied=Decimal("274.50"), remaining_after=Decimal("0.00"))
    assert json.loads(r.model_dump_json()) == {"id": "a", "bill_id": "b", "bill_number": "PB-1", "bill_amount": 1000,
                                               "remaining_before": 274.5, "amount_applied": 274.5, "remaining_after": 0}
    assert '"bill_amount":1000,' in r.model_dump_json()  # bulat = int, byte-identik dengan dulu
    for kelas, medan in ((SBP.BillPaymentListItem, ("total_amount", "allocated_amount", "unapplied_amount")),
                         (SBP.BillPaymentDetail, ("total_amount", "allocated_amount", "unapplied_amount", "discount_amount",
                                                  "bank_fee_amount", "amount_in_base_currency")),
                         (SBP.AvailableVendorDepositItem, ("amount", "amount_applied", "amount_refunded", "remaining_amount")),
                         (SB.BillActivity, ("amount",))):
        for m in medan:
            assert "int" != getattr(kelas.model_fields[m].annotation, "__name__", ""), (kelas.__name__, m)
            assert kelas.model_fields[m].annotation is not int, (kelas.__name__, m)


def test_penentu_bayar_tagihan_tanpa_int():
    pohon = ast.parse(inspect.getsource(BKR.rencana_bayar_tagihan).lstrip())
    panggil = [ast.unparse(n) for n in ast.walk(pohon) if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "int"]
    assert panggil == [], panggil
    s = inspect.getsource(BKR.rencana_bayar_tagihan)
    assert "remaining = rupiah_desimal(await conn.fetchval(SQL_SISA_LAMA, tid, str(bill_id), bill_id))" in s


class _K:
    """Tagihan terposting bersisa `sisa`; semua bacaan lain kosong (pratinjau mengumpulkan blok, tak berhenti)."""
    def __init__(self, sisa):
        self.sisa = sisa

    async def fetchrow(self, sql, *a):
        if "FROM bills" in sql:
            return {"id": a[0], "amount": Decimal("274.50"), "amount_paid": Decimal("0"), "status": "posted", "status_v2": "posted",
                    "ap_id": None, "vendor_id": uuid4(), "vendor_name": "V", "invoice_number": "PB-X",
                    "total_tagihan": Decimal("274.50"), "tax_amount": Decimal("24.50")}
        return None

    async def fetchval(self, sql, *a):
        return self.sisa if sql is BKR.SQL_SISA_LAMA else None

    async def fetch(self, sql, *a):
        return []

    async def execute(self, sql, *a):
        return None


@pytest.fixture
def peran(monkeypatch):
    """Peran akun AP/kas terpetakan (fake tak punya tabel account_roles)."""
    async def akun(*a, **k):
        return uuid4()
    monkeypatch.setattr(BKR, "resolve_account_id_by_role", akun)
    monkeypatch.setattr(BKR, "resolve_account_id_or_none", akun)


def _kode(sisa, jumlah):
    r = asyncio.run(BKR.rencana_bayar_tagihan(_K(sisa), "t", uuid4(), {"amount": jumlah, "payment_method": "transfer"}, pratinjau=True))
    return [b["code"] for b in r["blocks"]], r


def test_tagihan_bersen_bisa_dilunasi_persis(peran):
    kode, _ = _kode(Decimal("274.50"), Decimal("274.50"))
    assert "MELEBIHI_SISA_TAGIHAN" not in kode, kode          # dulu int(274,50)=274 -> 274,50 > 274 DITOLAK
    kode, r = _kode(Decimal("274.50"), Decimal("274.51"))
    assert "MELEBIHI_SISA_TAGIHAN" in kode
    msg = [b["message"] for b in r["blocks"] if b["code"] == "MELEBIHI_SISA_TAGIHAN"][0]
    assert msg == "Jumlah pembayaran Rp 274,51 melebihi sisa tagihan Rp 274,50."


def test_bulat_perilaku_lama(peran):
    assert "MELEBIHI_SISA_TAGIHAN" not in _kode(Decimal("50000.00"), Decimal("50000"))[0]
    assert "MELEBIHI_SISA_TAGIHAN" in _kode(Decimal("50000.00"), Decimal("50001"))[0]


def test_migrasi_v473_kolom_sisa_numeric():
    s = (MIG / "V473__bill_payment_alloc_sisa_sen.sql").read_text()
    assert "ALTER COLUMN remaining_before TYPE numeric(18,2)" in s and "ALTER COLUMN remaining_after TYPE numeric(18,2)" in s
    for kata in ("BEGIN;", "COMMIT;", "CONCURRENTLY"):
        assert kata not in s
    r = (MIG / "V473__bill_payment_alloc_sisa_sen_ROLLBACK.sql").read_text()
    assert "RAISE EXCEPTION" in r and "trunc(remaining_before)" in r  # pembalik menolak bila ada sen
