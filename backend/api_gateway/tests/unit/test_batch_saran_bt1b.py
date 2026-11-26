"""BT-1b: saran batch lintas gudang (FEFO/FIFO), baca saja."""
import sys, os
from datetime import date, datetime
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from app.services import batch_saran as BS

H = date(2026, 11, 27)


def r(n, exp, gud, qty, st="active", dibuat=1):
    return {"batch_id": f"b{n}", "batch_number": f"B{n}", "expiry_date": exp, "status": st,
            "created_at": datetime(2026, 1, dibuat), "warehouse_id": f"w{gud}", "warehouse_name": f"Gudang {gud}",
            "available_quantity": Decimal(qty)}


def test_fefo_lintas_gudang_urut_kedaluwarsa():
    rows = [r(1, date(2027, 3, 1), 1, 5), r(2, date(2027, 1, 1), 2, 4), r(3, None, 1, 100)]
    o = BS.susun(rows, 8, "FEFO", H, "pcs")
    assert [u["batch_no"] for u in o["urutan"]] == ["B2", "B1", "B3"]
    assert [str(u["saran_qty"]) for u in o["urutan"]] == ["4", "4", "0"]
    assert o["cukup"] and o["kurang_text"] is None
    assert o["urutan"][0]["saran_text"] == "4 pcs" and o["urutan"][0]["warehouse_name"] == "Gudang 2"


def test_fifo_urut_dibuat():
    rows = [r(1, date(2027, 1, 1), 1, 5, dibuat=5), r(2, date(2028, 1, 1), 1, 5, dibuat=2)]
    o = BS.susun(rows, 3, "FIFO", H, "pcs")
    assert [u["batch_no"] for u in o["urutan"]] == ["B2", "B1"]
    assert str(o["urutan"][0]["saran_qty"]) == "3"


def test_kedaluwarsa_tampil_tapi_tak_disarankan():
    rows = [r(1, date(2026, 1, 1), 1, 50), r(2, date(2027, 1, 1), 1, 3), r(3, date(2027, 2, 1), 1, 1, st="quarantine")]
    o = BS.susun(rows, 5, "FEFO", H, "pcs")
    b1 = [u for u in o["urutan"] if u["batch_no"] == "B1"][0]
    assert b1["kedaluwarsa"] and b1["saran_qty"] == 0 and b1["status_text"] == "Kedaluwarsa"
    assert not o["cukup"] and o["kurang_text"] == "Kurang 2 pcs"
    assert "mencukupi 3 pcs dari 5 pcs" in o["ringkas_text"]


def test_tanpa_batch_layak_dan_tanpa_batch_barang():
    o = BS.susun([r(1, date(2026, 1, 1), 1, 9)], 2, "FEFO", H, "pcs")
    assert not o["cukup"] and o["ringkas_text"].startswith("Tidak ada batch layak")
    t = BS.tanpa_batch(2, "pcs")
    assert t["urutan"] == [] and "tidak memakai batch" in t["ringkas_text"]


def test_desimal_dan_ringkas_cukup():
    o = BS.susun([r(1, None, 1, "2.5")], "1.5", "FEFO", H, "kg")
    assert o["urutan"][0]["saran_text"] == "1,5 kg" and o["ringkas_text"] == "1,5 kg dari 1 batch (FEFO)"
