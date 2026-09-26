"""P4 (26 Sep 2026): /fulfill menolak baris faktur yang nilainya SUDAH habis dikreditkan nota kredit tertunda.

Sejak cn312 (V312) NK atas pendapatan tertunda menurunkan sales_invoice_items.allocated_amount. Baris yang
dikreditkan penuh (allocated - recognized = 0, fulfilled_qty masih 0) dulu tetap bisa dikirim: HPP + stok keluar
dengan pendapatan 0. Kini 409 FULFILL_LINE_FULLY_CREDITED menyebut nomor NK. Pembeda dari baris harga-nol (bonus):
harus ada porsi NK AKTIF (credit_note_deferral_lines.reversed_at IS NULL) di baris itu.
Tes memanggil _execute_fulfillment (inti yang dipakai /fulfill DAN kirim otomatis) dengan koneksi tiruan;
"lolos penjaga" dibuktikan dengan maju ke cek stok (409 'Stok ... tidak cukup').
"""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.routers import sales_invoices as SI

T = "kaos-biru-konveksi"
INV = uuid.uuid4()
BARIS = uuid.uuid4()
WH = uuid.uuid4()


class Conn:
    def __init__(self, allocated, recognized="0", nk=("NK-2609-0001",), fulfilled="0"):
        self.row = {"id": BARIS, "item_id": uuid.uuid4(), "description": "Kaos Biru L", "quantity": Decimal("10"),
                    "fulfilled_qty": Decimal(fulfilled), "allocated_amount": Decimal(allocated),
                    "recognized_amount": Decimal(recognized)}
        self.nk, self.q = nk, []

    async def fetchrow(self, sql, *a):
        self.q.append((sql, a))
        if "FROM sales_invoice_items" in sql:
            return self.row
        raise AssertionError(sql[:60])

    async def fetch(self, sql, *a):
        self.q.append((sql, a))
        if "credit_note_deferral_lines" in sql:
            return [{"credit_note_number": n} for n in self.nk]
        raise AssertionError(sql[:60])

    async def fetchval(self, sql, *a):
        self.q.append((sql, a))
        if "FROM warehouse_stock" in sql:
            return Decimal("0")   # stok nol -> bukti MAJU melewati penjaga P4
        raise AssertionError(sql[:60])

    async def execute(self, sql, *a):
        self.q.append((sql, a))


@pytest.fixture(autouse=True)
def _tgl(monkeypatch):
    async def t(c, tid):
        return date(2026, 9, 26)
    monkeypatch.setattr(SI, "tanggal_dokumen", t)


async def _kirim(conn, qty=1):
    inv = {"id": INV, "invoice_number": "INV-2609-0100"}
    return await SI._execute_fulfillment(conn, T, "u1", inv, [{"invoice_item_id": BARIS, "quantity": qty}],
                                         WH, date(2026, 9, 26))


@pytest.mark.asyncio
async def test_baris_dikreditkan_penuh_ditolak_menyebut_nk():
    c = Conn(allocated="0")
    with pytest.raises(HTTPException) as e:
        await _kirim(c)
    assert e.value.status_code == 409
    d = e.value.detail
    assert d["code"] == "FULFILL_LINE_FULLY_CREDITED" and d["credit_notes"] == ["NK-2609-0001"]
    assert "NK-2609-0001" in d["message"] and "Kaos Biru L" in d["message"] and d["invoice_item_id"] == str(BARIS)
    assert not any("warehouse_stock" in s for s, _ in c.q), "penolakan harus SEBELUM cek stok/HPP (nol tulisan)"
    assert not any(s.lstrip().upper().startswith(("INSERT", "UPDATE")) for s, _ in c.q)


@pytest.mark.asyncio
async def test_sisa_pembulatan_sen_dianggap_habis():
    with pytest.raises(HTTPException) as e:
        await _kirim(Conn(allocated="100.004", recognized="100"))
    assert e.value.detail["code"] == "FULFILL_LINE_FULLY_CREDITED"


@pytest.mark.asyncio
async def test_baris_harga_nol_tanpa_nk_tetap_boleh():
    c = Conn(allocated="0", nk=())
    with pytest.raises(HTTPException) as e:
        await _kirim(c)
    assert e.value.status_code == 409 and "Stok" in str(e.value.detail)   # maju ke cek stok


@pytest.mark.asyncio
async def test_nk_sebagian_masih_ada_nilai_tak_ditanya():
    c = Conn(allocated="400000", recognized="0")
    with pytest.raises(HTTPException) as e:
        await _kirim(c)
    assert "Stok" in str(e.value.detail)
    assert not any("credit_note_deferral_lines" in s for s, _ in c.q)


@pytest.mark.asyncio
async def test_kueri_porsi_bertenant_dan_hanya_aktif():
    c = Conn(allocated="0")
    with pytest.raises(HTTPException):
        await _kirim(c)
    sql, a = [(s, a) for s, a in c.q if "credit_note_deferral_lines" in s][0]
    s = " ".join(sql.split())
    assert "d.tenant_id = $2" in s and "c.tenant_id = d.tenant_id" in s and "d.reversed_at IS NULL" in s
    assert "d.invoice_item_id = $1" in s and a == (BARIS, T)
