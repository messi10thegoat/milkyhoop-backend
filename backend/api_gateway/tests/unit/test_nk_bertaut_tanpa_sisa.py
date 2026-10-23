"""NK BERTAUT faktur = sisa kredit 0; refund/apply DARI NK itu 409 berkode (12 Okt 2026, putusan MASTER, pola NetSuite:
kredit yang sudah diterapkan ke invoice = applied; kelebihan = Customer Deposit KRD). Latar: kasus pemilik grapgrap
031-09-26 -- NK penuh atas INV lunas -> seluruhnya ke KRD; refund NK (Dr Piutang / Cr Bank) dulu tetap terbuka ->
piutang hantu + dana bisa keluar ganda. NK bebas (tanpa faktur): tak berubah."""
import asyncio
import ast
import os
import uuid
from decimal import Decimal
from pathlib import Path

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

import pytest
from fastapi import HTTPException

from app.routers import credit_notes as CN

SRC = Path(CN.__file__).read_text()


def _fungsi(nama):
    for n in ast.walk(ast.parse(SRC)):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == nama:
            return ast.get_source_segment(SRC, n)
    raise AssertionError(nama)


FAKTUR = uuid.uuid4()


@pytest.mark.parametrize("status,faktur,harap", [
    ("posted", None, Decimal("700")),        # NK bebas: total - applied - refunded (tak berubah)
    ("partial", None, Decimal("700")),
    ("posted", FAKTUR, Decimal("0")),        # NK bertaut terbit: kredit sudah dipakai faktur / pindah ke KRD
    ("applied", FAKTUR, Decimal("0")),
    ("draft", FAKTUR, Decimal("700")),       # draf belum terbit: rumus biasa (belum memotong apa pun)
    ("void", None, Decimal("0")),
    ("void", FAKTUR, Decimal("0")),
])
def test_sisa_kredit_nk_satu_rumus(status, faktur, harap):
    assert CN.sisa_kredit_nk(status, Decimal("1000"), Decimal("200"), Decimal("100"), faktur) == harap


class Konek:
    def __init__(self, krd="KRD-2610-0007"):
        self.krd, self.log = krd, []

    async def fetchval(self, sql, *a):
        self.log.append((sql, a))
        assert "FROM customer_deposits" in sql and "tenant_id = $2" in sql
        return self.krd


def _nk(**kw):
    d = {"status": "posted", "original_invoice_id": FAKTUR, "original_invoice_number": "INV-2609-0023", "created_deposit_id": None}
    d.update(kw)
    return d


def test_tolak_bertaut_ber_krd_409_kode_dan_nomor_krd():
    k = Konek()
    with pytest.raises(HTTPException) as e:
        asyncio.run(CN.tolak_nk_bertaut(k, "t", _nk(created_deposit_id=uuid.uuid4())))
    assert e.value.status_code == 409 and e.value.detail["code"] == "CN_KREDIT_DI_UANG_MUKA"
    assert e.value.detail["message"] == "Kredit nota ini sudah menjadi saldo kredit KRD-2610-0007; kembalikan lewat uang muka itu."
    assert k.log[0][1][1] == "t"   # tenant eksplisit


def test_tolak_bertaut_tanpa_krd_409_sudah_memotong_faktur():
    with pytest.raises(HTTPException) as e:
        asyncio.run(CN.tolak_nk_bertaut(Konek(), "t", _nk()))
    assert e.value.status_code == 409 and e.value.detail["code"] == "CN_SUDAH_MEMOTONG_FAKTUR"
    assert "INV-2609-0023" in e.value.detail["message"]


def test_nk_bebas_lolos_pagar():
    asyncio.run(CN.tolak_nk_bertaut(Konek(), "t", _nk(original_invoice_id=None, original_invoice_number=None)))


def test_refund_dan_apply_memakai_pagar_sebelum_menghitung_sisa():
    # NK-RENCANA-A (23 Okt): pemeriksaan pindah ke penentu bersama (services/nota_kredit_rencana); inti memanggilnya sebelum menulis
    import inspect
    from app.services import nota_kredit_rencana as NKR
    rr = inspect.getsource(NKR.rencana_refund_nk)
    assert rr.index("tolak_nk_bertaut(conn, tid, cn)") < rr.index("sisa = R.sisa_kredit_nk(")
    r = _fungsi("refund_nota_kredit_core")
    assert r.index("NKR.rencana_refund_nk(") < r.index("INSERT INTO journal_entries")
    assert "R.tolak_nk_bertaut(conn, tid, cn)" in inspect.getsource(NKR.rencana_terapkan_nk)
    a = _fungsi("apply_nota_kredit_core")
    assert a.index("NKR.rencana_terapkan_nk(") < a.index("INSERT INTO")


def test_semua_pembaca_sisa_memakai_rumus_yang_sama():
    # daftar, detail, keadaan pratinjau, refund -> sisa_kredit_nk; tak ada lagi rumus lama total - applied - refunded
    import inspect
    from app.services import nota_kredit_rencana as NKR
    SRC_NKR = inspect.getsource(NKR)
    assert SRC.count("sisa_kredit_nk(") >= 4   # definisi + daftar, detail, keadaan
    assert SRC_NKR.count("R.sisa_kredit_nk(") == 3   # refund (penentu) + keadaan sesudah pratinjau A + keadaan sesudah pratinjau terbit (B)
    assert 'sisa = cn["total_amount"] - (cn["amount_applied"] or 0)' not in SRC
    assert SRC_NKR.count("tolak_nk_bertaut(conn, tid, cn)") == 2              # penentu apply + refund memblok
    assert '- (row["amount_refunded"] or 0)' not in SRC and '- (cn["amount_refunded"] or 0)' not in SRC
    assert "AND cn.original_invoice_id IS NULL), 0) as available_balance" in SRC   # ringkasan: NK bebas saja


def test_draf_bertaut_tetap_pesan_lama_400():
    with pytest.raises(HTTPException) as e:
        asyncio.run(CN.tolak_nk_bertaut(Konek(), "t", _nk(status="draft")))
    assert e.value.status_code == 400 and e.value.detail == "Nota kredit ini sudah terkait ke faktur."


# ---------------- customer_credit di GET daftar/detail NK (12 Okt 2026, kontrak FE) ----------------
class KonekKrd:
    def __init__(self, rows, sisa):
        self.rows, self.sisa, self.sql = rows, sisa, []

    async def fetch(self, sql, *a):
        self.sql.append((sql, a))
        assert "tenant_id = $1" in sql and a[0] == "t"
        return self.rows


def test_saldo_kredit_nk_many_sisa_jurnal_dan_void_nol(monkeypatch):
    k1, k2 = uuid.uuid4(), uuid.uuid4()
    rows = [{"id": k1, "deposit_number": "KRD-1", "amount": Decimal("170000"), "status": "posted"},
            {"id": k2, "deposit_number": "KRD-2", "amount": Decimal("5000"), "status": "void"}]

    async def sisa(conn, tid, ids):
        return {str(k1): Decimal("120000"), str(k2): Decimal("5000")}
    import app.routers.customer_deposits as CD
    monkeypatch.setattr(CD, "compute_deposit_remaining_many", sisa)
    out = asyncio.run(CN.saldo_kredit_nk_many(KonekKrd(rows, None), "t", [k1, k2, None, k1]))
    assert out[str(k1)] == {"deposit_id": str(k1), "deposit_number": "KRD-1", "amount": 170000.0, "status": "posted",
                            "remaining_amount": 120000.0}
    assert out[str(k2)]["remaining_amount"] == 0.0          # KRD void (NK di-void) -> tak bisa dikembalikan
    assert asyncio.run(CN.saldo_kredit_nk_many(KonekKrd([], None), "t", [None])) == {}


def test_skema_daftar_dan_detail_mempertahankan_customer_credit():
    from app.schemas import credit_notes as S
    cc = {"deposit_id": str(uuid.uuid4()), "deposit_number": "KRD-1", "amount": 1.0, "remaining_amount": 1.0, "status": "posted"}
    assert S.CreditNoteListItem.model_fields["customer_credit"].default is None
    assert S.CreditNoteDetail.model_fields["customer_credit"].default is None
    assert S.NkSaldoKredit(**cc).model_dump() == cc


def test_detail_409_membawa_nomor_krd_dan_faktur():
    with pytest.raises(HTTPException) as e:
        asyncio.run(CN.tolak_nk_bertaut(Konek(), "t", _nk(created_deposit_id=uuid.uuid4())))
    assert e.value.detail["deposit_number"] == "KRD-2610-0007" and e.value.detail["deposit_id"]
    with pytest.raises(HTTPException) as e:
        asyncio.run(CN.tolak_nk_bertaut(Konek(), "t", _nk()))
    assert e.value.detail["invoice_number"] == "INV-2609-0023" and e.value.detail["invoice_id"] == str(FAKTUR)


def test_get_daftar_dan_detail_mengisi_customer_credit():
    assert 'created_deposit_id\n                FROM credit_notes' in SRC or "voided_reason, created_deposit_id" in SRC
    assert '"customer_credit": _kredit.get(' in _fungsi("list_credit_notes")
    assert '"customer_credit": (await saldo_kredit_nk_many(' in _fungsi("get_credit_note")
