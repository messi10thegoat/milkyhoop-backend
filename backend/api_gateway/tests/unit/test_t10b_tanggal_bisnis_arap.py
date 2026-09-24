"""#10b-1 — tanggal bisnis tenant di AR/AP + kas (lanjutan #10a, test_t10_tanggal_bisnis.py).

Void CN / pembayaran tagihan / uang muka vendor / nota kredit vendor / giro /
faktur / nota penjualan / tagihan, nomor PAY-YYYYMM, default tanggal dokumen
(aplikasi CN/DP-vendor/VC, SO->faktur, proforma, saldo awal rekening, jurnal
reklas, nomor jurnal), + periode tanggal PEMBALIK di void beban/transfer/
pembayaran tagihan (putusan MASTER 25 Sep).

Aturan: tanggal dari KLIEN tidak pernah ditimpa — tanggal bisnis hanya DEFAULT
saat kosong. Jam DISUNTIK ke tanggal_tenant: 2026-09-30 18:00Z (-> 1 Okt WIB)
dan kontrol 10:00Z (-> 30 Sep).
"""

import ast
import re
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from app.utils import tanggal_tenant as tt

APP = Path(tt.__file__).resolve().parents[1]
TENANT = "kaos-biru-konveksi"
MALAM_UTC = datetime(2026, 9, 30, 18, 0, tzinfo=timezone.utc)
SIANG_UTC = datetime(2026, 9, 30, 10, 0, tzinfo=timezone.utc)
KASUS = [(MALAM_UTC, date(2026, 10, 1)), (SIANG_UTC, date(2026, 9, 30))]

# Fungsi yang menulis/memeriksa TANGGAL AKUNTANSI (bukan cap waktu).
FUNGSI = {
    "routers/credit_notes.py": ["apply_credit_note", "void_credit_note"],
    "routers/bill_payments.py": ["generate_payment_number", "void_bill_payment"],
    "routers/vendor_deposits.py": ["apply_vendor_deposit", "void_vendor_deposit"],
    "routers/vendor_credits.py": ["apply_vendor_credit", "void_vendor_credit"],
    "routers/cheques.py": ["cancel_cheque", "delete_cheque"],
    "routers/sales_invoices.py": ["_execute_fulfillment", "_internal_post_invoice", "void_invoice"],
    "routers/sales_receipts.py": ["void_sales_receipt"],
    "services/bills_service.py": ["void_bill"],
    "routers/sales_orders.py": ["convert_to_invoice"],
    "routers/proformas.py": ["create_proforma"],
    "routers/journals.py": ["get_next_journal_number", "reclassify_bill_inventory"],
    "routers/bank_accounts.py": ["create_bank_account"],
    "routers/expenses.py": ["void_expense"],
    "routers/bank_transfers.py": ["void_bank_transfer"],
}
# Default saat klien kosong: tanggal bisnis HANYA boleh jadi cabang cadangan.
DEFAULT_KLIEN = {
    "routers/credit_notes.py": ["apply_credit_note"],
    "routers/vendor_deposits.py": ["apply_vendor_deposit"],
    "routers/vendor_credits.py": ["apply_vendor_credit"],
    "routers/sales_orders.py": ["convert_to_invoice"],
    "routers/proformas.py": ["create_proforma"],
    "routers/journals.py": ["get_next_journal_number", "reclassify_bill_inventory"],
    "routers/bank_accounts.py": ["create_bank_account"],
}


def _fungsi(berkas, nama):
    t = ast.parse((APP / berkas).read_text())
    hasil = [n for n in ast.walk(t) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == nama]
    assert len(hasil) == 1, (berkas, nama, len(hasil))
    return hasil[0]


def _jam_utc(node):
    """Panggilan jam UTC yang menghasilkan TANGGAL: X.today(), datetime.now().date()/strftime()."""
    salah = []
    for n in ast.walk(node):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
            v = n.func.value
            if n.func.attr == "today" and isinstance(v, ast.Name) and v.id in ("date", "dt_date", "datetime"):
                salah.append(n.lineno)
            if n.func.attr in ("date", "strftime") and isinstance(v, ast.Call) and isinstance(v.func, ast.Attribute) \
                    and v.func.attr in ("now", "utcnow") and not v.args and not v.keywords:
                salah.append(n.lineno)
    return salah


def _sql_utc(node):
    salah = []
    for n in ast.walk(node):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and "CURRENT_DATE" in n.value:
            if re.search(r"INSERT INTO (journal_entries|bank_transactions)\b|FROM fiscal_periods", n.value):
                salah.append(n.lineno)
    return salah


PASANGAN = [(b, f) for b, fs in FUNGSI.items() for f in fs]


@pytest.mark.parametrize("berkas,nama", PASANGAN)
def test_fungsi_tanpa_tanggal_utc(berkas, nama):
    f = _fungsi(berkas, nama)
    assert _jam_utc(f) == [], f"{berkas}::{nama} jam UTC di baris {_jam_utc(f)}"
    assert _sql_utc(f) == [], f"{berkas}::{nama} CURRENT_DATE di SQL baris {_sql_utc(f)}"


def test_penjaga_bisa_merah():
    t = ast.parse('async def f():\n    a = date.today()\n    b = datetime.now().strftime("%y")\n'
                  '    q = """INSERT INTO journal_entries (journal_date) VALUES (CURRENT_DATE)"""\n')
    assert _jam_utc(t) == [2, 3]
    assert _sql_utc(t) == [4]


def _cadangan_saja(fn):
    """Setiap await tanggal_dokumen(...) di fn berada di cabang CADANGAN: operan kanan `or`,
    `else` sebuah IfExp, atau badan `if <x> is None:`."""
    induk = {}
    for p in ast.walk(fn):
        for c in ast.iter_child_nodes(p):
            induk[c] = p
    panggilan = [n for n in ast.walk(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                 and n.func.id == "tanggal_dokumen"]
    assert panggilan, f"{fn.name}: tak memanggil tanggal_dokumen"
    for c in panggilan:
        node, ok = c, False
        while node in induk:
            p = induk[node]
            if isinstance(p, ast.BoolOp) and isinstance(p.op, ast.Or) and node in p.values[1:]:
                ok = True
                break
            if isinstance(p, ast.IfExp) and node is p.orelse:
                ok = True
                break
            if isinstance(p, ast.If) and node in p.body and isinstance(p.test, ast.Compare) \
                    and isinstance(p.test.ops[0], ast.Is):
                ok = True
                break
            node = p
        assert ok, f"{fn.name}: tanggal_dokumen di baris {c.lineno} bukan cadangan -> tanggal klien bisa tertimpa"


@pytest.mark.parametrize("berkas,nama", [(b, f) for b, fs in DEFAULT_KLIEN.items() for f in fs])
def test_tanggal_klien_tak_ditimpa(berkas, nama):
    _cadangan_saja(_fungsi(berkas, nama))


# ─────────────────────────── jam disuntik ───────────────────────────
@pytest.fixture
def jam(monkeypatch):
    def pasang(instant):
        class _DT(datetime):
            @classmethod
            def now(cls, tz=None):
                return instant if tz else instant.replace(tzinfo=None)

        monkeypatch.setattr(tt, "datetime", _DT)
        tt._cache.clear()

    yield pasang
    tt._cache.clear()


@pytest.mark.parametrize("instant,harap", KASUS)
@pytest.mark.asyncio
async def test_nomor_pembayaran_tagihan_ikut_bulan_bisnis(jam, instant, harap):
    """Jalur NOMOR dokumen: PAY-YYYYMM (18:00Z 30 Sep -> 202610)."""
    from app.routers import bill_payments as bp

    jam(instant)

    class _C:
        def __init__(self):
            self.ym = None

        async def fetchval(self, q, *a):
            return "Asia/Jakarta" if "timezone" in q else None

        async def fetchrow(self, q, *a):
            self.ym = a[1]
            return {"last_number": 1}

    c = _C()
    nomor = await bp.generate_payment_number(c, TENANT)
    assert c.ym == harap.strftime("%Y-%m")
    assert nomor == f"PAY-{harap.strftime('%Y%m')}-0001"


@pytest.mark.parametrize("instant,harap", KASUS)
@pytest.mark.asyncio
async def test_nomor_jurnal_default_bisnis_dan_tanggal_klien_utuh(jam, instant, harap):
    from app.routers import journals as jr

    jam(instant)

    class _C:
        def __init__(self):
            self.p_date = []

        async def fetchval(self, q, *a):
            if "timezone" in q:
                return "Asia/Jakarta"
            self.p_date.append(a[2])
            return "JV-X"

    c = _C()
    await jr.get_next_journal_number(c, TENANT, "JV")
    klien = date(2026, 9, 15)
    await jr.get_next_journal_number(c, TENANT, "JV", klien)
    assert c.p_date == [harap, klien]


class _Berhenti(Exception):
    pass


@pytest.mark.parametrize("instant,harap", KASUS)
@pytest.mark.asyncio
async def test_batal_giro_jurnal_pembalik_bertanggal_bisnis(jam, monkeypatch, instant, harap):
    from app.routers import cheques as cq

    jam(instant)
    tangkap = {}

    async def _pembalik(conn, tenant_id, journal_id, tanggal, *a, **k):
        tangkap["tanggal"] = tanggal
        raise _Berhenti()

    class _C:
        def transaction(self):
            class _T:
                async def __aenter__(s):
                    return s

                async def __aexit__(s, *e):
                    return False

            return _T()

        async def execute(self, q, *a):
            return None

        async def fetchval(self, q, *a):
            return "Asia/Jakarta" if "timezone" in q else None

        async def fetchrow(self, q, *a):
            return {"id": a[0], "receipt_journal_id": uuid.uuid4(), "cheque_number": "GR-1",
                    "status": "pending"}

    class _Pool:
        def acquire(self):
            c = _C()

            class _A:
                async def __aenter__(s):
                    return c

                async def __aexit__(s, *e):
                    return False

            return _A()

    async def _pool():
        return _Pool()

    monkeypatch.setattr(cq, "get_pool", _pool)
    monkeypatch.setattr(cq, "get_user_context", lambda r: {"tenant_id": TENANT, "user_id": str(uuid.uuid4())})
    monkeypatch.setattr(cq, "create_reversal_journal", _pembalik)
    body = cq.CancelChequeRequest(**{k: "uji pembatalan" for k in cq.CancelChequeRequest.model_fields
                                     if cq.CancelChequeRequest.model_fields[k].is_required()})
    with pytest.raises(Exception):
        await cq.cancel_cheque(object(), uuid.uuid4(), body)
    assert tangkap.get("tanggal") == harap


@pytest.mark.parametrize("berkas,nama", [
    ("routers/expenses.py", "void_expense"),
    ("routers/bank_transfers.py", "void_bank_transfer"),
    ("routers/bill_payments.py", "void_bill_payment"),
])
def test_void_memeriksa_periode_tanggal_pembalik(berkas, nama):
    """Putusan MASTER: void memeriksa periode tanggal JURNAL PEMBALIK (hari ini), bukan hanya tgl dokumen."""
    f = _fungsi(berkas, nama)
    ada = False
    for n in ast.walk(f):
        if isinstance(n, ast.Call) and any(isinstance(a, ast.Name) and a.id == "hari_ini" for a in n.args):
            nama_f = n.func.id if isinstance(n.func, ast.Name) else getattr(n.func, "attr", "")
            sql = [a.value for a in n.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
            if nama_f == "check_period_is_open" or any("fiscal_periods" in q for q in sql):
                ada = True
    assert ada, f"{berkas}::{nama} tak memeriksa periode hari_ini"
