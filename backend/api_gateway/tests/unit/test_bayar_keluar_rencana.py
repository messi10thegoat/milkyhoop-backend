"""PAY-PREVIEW U1 (23 Okt 2026): penentu SATU pembayaran keluar (services/bayar_keluar_rencana) dipakai tulis DAN pratinjau.
Jurnal rencana == baris yang ditulis (urutan/akun/nominal/memo kode lama); pratinjau READ ONLY tanpa sequence; blok tulis = galat lama."""
import asyncio
import inspect
import re
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.services import bayar_keluar_rencana as BKR
from app.services.role_resolver import AccountRole

APP = Path(__file__).resolve().parents[2] / "app"
AP, DISK, UMV, PPH_ROLE, BANK_COA, FEE, PPH_TC_COA = (uuid4() for _ in range(7))


class KoneksiPalsu:
    def __init__(self, bank=True, tc_coa=True):
        self.bank, self.tc_coa = bank, tc_coa

    async def fetchrow(self, sql, *a):
        if "FROM bank_accounts" in sql:
            return {"coa_id": BANK_COA, "account_name": "BCA"} if self.bank else None
        return None

    async def fetchval(self, sql, *a):
        if "FROM tax_codes" in sql:
            return PPH_TC_COA if self.tc_coa else None
        raise AssertionError(sql)


@pytest.fixture
def peran(monkeypatch):
    m = {AccountRole.AP_TRADE: AP, AccountRole.PURCHASE_DISCOUNT: DISK, AccountRole.AP_PREPAID: UMV,
         AccountRole.WHT_PPH_PAYABLE: PPH_ROLE}

    async def r(conn, tid, role):
        return m[role]

    async def kode(conn, tid, code):
        return FEE if code == "5-20850" else None
    monkeypatch.setattr(BKR, "resolve_account_id_by_role", r)
    monkeypatch.setattr(BKR, "resolve_account_id_or_none", kode)


def _tanpa_docstring(src: str) -> str:
    """Kode tanpa docstring & komentar (docstring menyebut nama cek -> urutan/teks terlarang palsu)."""
    import ast
    import textwrap
    t = ast.parse(textwrap.dedent(src))
    for n in ast.walk(t):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.body and isinstance(n.body[0], ast.Expr) \
                and isinstance(getattr(n.body[0], "value", None), ast.Constant) and isinstance(n.body[0].value.value, str):
            n.body = n.body[1:] or [ast.Pass()]
    return ast.unparse(t)


def jalan(c):
    return asyncio.run(c)


def test_baris_lengkap_urutan_dan_memo_kode_lama(peran):
    baris, td = jalan(BKR.baris_jurnal_bayar(
        KoneksiPalsu(), "t", payment_number="PAY-202610-0001", vendor_name="V", bank_account_id=str(uuid4()),
        total_amount=1000, allocated_amount=900, discount_amount=50, bank_fee_amount=10, unapplied_amount=150,
        pph_tax_code_id=str(uuid4()), pph_amount=Decimal("20")))
    assert td == 1060
    assert baris == [
        (AP, 900, 0, "Pelunasan hutang usaha - V"),
        (FEE, 10, 0, "Biaya administrasi bank"),
        (UMV, 150, 0, "Uang muka vendor - V"),
        (BANK_COA, 0, Decimal("990"), "Pembayaran dari bank - PAY-202610-0001"),
        (DISK, 0, 50, "Potongan pembelian"),
        (PPH_TC_COA, 0, Decimal("20"), "PPh dipotong dari pembayaran vendor"),
    ]
    assert sum(Decimal(str(b[1])) for b in baris) == sum(Decimal(str(b[2])) for b in baris) == td


def test_biaya_bank_tanpa_akun_400_pesan_lama(peran, monkeypatch):
    async def tak_ada(conn, tid, code):
        return None
    monkeypatch.setattr(BKR, "resolve_account_id_or_none", tak_ada)
    with pytest.raises(HTTPException) as e:
        jalan(BKR.baris_jurnal_bayar(KoneksiPalsu(), "t", payment_number="P", vendor_name="V", bank_account_id=str(uuid4()),
                                     total_amount=100, allocated_amount=100, bank_fee_amount=5))
    assert e.value.status_code == 400 and "5-20850/5-20800" in e.value.detail


def test_deposit_sumber_kredit_uang_muka(peran):
    baris, _ = jalan(BKR.baris_jurnal_bayar(KoneksiPalsu(bank=False), "t", payment_number="P", vendor_name="V", bank_account_id=None,
                                            total_amount=100, allocated_amount=100, source_type="deposit", source_deposit_id=str(uuid4())))
    assert baris[-1] == (UMV, 0, 100, "Aplikasi uang muka vendor - P")


def test_baris_tagihan_lama_sama_dgn_record_payment():
    b = BKR.baris_tagihan_lama(AP, BANK_COA, PPH_TC_COA, Decimal("1000"), Decimal("20"), "V", "BP-2610-0001")
    assert b == [(AP, Decimal("1000"), 0, "Pelunasan hutang - V"), (BANK_COA, 0, Decimal("980"), "Pembayaran dari bank - BP-2610-0001"),
                 (PPH_TC_COA, 0, Decimal("20"), "PPh dipotong dari pembayaran vendor")]
    assert len(BKR.baris_tagihan_lama(AP, BANK_COA, None, Decimal("5"), Decimal("0"), "V", "x")) == 2


def test_blok_tulis_melempar_pratinjau_mengumpulkan():
    t = BKR.Blok(False)
    with pytest.raises(HTTPException) as e:
        t.galat("X", 403, "pesan")
    assert (e.value.status_code, e.value.detail) == (403, "pesan")
    p = BKR.Blok(True)
    p.galat("X", 400, "a")
    p.galat("Y", 400, "b")
    assert p.untuk_respons() == [{"code": "X", "message": "a"}, {"code": "Y", "message": "b"}]


def test_penentu_tak_pernah_menyentuh_sequence_atau_menulis():
    kode = _tanpa_docstring((APP / "services/bayar_keluar_rencana.py").read_text())
    for terlarang in ("get_next_journal_number", "bill_payment_sequences", "generate_payment_number"):
        assert terlarang not in kode, terlarang
    assert not re.search(r"\b(INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM)\b", kode, re.I)  # FOR UPDATE (kunci) boleh


def test_penulis_memakai_penentu_yang_sama():
    bp = (APP / "routers/bill_payments.py").read_text()
    fn = bp[bp.index("async def create_bill_payment_journal"):bp.index("# === Journal Preview")]
    assert "await BKR.baris_jurnal_bayar(" in fn and fn.count("INSERT INTO journal_lines") == 1
    buat = bp[bp.index("async def create_bill_payment(request"):bp.index("async def delete_bill_payment")]
    assert "_r = await BKR.rencana_bayar(conn, ctx, payload)" in buat
    assert buat.index("BKR.rencana_bayar(") < buat.index("generate_payment_number(")  # nomor lahir SESUDAH semua cek
    assert 'for _al in _r["alokasi"]' in buat and "await efek_posting_bayar(" in buat  # U2a: cermin bank lewat penulis efek bersama
    efek = bp[bp.index("async def efek_posting_bayar("):bp.index("# === Journal Preview")]
    assert "BKR.cermin_bank(" in efek
    bs = (APP / "services/bills_service.py").read_text()
    rp = bs[bs.index("    async def record_payment("):bs.index("    async def mark_paid(")]
    assert "await BKR.rencana_bayar_tagihan(conn, tenant_id, bill_id, request)" in rp
    assert "BKR.baris_tagihan_lama(" in rp and rp.count("INSERT INTO journal_lines") == 1
    assert rp.index("rencana_bayar_tagihan(") < rp.index("get_next_journal_number")


def test_pratinjau_read_only_tanpa_kunci():
    bp = (APP / "routers/bill_payments.py").read_text()
    pv = bp[bp.index("async def preview_create_bill_payment"):bp.index('@router.get("", response_model=BillPaymentListResponse)')]
    bl = (APP / "routers/bills.py").read_text()
    pv2 = bl[bl.index("async def preview_record_payment"):bl.index("# MARK AS PAID (Quick Action)")]
    for p in (pv, pv2):
        assert "transaction(readonly=True)" in p and "tr.rollback()" in p
        assert "pg_advisory" not in p and "FOR UPDATE" not in p
    assert "pratinjau=True" in pv
    s = (APP / "services/bayar_keluar_rencana.py").read_text()
    assert '("" if pratinjau else " FOR UPDATE")' in s and '("" if pratinjau else "FOR UPDATE")' in s


def test_rute_pratinjau_terpetakan_izin_buat():
    m = (APP / "middleware/permission_middleware.py").read_text()
    assert '(r"^/api/bill-payments/preview$", ["POST"], "send_payment", "C")' in m
    assert '(r"^/api/bills/[^/]+/payments/preview$", ["POST"], "send_payment", "C")' in m


def test_urutan_cek_rencana_bayar_sama_dgn_kode_lama():
    s = _tanpa_docstring(inspect.getsource(BKR.rencana_bayar))
    urut = ["periode_terbuka(", "VENDOR_TIDAK_DITEMUKAN", "REKENING_TIDAK_DITEMUKAN", "_sinkron(normalisasi_pihak", "_sinkron(pastikan_pihak_sama",
            "ALOKASI_MELEBIHI_PEMBAYARAN", "MELEBIHI_SISA_TAGIHAN"]
    pos = [s.index(u) for u in urut]
    assert pos == sorted(pos)
    r = _tanpa_docstring(inspect.getsource(BKR.rencana_bayar_tagihan))
    urut2 = ["Faktur pembelian tidak ditemukan.", "Faktur pembelian batal tidak bisa dibayar.", "SQL_SISA_LAMA", "Nominal pembayaran harus lebih dari 0.", "melebihi sisa tagihan",
             "Rekening kas/bank tidak ditemukan atau tidak aktif.", "adalah akun induk", "bank_account_id atau account_id wajib diisi"]
    pos2 = [r.index(u) for u in urut2]
    assert pos2 == sorted(pos2)
    assert re.search(r"remaining == 0 and bill\[[\"']amount_paid[\"']\] == 0", r)  # cadangan cache lama DIPERTAHANKAN (U2a = putusan pemilik)
