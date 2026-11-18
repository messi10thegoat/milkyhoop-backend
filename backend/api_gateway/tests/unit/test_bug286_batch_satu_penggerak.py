"""BUG-286: saldo batch_warehouse_stock digeser SATU kali -- hanya trigger trg_update_batch_stock (INSERT inventory_ledger ber-batch_id + warehouse_id). Kode tak boleh menggeser lagi:
faktur pembelian (bills_service), faktur penjualan (_execute_fulfillment), pembalik void (record_inventory_reversal). Paritas nyata = uji salinan (scripts/ops/uji-ukur-bug286.sh, TEGAS=1)."""
import asyncio
import inspect
import re
import uuid
from datetime import date
from decimal import Decimal as D

from app.routers import sales_invoices as SI
from app.services import bills_service as BS
from app.services import inventory_helpers as IH


def tanpa_komentar(src: str) -> str:
    return "\n".join(re.sub(r"\s#.*$", "", ln) for ln in src.splitlines() if not ln.strip().startswith("#"))


def test_bills_service_tak_menyentuh_saldo_batch_manual():
    src = tanpa_komentar(inspect.getsource(BS))
    assert "batch_warehouse_stock" not in src


def test_faktur_jual_tak_mengurangi_batch_manual_dan_cek_sebelum_ledger():
    src = tanpa_komentar(inspect.getsource(SI._execute_fulfillment))
    assert "UPDATE batch_warehouse_stock" not in src
    assert "SELECT available_quantity FROM batch_warehouse_stock" in src and "FOR UPDATE" in src
    assert src.index("SELECT available_quantity FROM batch_warehouse_stock") < src.index("INSERT INTO inventory_ledger")   # cek kecukupan SEBELUM kartu stok (trigger menggeser sesudahnya)
    assert "tenant_id = $3 AND batch_id = $1" in src     # disaring tenant
    assert "Stok batch tidak cukup" in src


class Kon:
    def __init__(self):
        self.exe = []

    async def fetch(self, sql, *a):
        return [{"id": uuid.uuid4(), "product_id": uuid.uuid4(), "product_code": "P", "product_name": "Minyak", "quantity_in": D("10"), "quantity_out": D("0"),
                 "unit_cost": D("1000"), "total_cost": D("10000"), "warehouse_id": uuid.uuid4(), "source_number": "BILL-1", "batch_id": uuid.uuid4()}]

    async def execute(self, sql, *a):
        self.exe.append(sql)
        return "UPDATE 1"


def pembalik(monkeypatch, **kw):
    async def td(conn, tid): return date.today()

    async def sdn(conn, tid, pid): return D("10"), D("10000")

    async def wac(conn, tid, pid, a, b, uc, x): return D("1000")
    monkeypatch.setattr(IH, "tanggal_dokumen", td)
    monkeypatch.setattr(IH, "saldo_dan_nilai", sdn)
    monkeypatch.setattr(IH, "snapshot_wac_sesudah", wac)
    k = Kon()
    asyncio.run(IH.record_inventory_reversal(k, "t", "BILL", uuid.uuid4(), uuid.uuid4(), **kw))
    return k.exe


def test_pembalik_bawaan_tak_menggeser_saldo_batch_tapi_menandai_habis(monkeypatch):
    exe = pembalik(monkeypatch)
    assert not any("UPDATE batch_warehouse_stock" in s for s in exe)
    assert any("INSERT INTO inventory_ledger" in s for s in exe)             # baris pembalik (+ trigger DB) tetap ditulis
    assert any("UPDATE item_batches SET status = 'depleted'" in s for s in exe)


def test_pembalik_pindah_batch_true_eksplisit_masih_bisa_menggeser(monkeypatch):
    exe = pembalik(monkeypatch, pindah_batch=True)
    assert any("UPDATE batch_warehouse_stock" in s for s in exe) and any("UPDATE item_batches SET status = 'depleted'" in s for s in exe)


def test_semua_pemanggil_pembalik_memakai_bawaan_satu_penggerak():
    import pathlib
    app = pathlib.Path(__file__).resolve().parents[2] / "app"
    for f in app.rglob("*.py"):
        t = f.read_text()
        for m in re.finditer(r"record_inventory_reversal\((.*?)\)\s*\n", t, re.S):
            assert "pindah_batch=True" not in m.group(1), f
