"""KARAKTERISASI penentu debit FPB tagihan_aksi.hitung_debit (jalur draf->posting) per jenis baris -- WAJIB hijau di master SEBELUM dan
SESUDAH FPB-DEBIT-PENUH (30 Okt 2026): jalur buat-langsung-posted kini memanggil penentu yang sama, jadi perilaku jalur draf tak boleh
bergeser. Non-stok & tanpa produk -> COGS_SALES; jasa -> COGS_SERVICE; purchase_account_id produk dihormati; barang stok -> Persediaan."""
import asyncio
from decimal import Decimal
from uuid import uuid4

import pytest

from app.services import tagihan_aksi as TA

S, J, INV, OV = uuid4(), uuid4(), uuid4(), uuid4()


class _KH:
    def __init__(s, rows):
        s.rows = rows

    async def fetchval(s, sql, *a):
        if "FROM account_roles" in sql:
            return {"COGS_SALES": S, "COGS_SERVICE": J}.get(a[1])
        return None

    async def fetch(s, sql, *a):
        return s.rows


def _baris(item_type, track=False, pid=True, ov=None, net="100"):
    return {"id": uuid4(), "product_id": uuid4() if pid else None, "net": Decimal(net), "item_type": item_type, "track_inventory": track,
            "purchase_account_id": ov}


@pytest.mark.parametrize("baris,akun", [
    (_baris("non_inventory"), "S"), (_baris("service"), "J"), (_baris(None, pid=False), "S"), (_baris("non_inventory", ov=OV), "OV"),
])
def test_karakterisasi_hitung_debit_per_jenis(baris, akun):
    out = asyncio.run(TA.hitung_debit(_KH([baris]), "t", uuid4(), Decimal("100"), INV))
    assert out == [({"S": S, "J": J, "OV": OV}[akun], Decimal("100"), False)]


def test_karakterisasi_hitung_debit_barang_stok(monkeypatch):
    async def akun(conn, tid, pid): return {"inventory_account_id": INV, "cogs_account_id": S}
    import app.services.inventory_helpers as IH
    monkeypatch.setattr(IH, "resolve_inventory_accounts", akun)
    out = asyncio.run(TA.hitung_debit(_KH([_baris("goods", track=True)]), "t", uuid4(), Decimal("100"), INV))
    assert out == [(INV, Decimal("100"), True)]
