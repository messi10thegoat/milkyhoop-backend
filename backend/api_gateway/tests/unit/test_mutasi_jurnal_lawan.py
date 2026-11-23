"""23 Nov 2026 (kasus grapgrap Kas Operasional): Mutasi lama GET /bank-accounts/{id}/transactions ikut memuat jurnal POSTED pada CoA rekening yang
TIDAK punya baris bank_transactions di rekening itu (source='jurnal_lawan', amount bertanda, masuk saldo berjalan) -> saldo akhir = Σ jurnal.
Penanda tegas baris pembalik batal + label_sumber teks server."""
import asyncio
from datetime import date
from uuid import uuid4

from starlette.requests import Request

from app.routers import bank_accounts as RB


def _panggil(monkeypatch, **kw):
    tangkap = []

    class K:
        async def fetchrow(self, sql, *a):
            return {"id": a[0], "coa_id": uuid4()}

        async def fetchval(self, sql, *a):
            tangkap.append(("count", sql, a))
            return 0

        async def fetch(self, sql, *a):
            tangkap.append(("daftar", sql, a))
            return []

    class P:
        def acquire(self):
            class Cm:
                async def __aenter__(s):
                    return K()

                async def __aexit__(s, *a):
                    return False
            return Cm()

    async def pool():
        return P()
    monkeypatch.setattr(RB, "get_pool", pool)
    r = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    r.state.user = {"tenant_id": "t1", "user_id": "00000000-0000-0000-0000-000000000001"}
    arg = dict(transaction_type=None, date_from=None, date_to=None, is_reconciled=None, skip=0, limit=50, tanpa_batal=False)
    arg.update(kw)
    asyncio.run(RB.get_bank_transactions(r, uuid4(), **arg))
    return {j: (s, a) for j, s, a in tangkap}


def test_jurnal_tanpa_bt_ikut_di_count_dan_daftar_dan_saldo_berjalan(monkeypatch):
    q = _panggil(monkeypatch)
    for j in ("count", "daftar"):
        sql = q[j][0]
        assert "UNION ALL" in sql and "'jurnal_lawan'" in sql and "bx.bank_account_id = $1" in sql and "je.status = 'POSTED'" in sql, j
        assert "SUM(jl.debit - jl.credit)" in sql, j
    assert "SUM(amount) OVER" in q["daftar"][0]
    assert q["daftar"][1][-2:] == (50, 0)  # limit, offset di belakang


def test_saringan_tanggal_berlaku_juga_untuk_baris_jurnal_nomor_param_berurut(monkeypatch):
    q = _panggil(monkeypatch, date_from=date(2026, 11, 1), date_to=date(2026, 11, 30))
    sql, a = q["count"]
    assert "je.journal_date >= $2" in sql and "je.journal_date <= $3" in sql and a[1:3] == (date(2026, 11, 1), date(2026, 11, 30))


def test_saringan_tipe_atau_rekonsiliasi_hanya_baris_bt(monkeypatch):
    for kw in (dict(transaction_type="deposit"), dict(is_reconciled=True), dict(is_reconciled=False)):
        q = _panggil(monkeypatch, **kw)
        assert "UNION ALL" not in q["count"][0] and "UNION ALL" not in q["daftar"][0], kw
    assert "UNION ALL" in _panggil(monkeypatch, transaction_type="jurnal_lawan")["count"][0]


def _baris(**kw):
    b = dict(source="bank_transaction", journal_id=None, description="x", reference_type=None, reference_number=None, transaction_type="deposit")
    b.update(kw)
    return b


def test_penanda_baris_pembalik_dan_label_sumber():
    P = RB._penanda_mutasi
    assert P(_baris(description="[VOID] Expense: EXP-2611-0026"))["is_void_reversal"] is True
    assert P(_baris(description="[VOID] Expense: EXP-2611-0026"))["label_sumber"] == "Pembalik batal"
    assert P(_baris(reference_type="transfer_void", transaction_type="transfer_in"))["label_sumber"] == "Pembalik batal"
    assert P(_baris(reference_number="VOID-MT-42D51E18"))["is_void_reversal"] is True
    assert P(_baris())["label_sumber"] == "Setoran" and P(_baris())["is_void_reversal"] is False
    assert P(_baris(transaction_type="withdrawal"))["label_sumber"] == "Penarikan"
    assert P(_baris(transaction_type="transfer_in"))["label_sumber"] == "Transfer Masuk"
    j = uuid4()
    r = P(_baris(source="jurnal_lawan", journal_id=j, transaction_type="jurnal_lawan"))
    assert r == {"source": "jurnal_lawan", "journal_id": str(j), "is_void_reversal": False, "label_sumber": "Jurnal"}
