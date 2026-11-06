"""Law 25 (6 Nov 2026): jurnal HPP keluar barang (record_inventory_outbound, dipakai posting nota debit) + pratinjau ND dulu int(qty x biaya)
-> sen DIPOTONG (3 x 33,33 = 99 bukan 99,99) -> jurnal HPP < nilai kartu stok -> HC15 bergeser. Kini r2 HALF_UP, pratinjau == penulis."""
import asyncio
import os
from decimal import Decimal as D
from uuid import uuid4

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

from app.services import inventory_helpers as IH  # noqa: E402
from app.services import nota_debit_rencana as NR  # noqa: E402


class _K:
    def __init__(self):
        self.exec = []

    async def fetchval(self, sql, *a):
        if "SUM(quantity_in - quantity_out)" in sql:
            return D(10)
        if "get_weighted_average_cost" in sql:
            return D("33.33")
        return 1

    async def fetchrow(self, sql, *a):
        return {"id": a[0] if a else None, "item_code": "X", "nama_produk": "X", "track_inventory": True}

    async def execute(self, sql, *a):
        self.exec.append((sql, a))


async def _biaya(conn, tid, pid):
    return {"unit_cost": D("33.33"), "cost_source": "wac"}


async def _akun(conn, tid, pid):
    return {"cogs_account_id": "HPP", "inventory_account_id": "PERS"}


def test_record_inventory_outbound_jurnal_hpp_bersen_sama_dengan_kartu(monkeypatch):
    monkeypatch.setattr(IH, "get_cost_for_sale", _biaya)
    monkeypatch.setattr(IH, "resolve_inventory_accounts", _akun)
    k = _K()
    r = asyncio.run(IH.record_inventory_outbound(k, "t", uuid4(), "X", "X", uuid4(), 3, "VENDOR_CREDIT", uuid4(), "VC-1", None, "uji",
                                                 receipt_date=__import__("datetime").date(2026, 11, 6)))
    assert r["total_cost"] == D("99.99")                       # dulu int() -> 99
    jl = [a for s, a in k.exec if "INSERT INTO journal_lines" in s][0]
    assert D("99.99") in jl                                     # Dr HPP / Cr Persediaan = nilai kartu stok (3 x 33,33)


def test_pratinjau_nd_hpp_bersen_sama_dengan_penulis(monkeypatch):
    import app.services.inventory_helpers as IH2
    monkeypatch.setattr(IH2, "get_cost_for_sale", _biaya)
    monkeypatch.setattr(IH2, "resolve_inventory_accounts", _akun)

    async def jurnal(conn, tid, st, judul, tgl, baris):
        return {"source_type": st, "lines": baris}

    monkeypatch.setattr(NR, "_rencana_jurnal_nd", jurnal)
    vc = {"credit_number": "VC-1", "credit_date": None}
    j, stok = asyncio.run(NR.rencana_hpp(_K(), "t", vc, [{"product_id": uuid4(), "qty": D(3), "gudang_id": uuid4(), "gudang": "G"}]))
    assert stok[0]["total_cost"] == 99.99 and j[0]["lines"][0][1] == D("99.99") and j[0]["lines"][1][2] == D("99.99")
