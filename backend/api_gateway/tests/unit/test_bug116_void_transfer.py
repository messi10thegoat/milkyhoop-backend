"""BUG-116 (7 Nov 2026): void transfer antar rekening -- perilaku POST /bank-transfers/{id}/void dengan koneksi tiruan.
Dulu baris mutasi ASAL tak pernah ditandai batal: UPDATE memakai reference_type 'bank_transfer' (post menulis 'transfer') -> 0 baris;
dan nilai status 'void' melanggar CHECK V110 (DRAFT/POSTED/VOIDED) begitu predikatnya benar."""
import asyncio
import os
import re
import uuid
from datetime import date
from decimal import Decimal as D

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

from starlette.requests import Request  # noqa: E402

from app.routers import bank_transfers as BT  # noqa: E402
from app.services import kasbank_tulis as KT  # noqa: E402
from app.schemas.bank_transfers import VoidBankTransferRequest  # noqa: E402

T = "kaos-biru-konveksi"
TRF, JE, DARI, KE, COA_DARI, COA_KE, COA_FEE = (uuid.uuid4() for _ in range(7))
MIG = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(BT.__file__))), "..", "migrations")


class Kon:
    def __init__(self):
        self.exe = []

    def transaction(self):
        kon = self

        class Tx:
            async def __aenter__(self):
                return kon

            async def __aexit__(self, *a):
                return False
        return Tx()

    async def fetchval(self, sql, *a):
        return None

    async def fetchrow(self, sql, *a):
        if "FROM bank_transfers bt" in sql:
            return {"id": TRF, "status": "posted", "transfer_number": "TRF-2611-0003", "transfer_date": date(2026, 11, 7),
                    "journal_id": JE, "total_amount": D("7500"), "amount": D("5000"), "from_bank_id": DARI, "to_bank_id": KE,
                    "from_name": "Kas Toko", "from_coa_id": COA_DARI, "from_balance": D("1766900"),
                    "to_name": "Kas Kecil", "to_coa_id": COA_KE, "to_balance": D("5000")}
        return None  # periode: tak ada baris = terbuka

    async def fetch(self, sql, *a):
        if "FROM journal_lines" in sql:  # D Kas Kecil 5.000 + D biaya 2.500 / K Kas Toko 7.500
            return [{"account_id": COA_KE, "debit": D("5000"), "credit": D("0"), "memo": "masuk"},
                    {"account_id": COA_FEE, "debit": D("2500"), "credit": D("0"), "memo": "biaya"},
                    {"account_id": COA_DARI, "debit": D("0"), "credit": D("7500"), "memo": "keluar"}]
        return []

    async def execute(self, sql, *a):
        self.exe.append((" ".join(sql.split()), a))
        return "UPDATE 1"


class Pool:
    def __init__(self, kon):
        self.kon = kon

    def acquire(self):
        kon = self.kon

        class Cm:
            async def __aenter__(self):
                return kon

            async def __aexit__(self, *a):
                return False
        return Cm()


def _void(monkeypatch):
    kon = Kon()

    async def pool():
        return Pool(kon)

    async def lolos(*a):
        return None

    async def hari(*a):
        return date(2026, 11, 7)
    monkeypatch.setattr(BT, "get_pool", pool)
    # Kas&Bank standar SO (9 Nov): penjaga rekonsiliasi + periode hari ini pindah ke penentu bersama KT.rencana_void_transfer
    monkeypatch.setattr(KT, "tolak_void_bila_terekonsiliasi", lolos)
    monkeypatch.setattr(KT, "tanggal_dokumen", hari)
    monkeypatch.setattr(BT, "tanggal_dokumen", hari)
    r = Request({"type": "http", "method": "POST", "path": "/", "headers": []})
    r.state.user = {"tenant_id": T, "user_id": str(uuid.uuid4())}
    hasil = asyncio.run(BT.void_bank_transfer(r, TRF, VoidBankTransferRequest(reason="salah rekening")))
    return hasil, kon.exe


def _status_v110():
    teks = open(os.path.join(MIG, "V110__kasbank_phase1_schema.sql")).read()
    m = re.search(r"status VARCHAR\(10\) NOT NULL DEFAULT 'POSTED'\s+CHECK \(status IN \(([^)]*)\)\)", teks)
    return {x.strip().strip("'") for x in m.group(1).split(",")}


def test_baris_mutasi_asal_ditandai_voided_dengan_reference_type_post(monkeypatch):
    _, exe = _void(monkeypatch)
    upd = [(s, a) for s, a in exe if s.startswith("UPDATE bank_transactions")]
    assert len(upd) == 1, upd
    s, a = upd[0]
    # ROW (n): lewat kasbank_tulis.tandai_baris_void -- predikat reference_type/reference_id sebagai parameter ("transfer" = yang ditulis posting)
    assert "reference_type = $4" in s and "reference_id = $5" in s and "tenant_id = $1" in s and a[0] == T and a[3:] == ("transfer", TRF)
    st = re.search(r"SET status = '([A-Za-z]+)'", s).group(1)
    assert st in _status_v110() and st == "VOIDED"                 # CHECK V110: nilai lain = 500 di prod
    assert "status <> 'VOIDED'" in s or "status != 'VOIDED'" in s  # idempoten: baris yang sudah batal tak disentuh


def test_pembalik_semua_baris_ditukar_dan_mirror_dua_rekening(monkeypatch):
    h, exe = _void(monkeypatch)
    assert h["data"]["status"] == "void"
    je = [(s, a) for s, a in exe if s.startswith("INSERT INTO journal_entries")]
    assert len(je) == 1 and "'DRAFT'" in je[0][0] and je[0][1][5] == JE                       # reversal_of_id = jurnal asal
    jl = [a for s, a in exe if s.startswith("INSERT INTO journal_lines")]
    assert [(x[3], x[4], x[5]) for x in jl] == [(COA_KE, D("0"), D("5000")), (COA_FEE, D("0"), D("2500")),
                                                 (COA_DARI, D("7500"), D("0"))]                  # Law 2: semua baris, termasuk biaya
    assert any(s.startswith("UPDATE journal_entries SET status = 'POSTED'") for s, _ in exe)     # DRAFT -> POSTED
    bt = [a for s, a in exe if s.startswith("INSERT INTO bank_transactions")]
    assert [(x[2], x[3]) for x in bt] == [(DARI, D("7500")), (KE, D("-5000"))]                # BankSync R3: mirror kedua rekening
    assert all(x[5] == TRF for x in bt)
