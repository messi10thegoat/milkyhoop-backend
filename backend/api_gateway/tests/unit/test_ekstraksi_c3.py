"""BF-1c3: EKSTRAKSI tiga penulis dari handler rute supaya bisa dipanggil Bank Feed: (E1) bank_transfers.buat_transfer_core, (E2) bank_transfers.void_transfer_core,
(E3) customer_deposits.buat_dp_core. Urutan SQL HARUS sama dengan handler lama. Bukti identik pada DB nyata = harness uji-salinan-bankfeed-1c3 (dua pohon kode). TES MERAH DULU."""
import asyncio
import inspect
import uuid
from datetime import date
from decimal import Decimal as D

import pytest

from app.routers import bank_transfers as BTR
from app.routers import customer_deposits as CD
from app.schemas.bank_transfers import CreateBankTransferRequest  # noqa: F401  (lokasi skema dipastikan oleh impor)

T = "t-uji"
CTX = {"tenant_id": T, "user_id": str(uuid.uuid4())}
ASAL, TUJUAN, TRF, JE = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


class _K:
    def __init__(self):
        self.sql = []

    async def execute(self, sql, *a):
        self.sql.append((" ".join(sql.split()), a))

    async def fetchval(self, sql, *a):
        self.sql.append((" ".join(sql.split()), a))
        if "generate_bank_transfer_number" in sql:
            return "TRF-2611-0001"
        if "generate_customer_deposit_number" in sql:
            return "DEP-2611-0001"
        if "INSERT INTO customer_deposits" in sql:
            return DEP_ID
        raise AssertionError(sql[:80])

    async def fetchrow(self, sql, *a):
        self.sql.append((" ".join(sql.split()), a))
        raise AssertionError(sql[:80])


DEP_ID = uuid.uuid4()


# ====================================================================== E1: buat_transfer_core
def _body_transfer(auto_post):
    return CreateBankTransferRequest(from_bank_id=str(ASAL), to_bank_id=str(TUJUAN), amount=500000, fee_amount=0, transfer_date=date(2026, 10, 10), ref_no="R1", notes="uji", auto_post=auto_post)


def test_buat_transfer_core_urutan_nomor_insert_draf_lalu_posting(monkeypatch):
    panggil = []

    async def post(conn, ctx, tid):
        panggil.append(tid)
        return {"journal_id": "j1", "journal_number": "JT-1", "from_transaction_id": "bt-asal", "to_transaction_id": "bt-tujuan"}
    monkeypatch.setattr(BTR, "_post_transfer", post)
    k = _K()
    h = asyncio.run(BTR.buat_transfer_core(k, CTX, _body_transfer(True)))
    sql = [s for s, _ in k.sql]
    assert sql[0].startswith("SELECT generate_bank_transfer_number") and sql[1].startswith("INSERT INTO bank_transfers") and "'draft'" in sql[1] and len(sql) == 2
    a = k.sql[1][1]
    assert a[1] == T and a[2] == "TRF-2611-0001" and a[3] == ASAL and a[4] == TUJUAN and a[5] == 500000 and a[6] == 0 and a[7] == 500000 and a[8] == date(2026, 10, 10) and a[9] == "R1" and a[10] == "uji"
    assert panggil == [h["transfer_id"]] and h["transfer_number"] == "TRF-2611-0001" and h["post"]["from_transaction_id"] == "bt-asal"


def test_buat_transfer_core_tanpa_auto_post_hanya_draf(monkeypatch):
    async def post(*a):
        raise AssertionError("tak boleh posting")
    monkeypatch.setattr(BTR, "_post_transfer", post)
    h = asyncio.run(BTR.buat_transfer_core(_K(), CTX, _body_transfer(False)))
    assert h["post"] is None


# ====================================================================== E2: void_transfer_core
def test_void_transfer_core_urutan_sql_sama_dengan_handler_lama(monkeypatch):
    async def hari_ini(conn, tid):
        return date(2026, 11, 12)
    monkeypatch.setattr(BTR, "tanggal_dokumen", hari_ini)
    t = {"transfer_number": "TRF-2611-0001", "journal_id": JE, "total_amount": D(500000), "amount": D(500000), "from_bank_id": ASAL, "to_bank_id": TUJUAN}
    r = {"t": t, "hapus_draf": False, "baris": [(TUJUAN, D(0), D(500000), "balik"), (ASAL, D(500000), D(0), "balik")],
         "saldo": [{"sesudah": D(1500000)}, {"sesudah": D(0)}]}
    k = _K()
    h = asyncio.run(BTR.void_transfer_core(k, CTX, TRF, "salah transfer", r))
    sql = [s for s, _ in k.sql]
    assert sql[0].startswith("INSERT INTO journal_entries") and "reversal_of_id" in sql[0] and "'DRAFT'" in sql[0]
    assert sql[1].startswith("INSERT INTO journal_lines") and sql[2].startswith("INSERT INTO journal_lines")
    assert sql[3] == "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1"
    assert sql[4].startswith("UPDATE journal_entries SET reversed_by_id = $2, reversed_at = NOW()")
    assert sql[5].startswith("INSERT INTO bank_transactions") and "'transfer_in'" in sql[5] and "'transfer_void'" in sql[5]
    assert sql[6].startswith("INSERT INTO bank_transactions") and "'transfer_out'" in sql[6] and "'transfer_void'" in sql[6]
    assert sql[7].startswith("UPDATE bank_transactions SET status = 'VOIDED'") and sql[8].startswith("UPDATE bank_transfers SET status = 'void'") and len(sql) == 9
    je = k.sql[0][1]
    assert je[2] == "RV-TRF-2611-0001" and je[3] == "Void Transfer TRF-2611-0001 - salah transfer" and je[4] == TRF and je[5] == JE and je[8] == date(2026, 11, 12) and h["reversal_journal_id"] == je[0]
    asal_bt, tujuan_bt = k.sql[5][1], k.sql[6][1]
    assert asal_bt[2] == ASAL and asal_bt[3] == D(500000) and asal_bt[4] == D(1500000) and tujuan_bt[2] == TUJUAN and tujuan_bt[3] == -D(500000) and tujuan_bt[4] == D(0)


# ====================================================================== E3: buat_dp_core
def _body_dp(auto_post):
    return CD.CreateCustomerDepositRequest(customer_id=str(uuid.uuid4()), customer_name="PT Uji", amount=1000000, deposit_date=date(2026, 10, 10), payment_method="transfer",
                                           account_id=str(uuid.uuid4()), bank_account_id=str(ASAL), reference="ref", notes="uji", auto_post=auto_post)


def test_buat_dp_core_draf_dan_posting(monkeypatch):
    urut = []

    async def post(conn, ctx, dep_id):
        urut.append(("post", dep_id))
        return {"journal_id": "j-dp", "journal_number": "DEP-J-1"}
    monkeypatch.setattr(CD, "_post_deposit", post)
    rb = {"order_id": None, "pelanggan_dp": uuid.uuid4(), "jenis": None}
    k = _K()
    h = asyncio.run(CD.buat_dp_core(k, CTX, _body_dp(True), rb))
    sql = [s for s, _ in k.sql]
    assert sql[0].startswith("SELECT generate_customer_deposit_number") and sql[1].startswith("INSERT INTO customer_deposits") and "'draft'" in sql[1]
    assert sql[2] == "SELECT pg_advisory_xact_lock(hashtext($1))" and k.sql[2][1] == (f"DEPOSIT_POST:{DEP_ID}",) and urut == [("post", DEP_ID)]
    assert h["data"]["id"] == str(DEP_ID) and h["data"]["status"] == "posted" and h["data"]["journal_id"] == "j-dp" and h["message"] == "Customer deposit created and posted"
    d = asyncio.run(CD.buat_dp_core(_K(), CTX, _body_dp(False), rb))
    assert d["data"]["status"] == "draft" and d["message"] == "Uang muka dibuat" and "journal_id" not in d["data"]


# ====================================================================== handler lama memanggil inti hasil ekstraksi, tanpa SQL tulis inline
def test_handler_lama_memanggil_inti_tanpa_sql_inline():
    a = inspect.getsource(BTR.create_bank_transfer)
    assert "buat_transfer_core(" in a and "INSERT INTO bank_transfers" not in a
    b = inspect.getsource(BTR.void_bank_transfer)
    assert "void_transfer_core(" in b and "INSERT INTO journal_entries" not in b and "INSERT INTO bank_transactions" not in b
    c = inspect.getsource(CD.create_customer_deposit)
    assert "buat_dp_core(" in c and "INSERT INTO customer_deposits" not in c
    for fn in (BTR.buat_transfer_core, BTR.void_transfer_core, CD.buat_dp_core):
        assert "float(" not in inspect.getsource(fn)
