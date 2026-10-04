"""U11 (5 Okt 2026, MASTER+WORKSPACE): status_detail TURUNAN pada Nota Kredit (pola uang muka). Status tersimpan dan semua
penjaga tak disentuh. Python (pemetaan baris) dan SQL (filter/summary) HARUS sama -- dikunci di sini lalu dibuktikan di data
nyata kaos (harness): pemetaan Python == hasil SQL untuk SETIAP NK."""
import asyncio
import inspect
import re
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app.routers import credit_notes as C
from app.schemas.credit_notes import CreditNoteDetail, CreditNoteListItem

T = datetime(2026, 10, 3, 5, 0, tzinfo=timezone.utc)
KASUS = [  # (status, applied, refunded) -> status_detail
    ("draft", 0, 0, "draft"), ("posted", 0, 0, "posted"), ("void", 0, 0, "void"),
    ("partial", 0, 4000, "partially_refunded"), ("applied", 0, 10000, "refunded"),
    ("applied", 10000, 0, "applied"),                       # diterapkan ke faktur = tetap Terpakai
    ("partial", 5000, 0, "partial"),                        # terpakai sebagian (tak terjadi lewat apply, tetap ditegakkan)
    ("partial", 5000, 3000, "partial"), ("applied", 5000, 5000, "applied"),  # campuran: ada penerapan -> status tersimpan
    ("partial", None, 4000, "partially_refunded"), ("applied", None, 10000, "refunded"), ("posted", None, None, "posted"),
    ("posted", 0, 4000, "posted"),                          # tak terjadi (trigger menjadikannya partial); jangan menebak
]


@pytest.mark.parametrize("st,ap,rf,harap", KASUS)
def test_pemetaan_python(st, ap, rf, harap):
    assert C.status_detail_nk(st, ap, rf) == harap


@pytest.mark.parametrize("st,ap,rf,harap", KASUS)
def test_sql_sama_dengan_python(st, ap, rf, harap):
    """Evaluasi ungkapan SQL secara harfiah (CASE sederhana) terhadap baris yang sama -> sama dengan pemetaan Python."""
    sql = " ".join(C._SQL_STATUS_DETAIL_NK.split())
    a, r = (ap or 0), (rf or 0)
    m = re.fullmatch(r"\(CASE WHEN status = 'partial' AND COALESCE\(amount_applied, 0\) = 0 AND COALESCE\(amount_refunded, 0\) > 0 "
                     r"THEN 'partially_refunded' WHEN status = 'applied' AND COALESCE\(amount_applied, 0\) = 0 AND "
                     r"COALESCE\(amount_refunded, 0\) > 0 THEN 'refunded' ELSE status END\)", sql)
    assert m, sql
    hasil = ("partially_refunded" if st == "partial" and a == 0 and r > 0 else
             "refunded" if st == "applied" and a == 0 and r > 0 else st)
    assert hasil == harap


def test_awalan_alias_sql_untuk_summary():
    s = " ".join(C._sql_status_detail_nk("cn.").split())
    assert "cn.status = 'partial'" in s and "COALESCE(cn.amount_applied, 0) = 0" in s and "ELSE cn.status END" in s
    assert C._sql_status_detail_nk() == C._SQL_STATUS_DETAIL_NK and "cn." not in C._SQL_STATUS_DETAIL_NK


def test_skema_dan_filter_kosakata():
    assert "status_detail" in CreditNoteListItem.model_fields and "status_detail" in CreditNoteDetail.model_fields
    src = inspect.getsource(C.list_credit_notes)
    assert '"partially_refunded", "refunded"' in src
    assert 'conditions.append(f"{_SQL_STATUS_DETAIL_NK} = ${param_idx}")' in src  # filter mengikuti status_detail


class _Konn:
    def __init__(self, rows=(), row=None):
        self.rows, self.row, self.sql = list(rows), row, []

    async def fetchval(self, sql, *a):
        self.sql.append((sql, a))
        return len(self.rows)

    async def fetch(self, sql, *a):
        self.sql.append((sql, a))
        return [] if "FROM sales_invoices" in sql else self.rows

    async def fetchrow(self, sql, *a):
        self.sql.append((sql, a))
        return self.row


class _Req:
    class state:
        user = {"tenant_id": "t1", "user_id": "0bccdb25-fdf0-4e99-9024-b9a20846f76c"}


def _pool(monkeypatch, k):
    class _A:
        async def __aenter__(s):
            return k

        async def __aexit__(s, *a):
            return False

    class _P:
        def acquire(self_):
            return _A()

    async def gp():
        return _P()
    monkeypatch.setattr(C, "get_pool", gp)

    async def tk(c, t, jenis, dokumen, kunci="id"):
        return dokumen
    import app.services.kode_order as KO
    monkeypatch.setattr(KO, "tempel_kode", tk)


def _baris(status, ap, rf):
    return {"id": uuid.uuid4(), "credit_note_number": "CN-1", "customer_id": "c1", "customer_name": "X",
            "credit_note_date": date(2026, 10, 1), "total_amount": 10000, "amount_applied": ap, "amount_refunded": rf,
            "status": status, "reason": "retur", "created_at": T, "original_invoice_id": None,
            "original_invoice_number": None, "voided_at": None, "voided_reason": None}


def test_daftar_membawa_status_detail_per_baris(monkeypatch):
    k = _Konn([_baris("partial", 0, 4000), _baris("applied", 0, 10000), _baris("applied", 10000, 0), _baris("posted", 0, 0)])
    _pool(monkeypatch, k)
    h = asyncio.run(C.list_credit_notes(_Req(), status="all", customer_id=None, search=None, date_from=None, date_to=None,
                                        skip=0, limit=20, sort_by="created_at", sort_order="desc"))
    assert [i["status_detail"] for i in h["items"]] == ["partially_refunded", "refunded", "applied", "posted"]
    assert [i["status"] for i in h["items"]] == ["partial", "applied", "applied", "posted"]  # status tersimpan utuh


@pytest.mark.parametrize("filter_", ["partially_refunded", "refunded", "partial", "applied"])
def test_filter_status_memakai_ungkapan_status_detail_terikat_parameter(monkeypatch, filter_):
    k = _Konn([])
    _pool(monkeypatch, k)
    asyncio.run(C.list_credit_notes(_Req(), status=filter_, customer_id=None, search=None, date_from=None, date_to=None,
                                    skip=0, limit=20, sort_by="created_at", sort_order="desc"))
    for sql, a in (q for q in k.sql if "FROM credit_notes" in q[0]):
        assert " ".join(C._SQL_STATUS_DETAIL_NK.split()) in " ".join(sql.split()) and "= $2" in sql
        assert a[:2] == ("t1", filter_)


def test_summary_menambah_dua_hitungan_tanpa_mengubah_yang_lama(monkeypatch):
    baris = {"total": 6, "draft_count": 1, "posted_count": 1, "partial_count": 2, "applied_count": 2,
             "partially_refunded_count": 1, "refunded_count": 1, "total_value": Decimal("100"), "total_applied": 0,
             "total_refunded": 0, "available_balance": 0}
    k = _Konn(row=baris)
    _pool(monkeypatch, k)
    k.fetchval = lambda sql, *a: _vv()

    async def _vv():
        return 0
    d = asyncio.run(C.get_credit_notes_summary(_Req()))["data"]
    assert d["partially_refunded_count"] == 1 and d["refunded_count"] == 1
    assert d["partial_count"] == 2 and d["applied_count"] == 2 and d["total"] == 6  # hitungan lama = status tersimpan
    main = " ".join(next(q[0] for q in k.sql if "WITH cn_journal" in q[0]).split())
    assert "COUNT(*) FILTER (WHERE cn.status = 'partial') as partial_count" in main
    assert main.count(" ".join(C._sql_status_detail_nk("cn.").split())) == 2


@pytest.mark.parametrize("st,ap,rf,harap", [("partial", 4000, 0, "partial"), ("partial", 0, 4000, "partially_refunded")])
def test_keadaan_pratinjau_membawa_status_detail(st, ap, rf, harap):
    class K(_Konn):
        async def fetchrow(self, sql, *a):
            return {"credit_note_number": "CN-1", "status": st, "total_amount": 10000, "amount_applied": ap,
                    "amount_refunded": rf, "original_invoice_id": None, "created_deposit_id": None}
    ke = asyncio.run(C._keadaan_nk(K(), "t1", uuid.uuid4()))
    assert ke["status_detail"] == harap and ke["status"] == st


def test_detail_membawa_status_detail_dari_baris_tersimpan():
    src = " ".join(inspect.getsource(C.get_credit_note).split())
    assert '"status_detail": status_detail_nk(cn["status"], cn["amount_applied"], cn["amount_refunded"])' in src
