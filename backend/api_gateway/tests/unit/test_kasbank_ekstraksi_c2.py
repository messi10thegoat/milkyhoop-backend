"""BF-1c2: EKSTRAKSI penulis Uang Masuk/Keluar (routers/bank_accounts.create_manual_transaction) dan void manual (routers/kasbank_v2.void_transaction) ke services/kasbank_tulis
supaya bisa dipanggil Bank Feed. Urutan SQL HARUS sama dengan handler lama (jurnal DRAFT -> baris -> POSTED -> bank_transactions -> source_id; pembalik DRAFT -> baris -> POSTED ->
asli reversed_by_id -> mirror bt -> tandai VOIDED). Bukti identik pada DB nyata = harness lama-vs-baru (uji-salinan-bankfeed-1c2). TES MERAH DULU."""
import asyncio
import inspect
import uuid
from datetime import date
from decimal import Decimal as D

from app.routers import bank_accounts as BAR
from app.routers import kasbank_v2 as KV2
from app.schemas.bank_accounts import CreateManualTransactionRequest
from app.services import kasbank_tulis as KT

T = "t-uji"
REK, COA_BANK, LAWAN, TX, JE = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
CTX = {"tenant_id": T, "user_id": str(uuid.uuid4())}


class _K:
    def __init__(self):
        self.sql = []

    async def execute(self, sql, *a):
        self.sql.append((" ".join(sql.split()), a))


def _jenis(sql):
    return sql.split("(")[0].strip()[:60]


def test_tulis_tx_manual_urutan_sql_sama_dengan_handler_lama():
    body = CreateManualTransactionRequest(direction="out", amount=500000, transaction_date=date(2026, 10, 10), description="beli lakban", contra_account_id=str(LAWAN), contact_name="Budi")
    baris = [(LAWAN, D(500000), D(0), "beli lakban"), (COA_BANK, D(0), D(500000), "beli lakban")]
    r = {"blocks": [], "akun": {"id": REK, "coa_id": COA_BANK, "saldo": D(2000000)}, "baris": baris, "saldo": []}
    k = _K()
    hasil = asyncio.run(KT.tulis_tx_manual(k, CTX, REK, body, r))
    urut = [s for s, _ in k.sql]
    assert urut[0].startswith("INSERT INTO journal_entries") and "'DRAFT'" in urut[0]
    assert [s.startswith("INSERT INTO journal_lines") for s in urut[1:3]] == [True, True]
    assert urut[3] == "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1"
    assert urut[4].startswith("INSERT INTO bank_transactions") and "'MANUAL','POSTED'" in urut[4].replace(" ", "")
    assert urut[5] == "UPDATE journal_entries SET source_id = $1 WHERE id = $2" and len(urut) == 6
    je = k.sql[0][1]
    assert je[1] == T and je[3] == date(2026, 10, 10) and je[4] == "Uang Keluar - beli lakban" and je[6] == 500000 and je[0] == hasil["journal_id"]
    assert hasil["journal_number"].startswith("MT-") and len(hasil["journal_number"]) == 11
    bt = k.sql[4][1]
    assert bt[0] == hasil["bank_txn_id"] and bt[4] == "withdrawal" and bt[5] == -500000 and bt[6] == 2000000 - 500000 and bt[7] == "beli lakban" and bt[8] == "Budi"
    assert [k.sql[1][1][2], k.sql[2][1][2]] == [1, 2] and k.sql[1][1][3] == LAWAN and k.sql[2][1][3] == COA_BANK     # baris jurnal PERSIS dari penentu (== pratinjau), nomor baris 1,2


def test_tulis_tx_manual_uang_masuk_deposit_bertanda_positif():
    body = CreateManualTransactionRequest(direction="in", amount=100000, transaction_date=date(2026, 10, 11), description="setoran", contra_account_id=str(LAWAN))
    r = {"blocks": [], "akun": {"id": REK, "coa_id": COA_BANK, "saldo": D(0)}, "baris": [(COA_BANK, D(100000), D(0), "setoran"), (LAWAN, D(0), D(100000), "setoran")], "saldo": []}
    k = _K()
    asyncio.run(KT.tulis_tx_manual(k, CTX, REK, body, r))
    assert k.sql[0][1][4] == "Uang Masuk - setoran" and k.sql[4][1][4] == "deposit" and k.sql[4][1][5] == 100000 and k.sql[4][1][6] == 100000


def test_kunci_tx_manual_advisory_per_rekening():
    k = _K()
    asyncio.run(KT.kunci_tx_manual(k, REK))
    assert k.sql == [("SELECT pg_advisory_xact_lock(hashtext($1))", (f"BANK_TX_MANUAL:{REK}",))]


def test_tulis_void_manual_urutan_sql_sama_dengan_handler_lama(monkeypatch):
    async def hari_ini(conn, tid):
        return date(2026, 11, 12)
    monkeypatch.setattr(KT, "tanggal_dokumen", hari_ini)
    tx = {"id": TX, "journal_id": JE, "bank_account_id": REK, "transaction_type": "withdrawal", "amount": D(-500000)}
    r = {"blocks": [], "tx": tx, "je": {"id": JE, "journal_number": "MT-AB12CD34"}, "jumlah": D(500000),
         "baris": [(COA_BANK, D(500000), D(0), "balik"), (LAWAN, D(0), D(500000), "balik")], "saldo": []}
    k = _K()
    hasil = asyncio.run(KT.tulis_void_manual(k, CTX, TX, "salah catat", r))
    urut = [s for s, _ in k.sql]
    assert urut[0].startswith("INSERT INTO journal_entries") and "reversal_of_id" in urut[0] and "'DRAFT'" in urut[0]
    assert urut[1].startswith("INSERT INTO journal_lines") and urut[2].startswith("INSERT INTO journal_lines")
    assert urut[3] == "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1"
    assert urut[4].startswith("UPDATE journal_entries SET reversed_by_id = $2, reversed_at = NOW()")
    assert urut[5].startswith("INSERT INTO bank_transactions") and "'manual_void'" in urut[5]
    assert urut[6].startswith("UPDATE bank_transactions SET status = 'VOIDED'") and len(urut) == 7
    je = k.sql[0][1]
    assert je[2] == "RV-MT-AB12CD34" and je[3] == "Void MT-AB12CD34 - salah catat" and je[5] == JE and je[8] == date(2026, 11, 12) and hasil["reversal_number"] == "RV-MT-AB12CD34"
    mirror = k.sql[5][1]
    assert mirror[3] == "deposit" and mirror[4] == 500000 and mirror[7] == "Void - salah catat" and k.sql[4][1][1] == hasil["reversal_journal_id"]


def test_kunci_void_manual_advisory_per_transaksi():
    k = _K()
    asyncio.run(KT.kunci_void_manual(k, TX))
    assert k.sql == [("SELECT pg_advisory_xact_lock(hashtext($1))", (f"BANK_TX_VOID:{TX}",))]


def test_handler_lama_memanggil_penulis_hasil_ekstraksi_tanpa_sql_jurnal_inline():
    a = inspect.getsource(BAR.create_manual_transaction)
    assert "KT.tulis_tx_manual(" in a and "KT.kunci_tx_manual(" in a and "INSERT INTO journal_entries" not in a and "INSERT INTO bank_transactions" not in a
    b = inspect.getsource(KV2.void_transaction)
    assert "KT.tulis_void_manual(" in b and "KT.kunci_void_manual(" in b and "INSERT INTO journal_entries" not in b and "INSERT INTO bank_transactions" not in b
    for nama in ("tulis_tx_manual", "tulis_void_manual"):
        assert "float(" not in inspect.getsource(getattr(KT, nama))


# ---- periode tertutup pada penentu void manual (BF-1c2, permintaan MASTER): tanggal ASLI dan hari ini diperiksa; tak ada pergeseran tanggal diam-diam
class _KV:
    def __init__(self, tertutup):
        self.tertutup = set(tertutup)

    async def fetchrow(self, sql, *a):
        s = " ".join(sql.split())
        if "count(*) FILTER" in s:
            return {"rekon": 0, "cocok": 0, "bertaut": 0}
        if s.startswith("SELECT bt.*, ba.coa_id AS bank_coa_id"):
            return {"id": TX, "journal_id": JE, "bank_account_id": REK, "bank_coa_id": COA_BANK, "transaction_type": "withdrawal", "amount": D(-500000), "status": "POSTED",
                    "reconciliation_status": "UNRECONCILED", "transaction_date": date(2026, 10, 2), "asal_sah": True}
        if s.startswith("SELECT id, journal_number, source_type"):
            return {"id": JE, "journal_number": "MT-AB12CD34", "source_type": "BANK_TRANSACTION", "source_id": TX, "reversed_by_id": None}
        if s.startswith("SELECT id, period_name, status FROM fiscal_periods"):
            return {"id": 1, "period_name": "Periode uji", "status": "CLOSED" if a[1] in self.tertutup else "OPEN"}
        if s.startswith("SELECT ba.id, ba.account_name"):
            return {"id": REK, "account_name": "BCA UJI", "account_type": "bank", "coa_id": COA_BANK, "is_active": True, "saldo": D(1000000)}
        raise AssertionError("fetchrow SQL tak dikenal: " + s[:80])

    async def fetch(self, sql, *a):
        return [{"account_id": LAWAN, "debit": D(500000), "credit": D(0), "memo": "m", "line_number": 1}, {"account_id": COA_BANK, "debit": D(0), "credit": D(500000), "memo": "m", "line_number": 2}]


def _rencana_void(monkeypatch, tertutup):
    from app.services.bayar_keluar_rencana import Blok

    async def hari_ini(conn, tid):
        return date(2026, 11, 12)
    monkeypatch.setattr(KT, "tanggal_dokumen", hari_ini)
    bl = Blok(True)
    r = asyncio.run(KT.rencana_void_manual(_KV(tertutup), T, TX, "salah catat", bl))
    return bl, r


def test_void_manual_periode_tanggal_ASLI_tertutup_diblok_tanpa_geser_tanggal(monkeypatch):
    bl, r = _rencana_void(monkeypatch, {date(2026, 10, 2)})
    assert [b["code"] for b in bl.daftar] == ["PERIODE_TERTUTUP"] and "Periode uji" in bl.daftar[0]["message"]


def test_void_manual_periode_HARI_INI_tertutup_diblok(monkeypatch):
    bl, r = _rencana_void(monkeypatch, {date(2026, 11, 12)})
    assert [b["code"] for b in bl.daftar] == ["PERIODE_TERTUTUP"]


def test_void_manual_kedua_periode_terbuka_lolos_dan_baris_pembalik_menukar_debit_kredit(monkeypatch):
    bl, r = _rencana_void(monkeypatch, set())
    assert bl.daftar == [] and r["baris"] == [(LAWAN, D(0), D(500000), "Reversal - m"), (COA_BANK, D(500000), D(0), "Reversal - m")] and r["jumlah"] == D(500000)
