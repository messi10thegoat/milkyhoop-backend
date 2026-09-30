"""POST /credit-notes/preview (30 Sep 2026, F2 c, halaman CW nota kredit).

Rencana (_rencana_nota_kredit) mengumpulkan SEMUA penghalang lewat helper yang SAMA dengan inti; bila bersih,
buat_nota_kredit + posting_nota_kredit (isi transaksi rute create/post lama, dipindah utuh; paritas byte 9 skenario di
salinan DB, kontrol merah memerah) jalan di savepoint, jurnal yang lahir dibaca, transaksi SELALU di-ROLLBACK.
"""
import os
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

import ast  # noqa: E402
import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from app.routers import credit_notes as CN  # noqa: E402
from app.schemas.credit_notes import CreateCreditNoteRequest  # noqa: E402

T = "kaos-biru-konveksi"
INV = UUID("20000000-0000-0000-0000-0000000000d1")
HARI = date(2026, 9, 30)


def _req():
    return SimpleNamespace(state=SimpleNamespace(user={"user_id": "00000000-0000-0000-0000-0000000000a1",
                                                       "tenant_id": T, "role": "OWNER"}), headers={})


class _Tx:
    def __init__(self, c):
        self.c = c

    async def start(self):
        self.c.tx.append("start")

    async def rollback(self):
        self.c.tx.append("rollback")

    async def __aenter__(self):
        self.c.tx.append("sp")
        return self.c

    async def __aexit__(self, et, *a):
        self.c.tx.append("sp-rollback" if et else "sp-release")
        return False


class _Acq:
    def __init__(self, c):
        self.c = c

    async def __aenter__(self):
        return self.c

    async def __aexit__(self, *a):
        return False


class _Pool:
    def __init__(self, c):
        self.c = c

    def acquire(self):
        return _Acq(self.c)


class _C:
    def __init__(self, periode=None):
        self.periode, self.tx, self.q = periode, [], []

    def transaction(self):
        return _Tx(self)

    async def execute(self, sql, *a):
        self.q.append(sql)

    async def fetchrow(self, sql, *a):
        if "FROM fiscal_periods" in sql:
            return {"status": self.periode} if self.periode else None
        raise AssertionError(sql[:80])

    async def fetchval(self, sql, *a):
        if "compute_ar_outstanding" in sql:  # sisa tagihan: sebelum 80.000, sesudah posting (di savepoint) 30.000
            return Decimal("30000") if "sp" in self.tx and self.tx[-1] == "sp" else Decimal("80000")
        raise AssertionError(sql[:80])

    async def fetch(self, sql, *a):
        if "FROM journal_entries" in sql and "created_at = NOW()" in sql:
            return [{"id": UUID(int=1), "journal_number": "CN-2609-0009", "source_type": "CREDIT_NOTE", "total_debit": Decimal("50000")}]
        if "FROM journal_lines" in sql:
            return [{"account_code": "4-10300", "name": "Retur Penjualan", "debit": Decimal("50000"), "credit": Decimal("0")},
                    {"account_code": "1-10400", "name": "Piutang Usaha", "debit": Decimal("0"), "credit": Decimal("50000")}]
        if "FROM inventory_ledger" in sql:
            return []
        raise AssertionError(sql[:80])


def _body(**k):
    b = dict(customer_id=str(UUID(int=4)), customer_name="Budi", credit_note_date=HARI, reason="pricing_error",
             items=[{"description": "Koreksi", "quantity": 1, "unit_price": 50000}], original_invoice_id=str(INV))
    b.update(k)
    return CreateCreditNoteRequest(**b)


@pytest.fixture
def pasang(monkeypatch):
    rekam = {"buat": 0, "post": 0}

    def _p(c, pelanggan_salah=False, diskon_salah=False, faktur_salah=False, lebih=False, non_pkp=False, inti_gagal=None):
        async def pelanggan(conn, tid, v):
            if pelanggan_salah:
                raise HTTPException(400, "Pelanggan tidak ditemukan")
            return v
        monkeypatch.setattr(CN, "pelanggan_kanonik_tenant", pelanggan)

        async def doc(conn, tid, items, dp, da, tr):
            if diskon_salah:
                raise HTTPException(400, "Diskon dokumen melebihi subtotal")
            return {"items": [{"description": "Koreksi", "quantity": Decimal("1"), "unit_price": Decimal("50000"),
                               "subtotal": Decimal("50000"), "tax_amount": Decimal("5500"), "total": Decimal("55500"),
                               "dpp": Decimal("50000"), "item_code": None}],
                    "gross_subtotal": Decimal("50000"), "doc_discount": Decimal("0"), "tax_amount": Decimal("5500"),
                    "total_amount": Decimal("55500")}
        monkeypatch.setattr(CN, "_cn_doc", doc)

        async def faktur(conn, tid, iid, p):
            if faktur_salah:
                raise HTTPException(400, "Faktur tidak ditemukan")
            return {"id": INV, "invoice_number": "INV-7"}
        monkeypatch.setattr(CN, "faktur_tenant_untuk_pelanggan", faktur)

        async def muat(conn, tid, f, total):
            if lebih:
                raise HTTPException(400, "Nota kredit melebihi sisa tagihan INV-7")
        monkeypatch.setattr(CN, "pastikan_cn_muat_faktur", muat)

        async def pkp(conn, tid, tax):
            if non_pkp:
                raise HTTPException(422, "Tenant bukan PKP")
        monkeypatch.setattr(CN, "tolak_ppn_bila_non_pkp", pkp)

        async def sisa(conn, tid, iid):
            return Decimal("80000")
        monkeypatch.setattr(CN, "get_invoice_remaining_from_journal", sisa)

        async def buat(conn, ctx, body):
            rekam["buat"] += 1
            rekam["tx_buat"] = list(conn.tx)
            return {"data": {"id": str(UUID(int=9)), "credit_note_number": "CN-2609-0031"}}
        monkeypatch.setattr(CN, "buat_nota_kredit", buat)

        async def post(conn, ctx, cid):
            rekam["post"] += 1
            if inti_gagal:
                raise HTTPException(400, inti_gagal)
            return {"data": {"status": "posted"}}
        monkeypatch.setattr(CN, "posting_nota_kredit", post)

        async def pra(*a, **k):
            return None
        monkeypatch.setattr(CN, "_ensure_role_preconditions", pra)

        async def pool():
            return _Pool(c)
        monkeypatch.setattr(CN, "get_pool", pool)
        return c
    _p.rekam = rekam
    return _p


async def _pv(body=None):
    return (await CN.preview_credit_note(_req(), body or _body()))["data"]


def _kode(d):
    return [b["code"] for b in d["blocks"]]


@pytest.mark.asyncio
async def test_semua_blok_dikumpulkan_tanpa_inti(pasang):
    pasang(_C(periode="CLOSED"), pelanggan_salah=True, faktur_salah=True, non_pkp=True)
    d = await _pv()
    assert _kode(d) == ["CN_CUSTOMER_INVALID", "CN_INVOICE_INVALID", "CN_TAX_NON_PKP", "CN_PERIOD_CLOSED"]
    assert d["ok"] is False and d["payload"] is None and d["journals_on_post"] == []
    assert pasang.rekam["buat"] == 0 and pasang.rekam["post"] == 0


@pytest.mark.asyncio
async def test_diskon_dan_lebih_faktur(pasang):
    pasang(_C(), diskon_salah=True)
    d = await _pv()
    assert _kode(d) == ["CN_DISCOUNT_INVALID"] and d["total_amount"] is None
    pasang(_C(), lebih=True)
    d = await _pv()
    assert _kode(d) == ["CN_EXCEEDS_INVOICE"]


@pytest.mark.asyncio
async def test_bersih_buat_lalu_posting_di_savepoint_lalu_rollback(pasang):
    c = pasang(_C())
    d = await _pv()
    assert d["ok"] is True and d["blocks"] == []
    assert pasang.rekam["buat"] == 1 and pasang.rekam["post"] == 1
    assert pasang.rekam["tx_buat"][-1] == "sp"  # inti di savepoint
    assert c.tx[0] == "start" and c.tx[-1] == "rollback"  # SELALU rollback
    assert d["credit_note_number_preview"] == "CN-2609-0031" and d["total_amount"] == 55500.0
    assert d["journals_on_post"][0]["lines"][1] == {"account_code": "1-10400", "account_name": "Piutang Usaha",
                                                    "debit": 0.0, "credit": 50000.0}
    # 30 Sep: NK bertaut LANGSUNG memotong sisa tagihan saat posting (diukur nyata CN-2609-0030) -> sebelum/sesudah
    assert d["invoice"] == {"id": str(INV), "invoice_number": "INV-7", "remaining": 80000.0, "remaining_after_post": 30000.0}
    assert [n["code"] for n in d["notes"]] == ["CN_REDUCES_INVOICE"]
    assert d["payload"]["original_invoice_id"] == str(INV) and d["payload"]["items"][0]["description"] == "Koreksi"


@pytest.mark.asyncio
async def test_penolakan_inti_jadi_blok(pasang):
    pasang(_C(), inti_gagal="Retur X melebihi yang terkirim")
    d = await _pv()
    assert _kode(d) == ["CN_REJECTED"] and d["blocks"][0]["message"] == "Retur X melebihi yang terkirim"
    assert d["payload"] is None and d["journals_on_post"] == [] and d["credit_note_number_preview"] is None


def _fungsi(nama):
    t = ast.parse(open(CN.__file__, encoding="utf-8").read())
    return next(n for n in ast.walk(t) if isinstance(n, ast.AsyncFunctionDef) and n.name == nama)


@pytest.mark.parametrize("rute,inti", [("create_credit_note", "buat_nota_kredit"), ("post_credit_note", "posting_nota_kredit")])
def test_rute_lama_memanggil_inti_di_dalam_transaksi(rute, inti):
    fn = _fungsi(rute)
    tx = [n for n in ast.walk(fn) if isinstance(n, ast.AsyncWith) and "conn.transaction" in ast.unparse(n.items[0].context_expr)]
    assert len(tx) == 1
    p = [c for c in ast.walk(tx[0]) if isinstance(c, ast.Call) and getattr(c.func, "id", None) == inti]
    assert len(p) == 1, f"{rute} wajib memanggil {inti} tepat sekali DI DALAM transaksinya"


def test_inti_posting_memegang_kunci_dan_cek_periode():
    src = open(CN.__file__, encoding="utf-8").read()
    s = " ".join(ast.get_source_segment(src, _fungsi("posting_nota_kredit")).split())
    assert "CREDIT_NOTE:{credit_note_id}" in s and "pg_advisory_xact_lock" in s
    assert "Periode akuntansi sudah" in s and "UPDATE credit_notes SET status = 'posted'" in s
