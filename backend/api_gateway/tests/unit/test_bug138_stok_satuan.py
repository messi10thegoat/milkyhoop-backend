"""BUG-138 (8 Nov 2026, kontrak ADITIF WORKSPACE): stock[] pratinjau NK (nota_kredit_rencana.stok_bentuk_nd) dan ND
(nota_debit_rencana.stok_retur) membawa medan "unit" = satuan dari `quantity` itu (satuan DASAR stok, mis. "Botol"), di pratinjau aksi
DAN buat. Sumber satu: satuan_dasar (SD.muat/hitung -> satuan_dasar), juga bila baris dokumen tanpa satuan. Nilai lain TIDAK berubah."""
import uuid
from decimal import Decimal

import pytest

from app.services import nota_debit_gudang as NG
from app.services import nota_debit_rencana as NDR
from app.services import nota_kredit_rencana as NKR

from .test_qty_dasar import Bl, KonND, Kon, P_MINYAK, T


class KonNK(Kon):
    """_rencana_restock_nk tanpa faktur asal: WAC 5000, gudang ada, akun barang terisi."""
    async def fetchval(self, sql, *a):
        if "get_weighted_average_cost" in sql:
            return Decimal("5000")
        if "FROM warehouses" in sql:
            return "G1"
        return None

    async def fetchrow(self, sql, *a):
        if "cogs_account_id" in sql:
            return {"cogs_account_id": "C", "inventory_account_id": "I"}
        if "FROM products" in sql:
            return {"id": P_MINYAK, "item_code": "MG1", "nama_produk": "Minyak Goreng 1L", "track_inventory": True}
        return None


async def _restock(unit, qty):
    cn = {"id": uuid.uuid4(), "reason": "return", "original_invoice_id": None}
    return await NKR._rencana_restock_nk(KonNK(), {"tenant_id": T}, cn, Bl(), items=[{"item_id": P_MINYAK, "quantity": qty, "unit": unit}])


@pytest.mark.asyncio
@pytest.mark.parametrize("unit, qty, harap_qty", [(None, 2, 2), ("", 2, 2), ("btl", 2, 2), ("Karton", 1, 12)])
async def test_nk_restock_membawa_satuan_dasar_juga_tanpa_satuan_baris(unit, qty, harap_qty):
    out = await _restock(unit, qty)
    assert [(x["qty"], x["satuan"]) for x in out] == [(Decimal(harap_qty), "Botol")]


@pytest.mark.asyncio
async def test_nk_stock_bentuk_nd_unit_aditif_nilai_lain_tetap():
    out = await _restock(None, 2)
    assert NKR.stok_bentuk_nd(out) == [{"product_id": str(P_MINYAK), "product": "Minyak Goreng 1L", "quantity": 2.0,
                                        "unit_cost": 5000.0, "total_cost": 10000.0, "unit": "Botol"}]


def test_nk_alias_aksi_dan_buat_membawa_unit():
    r = [{"product_id": P_MINYAK, "nama": "M", "qty": Decimal("3"), "unit_cost": Decimal("2"), "line_cost": Decimal("6"), "satuan": "Botol"}]
    assert NKR.alias_aksi_nd({}, r)["stock"][0]["unit"] == "Botol"
    stok = NKR.stok_bentuk_nd(r)
    d = {"invoice": None, "can_save": True, "gross_subtotal": 0, "doc_discount": 0, "journals_on_post": []}
    assert NKR.alias_buat_nd(d, stok)["stock"][0]["unit"] == "Botol"
    assert NKR.stok_bentuk_nd([{**r[0], "satuan": None}])[0]["unit"] is None  # barang tanpa satuan dasar -> FE angka saja


@pytest.mark.asyncio
@pytest.mark.parametrize("unit, harap_qty", [(None, 1), ("botol", 1), ("Karton", 12)])
async def test_nd_keluar_membawa_satuan_dasar_juga_tanpa_satuan_baris(unit, harap_qty):
    bl = Bl()
    k = await NG.rencana_keluar(KonND(stok=100), T, {"original_bill_id": None, "id": None},
                                [{"item_id": P_MINYAK, "quantity": 1, "unit": unit}], False, bl)
    assert bl.isi == [] and [(x["qty"], x["satuan"]) for x in k] == [(Decimal(harap_qty), "Botol")]


def test_nd_stok_retur_unit_aditif_nilai_lain_tetap():
    b = {"produk": {"id": P_MINYAK, "nama_produk": "Minyak Goreng 1L"}, "qty": Decimal("12"), "unit_cost": Decimal("1500"),
         "total": Decimal("18000"), "cost_source": "wac", "gudang_id": "G1", "gudang": "Gudang Utama", "satuan": "Botol"}
    assert NDR.stok_retur({"baris": [b]}) == [{"product_id": str(P_MINYAK), "product": "Minyak Goreng 1L", "quantity": 12.0,
                                               "unit_cost": 1500.0, "total_cost": 18000.0, "cost_source": "wac",
                                               "warehouse_id": "G1", "warehouse": "Gudang Utama", "unit": "Botol"}]
    assert NDR.stok_retur(None) == []
