"""Q-018 (26 Sep 2026): aksi "akui pendapatan baris non-stok" + penjaga E2b di impor massal.

INV-2609-0009 grapgrap: Rp 1,5 jt terkunci di Pendapatan Diterima Dimuka (post saat produk berstok WAC=0 ->
ditunda; produk lalu diubah non-stok -> tak bisa dikirim). Aksi hanya untuk baris tertunda yang produknya kini
NON-STOK; jurnal INVOICE_REVENUE Dr Diterima Dimuka / Cr Penjualan, cek periode, idempoten, audit.
"""
import uuid
from datetime import date
from decimal import Decimal as D
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routers import sales_invoices as SI

T = "grapgrap-manado"
INV = uuid.uuid4()
L1 = uuid.uuid4()
AKUN_TANGGUH, AKUN_JUAL = uuid.uuid4(), uuid.uuid4()


class Conn:
    def __init__(self, status="paid", baris=None, tutup=False):
        self.status, self.tutup = status, tutup
        self.baris = [{"id": L1, "allocated_amount": D("1500000"), "recognized_amount": D("0"), "description": "Kaos Pendek + Dtf"}] if baris is None else baris
        self.exec, self.q = [], []

    async def execute(self, sql, *a):
        self.exec.append((" ".join(sql.split()), a))

    async def fetchrow(self, sql, *a):
        self.q.append(sql)
        return {"id": INV, "invoice_number": "INV-2609-0009", "invoice_date": date(2026, 9, 16), "status": self.status,
                "revenue_status": "deferred", "customer_name": "Pelanggan"}

    async def fetch(self, sql, *a):
        self.q.append(" ".join(sql.split()))
        return self.baris

    async def fetchval(self, sql, *a):
        if "is_period_closed" in sql:
            return self.tutup
        if "get_next_journal_number" in sql:
            assert a[1] == "RECOG"
            return "RECOG-2609-0001"
        raise AssertionError(sql)


@pytest.fixture
def pasang(monkeypatch):
    dipanggil = []

    async def _tangguh(c, t):
        return AKUN_TANGGUH

    async def _peran(c, t, role):
        assert role == SI.AccountRole.REVENUE_SALES_GOODS
        return AKUN_JUAL

    async def _status(c, i, t):
        dipanggil.append((i, t))

    monkeypatch.setattr(SI, "_resolve_unearned_revenue", _tangguh)
    monkeypatch.setattr(SI, "resolve_account_id_by_role", _peran)
    monkeypatch.setattr(SI, "_update_invoice_fulfillment_status", _status)
    return dipanggil


def _sql(conn, awal):
    return [e for e in conn.exec if e[0].startswith(awal)]


@pytest.mark.asyncio
async def test_akui_membuat_jurnal_benar(pasang):
    conn = Conn()
    r = await SI._akui_pendapatan_nonstok(conn, T, INV, "u1")
    assert conn.exec[0][0].startswith("SELECT pg_advisory_xact_lock") and conn.exec[0][1][1] == f"INVOICE_FULFILL:{INV}"
    je = _sql(conn, "INSERT INTO journal_entries")[0]
    assert "'INVOICE_REVENUE'" in je[0] and "'DRAFT'" in je[0] and je[1][3] == date(2026, 9, 16) and je[1][7] == "1500000"
    jl = _sql(conn, "INSERT INTO journal_lines")[0]
    assert jl[1][2] == AKUN_TANGGUH and jl[1][5] == AKUN_JUAL and jl[1][4] == "1500000"   # Dr tangguh / Cr jual
    assert _sql(conn, "UPDATE journal_entries SET status = 'POSTED'")
    assert _sql(conn, "UPDATE sales_invoice_items SET recognized_amount = allocated_amount")[0][1][0] == [L1]
    assert _sql(conn, "INSERT INTO audit_logs")[0][1][4] == T
    na = _sql(conn, "UPDATE sales_invoices SET fulfillment_status = 'not_applicable'")
    assert na and "COALESCE(p.track_inventory, false) = true" in na[0][0]
    assert pasang == [(INV, T)]
    assert r["amount"] == 1500000.0 and r["journal_number"] == "RECOG-2609-0001"


def test_kueri_baris_hanya_non_stok_tertunda_berpagar_tenant():
    import inspect
    s = " ".join(inspect.getsource(SI._akui_pendapatan_nonstok).split())
    assert "LEFT JOIN products p ON p.id = sii.item_id AND p.tenant_id = $2" in s
    assert "COALESCE(sii.allocated_amount, 0) - COALESCE(sii.recognized_amount, 0) > 0.005" in s
    assert "COALESCE(p.track_inventory, false) = false" in s


@pytest.mark.asyncio
async def test_klik_kedua_409_tanpa_tulis(pasang):
    conn = Conn(baris=[])
    with pytest.raises(HTTPException) as e:
        await SI._akui_pendapatan_nonstok(conn, T, INV, "u1")
    assert e.value.status_code == 409
    assert not _sql(conn, "INSERT")


@pytest.mark.asyncio
async def test_periode_tertutup_400_tanpa_tulis(pasang):
    conn = Conn(tutup=True)
    with pytest.raises(HTTPException) as e:
        await SI._akui_pendapatan_nonstok(conn, T, INV, "u1")
    assert e.value.status_code == 400 and not _sql(conn, "INSERT")


@pytest.mark.asyncio
@pytest.mark.parametrize("st", ["draft", "void"])
async def test_faktur_tak_terbit_409(pasang, st):
    conn = Conn(status=st)
    with pytest.raises(HTTPException) as e:
        await SI._akui_pendapatan_nonstok(conn, T, INV, "u1")
    assert e.value.status_code == 409 and not _sql(conn, "INSERT")


@pytest.mark.asyncio
async def test_tanggal_pilihan_dipakai(pasang):
    conn = Conn()
    await SI._akui_pendapatan_nonstok(conn, T, INV, "u1", date(2026, 9, 26))
    assert _sql(conn, "INSERT INTO journal_entries")[0][1][3] == date(2026, 9, 26)


class ConnImpor:
    def __init__(self, tunda=None, ledger=False):
        self.tunda, self.ledger = tunda, ledger

    async def fetchval(self, sql, *a):
        s = " ".join(sql.split())
        if "string_agg(DISTINCT si.invoice_number" in s:
            return self.tunda
        if "FROM inventory_ledger" in s:
            return self.ledger
        raise AssertionError(s)


@pytest.mark.asyncio
async def test_jebakan_hanya_pendapatan_tertunda_atau_ledger():
    from app.routers import items as IT
    assert await IT._item_jebakan_ubah_tipe(ConnImpor(), T, uuid.uuid4()) is None          # niat sah pemilik: boleh
    a = await IT._item_jebakan_ubah_tipe(ConnImpor(tunda="INV-2609-0009"), T, uuid.uuid4())
    assert "INV-2609-0009" in a and "Akui pendapatan baris non-stok" in a
    assert "ledger" in await IT._item_jebakan_ubah_tipe(ConnImpor(ledger=True), T, uuid.uuid4())


def test_impor_massal_memakai_penjaga_sempit():
    import inspect
    from app.routers import items as IT
    s = inspect.getsource(IT.bulk_import_items)
    assert "alasan = await _item_jebakan_ubah_tipe(conn, ctx[\"tenant_id\"], existing[\"id\"])" in s
    assert "_item_has_transactions" not in s                            # BUKAN blok umum (niat pemilik)
    assert "name, tipe_baru, item.get(\"unit\")," in " ".join(s.split())
