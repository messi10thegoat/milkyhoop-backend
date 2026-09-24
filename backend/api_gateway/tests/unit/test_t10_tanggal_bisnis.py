"""#10a — tanggal jurnal/bank-tx = TANGGAL BISNIS tenant (utils/tanggal_tenant), bukan UTC.

Akar (diukur 24 Sep 2026): container + Postgres UTC; jalur void/unapply memakai
`CURRENT_DATE` SQL / `date.today()` Python -> aksi 00:00-07:00 WIB mendapat
tanggal KEMARIN; tanggal 1 pagi -> jurnal jatuh di BULAN SEBELUMNYA (periode
tertutup / laporan bulanan salah). Bukti nyata: LPS-2609-0082/0083 (kaos,
gate 18:11Z) bertanggal 23 Sep, DA pasangannya 24 Sep.

Unit ini: modul uang harian (receive_payments, customer_deposits, expenses,
bank_transfers, kasbank_v2) + services/bank_sync (pembalik bank-tx bersama).
Payroll = unit berikut.

Jam DISUNTIK ke tanggal_tenant (kode produksi yang sama), bukan ditiru:
- 2026-09-30 18:00Z = 1 Okt 01:00 WIB  -> harus 2026-10-01 (UTC bilang 30 Sep)
- 2026-09-30 10:00Z = 30 Sep 17:00 WIB -> harus 2026-09-30 (kontrol: UTC = WIB)
"""

import ast
import re
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from app.utils import tanggal_tenant as tt

APP = Path(tt.__file__).resolve().parents[1]
BERKAS = [
    "routers/receive_payments.py",
    "routers/customer_deposits.py",
    "routers/expenses.py",
    "routers/bank_transfers.py",
    "routers/kasbank_v2.py",
    "services/bank_sync.py",
]
TENANT = "kaos-biru-konveksi"

MALAM_UTC = datetime(2026, 9, 30, 18, 0, tzinfo=timezone.utc)
SIANG_UTC = datetime(2026, 9, 30, 10, 0, tzinfo=timezone.utc)
KASUS = [(MALAM_UTC, date(2026, 10, 1)), (SIANG_UTC, date(2026, 9, 30))]


# ─────────────────────────── penjaga statis ───────────────────────────
def _sql_tulis_tanggal(src):
    """String SQL yang MENULIS journal_entries / bank_transactions."""
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            if re.search(r"INSERT INTO (journal_entries|bank_transactions)\b", n.value):
                yield n.lineno, n.value


@pytest.mark.parametrize("berkas", BERKAS)
def test_tak_ada_current_date_di_insert_jurnal_atau_banktx(berkas):
    src = (APP / berkas).read_text()
    salah = [ln for ln, sql in _sql_tulis_tanggal(src) if "CURRENT_DATE" in sql or "now()::date" in sql.lower()]
    assert salah == [], f"{berkas}: INSERT bertanggal UTC di baris {salah}"


@pytest.mark.parametrize("berkas", BERKAS)
def test_tak_ada_jam_utc_python_untuk_tanggal(berkas):
    src = (APP / berkas).read_text()
    salah = []
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Name):
            if (n.func.value.id, n.func.attr) in {("date", "today"), ("dt", "now"), ("datetime", "now"), ("datetime", "utcnow")}:
                salah.append(n.lineno)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value.strip().upper() == "SELECT CURRENT_DATE":
            salah.append(n.lineno)
    assert salah == [], f"{berkas}: jam UTC untuk tanggal di baris {salah}"


def test_penjaga_statis_bisa_merah():
    """Kontrol merah alat: pola lama TERDETEKSI."""
    contoh = 'x = """INSERT INTO journal_entries (journal_date) VALUES (CURRENT_DATE)"""\n'
    assert [ln for ln, sql in _sql_tulis_tanggal(contoh) if "CURRENT_DATE" in sql] == [1]


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


@pytest.mark.asyncio
async def test_suntikan_jam_mencapai_helper(jam):
    """Alat: tanpa suntikan yang bekerja, semua tes di bawah tak bermakna."""

    class _C:
        async def fetchval(self, q, *a):
            return "Asia/Jakarta"

    for instant, harap in KASUS:
        jam(instant)
        assert await tt.tanggal_dokumen(_C(), TENANT) == harap


class _Berhenti(Exception):
    pass


def _nilai_kolom(q, a, kolom):
    """Nilai kolom `kolom` pada INSERT: argumen $N, atau ekspresi SQL mentah."""
    m = re.search(r"INSERT INTO \w+\s*\((.*?)\)\s*VALUES\s*\((.*?)\)\s*(RETURNING|$)", q, re.S)
    kol = [k.strip() for k in m.group(1).split(",")]
    val = [v.strip() for v in m.group(2).split(",")]
    ekspr = val[kol.index(kolom)]
    if re.fullmatch(r"\$\d+", ekspr):
        return a[int(ekspr[1:]) - 1]
    return ekspr  # mis. 'CURRENT_DATE' -> tak akan sama dengan date


# ─── bank_sync.create_reversal_bank_transaction (dipakai void RP/DP/beban/...) ───
@pytest.mark.parametrize("instant,harap", KASUS)
@pytest.mark.asyncio
async def test_pembalik_banktx_bertanggal_bisnis(jam, monkeypatch, instant, harap):
    from app.services import bank_sync

    jam(instant)
    tangkap = {}

    async def _buat(conn, **kw):
        tangkap.update(kw)
        return uuid.uuid4()

    monkeypatch.setattr(bank_sync, "create_bank_transaction_for_journal", _buat)

    class _C:
        async def fetchrow(self, q, *a):
            return {"id": a[0], "bank_account_id": uuid.uuid4(), "amount": Decimal("5000"),
                    "transaction_type": "deposit", "reference_type": "receive_payment",
                    "reference_id": uuid.uuid4(), "reference_number": "RCV-1",
                    "description": "x", "status": "POSTED"}

        async def fetchval(self, q, *a):
            return "Asia/Jakarta" if "timezone" in q else None

        async def execute(self, q, *a):
            return None

    await bank_sync.create_reversal_bank_transaction(
        _C(), tenant_id=TENANT, original_bank_transaction_id=uuid.uuid4(),
        reversal_journal_id=uuid.uuid4(), created_by=uuid.uuid4(),
    )
    assert tangkap["transaction_date"] == harap


# ─── kasbank_v2: nomor BT-YYMM ikut bulan bisnis ───
@pytest.mark.parametrize("instant,harap", KASUS)
@pytest.mark.asyncio
async def test_nomor_transaksi_kas_ikut_bulan_bisnis(jam, instant, harap):
    from app.routers import kasbank_v2

    jam(instant)

    class _C:
        async def fetchval(self, q, *a):
            return "Asia/Jakarta" if "timezone" in q else None

    nomor = await kasbank_v2.generate_transaction_number(_C(), TENANT)
    assert nomor.startswith(f"BT-{harap.strftime('%y%m')}-"), nomor


# ─── customer_deposits.reverse_deposit_application_core (juga dipanggil void faktur) ───
@pytest.mark.parametrize("instant,harap", KASUS)
@pytest.mark.asyncio
async def test_unapply_uang_muka_jurnal_dan_periode_bertanggal_bisnis(jam, monkeypatch, instant, harap):
    from app.routers import customer_deposits as cd

    jam(instant)
    akun_ar = uuid.uuid4()

    async def _nop(*a, **k):
        return None

    monkeypatch.setattr(cd, "_assert_ar_side_is_ar_trade", _nop)

    class _C:
        def __init__(self):
            self.periode = []
            self.jurnal = None

        async def execute(self, q, *a):
            if "INSERT INTO journal_entries" in q:
                self.jurnal = (q, a)
                raise _Berhenti()

        async def fetchval(self, q, *a):
            if "timezone" in q:
                return "Asia/Jakarta"
            if "get_next_journal_number" in q:
                return "RV-UJI"
            return None

        async def fetchrow(self, q, *a):
            if "customer_deposit_applications" in q:
                return {"status": "active", "reversed_by_id": None, "journal_id": uuid.uuid4(),
                        "amount_applied": Decimal("100000"), "invoice_id": uuid.uuid4(),
                        "invoice_number": "INV-UJI"}
            if "fiscal_periods" in q:
                self.periode.append(a[1])
                return None
            if "FROM journal_entries" in q:
                return {"id": a[0], "reversed_by_id": None, "status": "POSTED"}
            return None

        async def fetch(self, q, *a):
            return [{"account_id": uuid.uuid4(), "debit": Decimal("100000"), "credit": 0, "memo": "Dr"},
                    {"account_id": akun_ar, "debit": 0, "credit": Decimal("100000"), "memo": "Cr"}]

    c = _C()
    with pytest.raises(_Berhenti):
        await cd.reverse_deposit_application_core(c, {"tenant_id": TENANT, "user_id": str(uuid.uuid4())},
                                                   uuid.uuid4(), uuid.uuid4())
    assert c.periode == [harap], "periode yang diperiksa != tanggal jurnal"
    q, a = c.jurnal
    assert _nilai_kolom(q, a, "journal_date") == harap
