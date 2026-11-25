"""Unit 0 FIFO (25 Nov 2026): SATU penulis inventory_ledger; nilai yang ditulis == sebelum refaktor (peta kolom beku per penulis)."""
import ast
import asyncio
import pathlib
import re
import uuid
from decimal import Decimal as D

from app.services import ledger_kartu as LK

APP = pathlib.Path(__file__).resolve().parents[2] / "app"
BEKU = pathlib.Path(__file__).parent / "ledger_kartu_pemetaan.txt"
KOLOM = ("id", "tenant_id", "product_id", "product_code", "product_name", "movement_type", "movement_date", "source_type", "source_id", "source_number", "quantity_in",
         "quantity_out", "quantity_balance", "unit_cost", "total_cost", "average_cost", "warehouse_id", "journal_id", "created_by", "notes", "batch_id",
         "storage_location_id", "transaction_unit", "transaction_quantity", "conversion_factor")


def _tulis(**kw):
    class K:
        async def execute(self, sql, *a):
            self.p = (sql, a)
    k = K()
    b = dict(tenant_id="t", product_id="p", product_code="c", product_name="n", movement_type="SALE", movement_date="d", source_type="S", source_id="s", quantity_in=0,
             quantity_out=2, quantity_balance=3, unit_cost=D("5"), total_cost=D("10"), average_cost=D("6"), warehouse_id="w")
    b.update(kw)
    hasil = asyncio.run(LK.tulis_kartu(k, **b))
    sql, a = k.p
    m = re.search(r"\(([^)]*)\) VALUES \((.*)\)$", sql, re.S)
    kol, val = [x.strip() for x in m[1].split(",")], [x.strip() for x in m[2].split(", ")]
    assert len(kol) == len(val) == len(a) == 26 and all(re.search(rf"\${i}\b", v) for i, v in enumerate(val, 1))
    return hasil, dict(zip(kol, a))


def test_setiap_kolom_mendapat_nilainya_sendiri():
    b = {k: f"<{k}>" for k in KOLOM}
    hasil, isi = _tulis(**b)
    assert hasil == "<id>" and {k: v for k, v in isi.items() if k != "created_at"} == b


def test_bawaan_sama_dengan_bawaan_kolom():
    hasil, isi = _tulis()
    assert isinstance(hasil, uuid.UUID) and isi["id"] == hasil and isi["transaction_quantity"] == 0 and isi["created_at"] is False
    for k in ("source_number", "journal_id", "created_by", "notes", "batch_id", "storage_location_id", "transaction_unit", "conversion_factor"):
        assert isi[k] is None
    assert _tulis(transaction_quantity=None)[1]["transaction_quantity"] is None and _tulis(jam_nyata=True)[1]["created_at"] is True
    assert "THEN clock_timestamp() ELSE now()" in LK.SQL


def test_satu_penulis():
    pola = re.compile(r"INSERT\s+INTO\s+(public\.)?inventory_ledger\b", re.I)
    assert [str(p.relative_to(APP)) for p in APP.rglob("*.py") if pola.search(p.read_text())] == ["services/ledger_kartu.py"]
    assert pola.search("insert into public.inventory_ledger (id)")   # penjaga bisa merah


def _panggilan():
    out = []
    for p in sorted(APP.rglob("*.py")):
        src = p.read_text()
        if "tulis_kartu(" not in src or p.name == "ledger_kartu.py":
            continue
        for f in ast.walk(ast.parse(src)):
            if isinstance(f, (ast.AsyncFunctionDef, ast.FunctionDef)):
                for c in ast.walk(f):
                    if isinstance(c, ast.Call) and getattr(c.func, "id", None) == "tulis_kartu":
                        out.append(f"{p.stem}.{f.name} :: " + " | ".join(sorted(f"{k.arg or '**'}={ast.unparse(k.value)}" for k in c.keywords)))
    return sorted(out)


def test_pemetaan_kolom_beku_per_penulis():
    """Mengubah nilai yang ditulis = mengubah baris beku ini dengan sengaja (banding kode lama 949e241cd: 17 == 17)."""
    assert _panggilan() == BEKU.read_text().splitlines()
