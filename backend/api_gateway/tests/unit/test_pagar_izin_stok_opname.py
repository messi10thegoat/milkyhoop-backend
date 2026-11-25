"""S4 pagar izin Stok Opname (25 Nov 2026), SEBELUM flag stok_opname_v1 menyala: tiap rute router stock_opnames punya izin yang BENAR (modul stock_adjust),
pratinjau = huruf SAMA dengan penulisnya, dan rute BARU tanpa peta = MERAH (bukan jatuh diam-diam ke huruf lain)."""
import re
from pathlib import Path

import pytest

from app.middleware.permission_middleware import izin_untuk_jalur

ID = "11111111-1111-1111-1111-111111111111"
MODUL = "stock_adjust"
# (metode, jalur sesudah /api/stock-opnames, huruf)
PETA = [
    ("GET", "", "R"), ("GET", f"/{ID}", "R"),
    ("POST", "", "C"), ("POST", "/preview", "C"),
    ("PUT", f"/{ID}/baris", "U"),
    ("POST", f"/{ID}/mulai", "U"), ("POST", f"/{ID}/mulai/preview", "U"),
    ("POST", f"/{ID}/batal", "U"), ("POST", f"/{ID}/batal/preview", "U"),
    ("POST", f"/{ID}/post", "P"), ("POST", f"/{ID}/post/preview", "P"),
    ("POST", f"/{ID}/void", "V"), ("POST", f"/{ID}/void/preview", "V"),
    ("DELETE", f"/{ID}", "D"),
]


@pytest.mark.parametrize("metode,jalur,huruf", PETA)
def test_izin_tiap_aksi_opname(metode, jalur, huruf):
    assert izin_untuk_jalur("/api/stock-opnames" + jalur, metode) == (MODUL, huruf)


def test_pratinjau_sama_huruf_dengan_penulis():
    for aksi in ("mulai", "batal", "post", "void"):
        assert izin_untuk_jalur(f"/api/stock-opnames/{ID}/{aksi}/preview", "POST") == izin_untuk_jalur(f"/api/stock-opnames/{ID}/{aksi}", "POST")
    assert izin_untuk_jalur("/api/stock-opnames/preview", "POST") == izin_untuk_jalur("/api/stock-opnames", "POST")


def test_posting_dan_void_tak_jatuh_ke_huruf_buat_atau_ubah():
    assert izin_untuk_jalur(f"/api/stock-opnames/{ID}/post", "POST")[1] == "P" != izin_untuk_jalur("/api/stock-opnames", "POST")[1]
    assert izin_untuk_jalur(f"/api/stock-opnames/{ID}/void", "POST")[1] == "V"


def test_setiap_rute_router_ada_di_peta_ini():
    """Rute baru di routers/stock_opnames.py yang belum didaftarkan di PETA di atas = MERAH (paksa putusan huruf izin sebelum rilis)."""
    src = (Path(__file__).parents[2] / "app" / "routers" / "stock_opnames.py").read_text()
    rute = set()
    for m in re.finditer(r'@router\.(get|post|put|patch|delete)\("([^"]*)"', src):
        metode, jalur = m.group(1).upper(), m.group(2).replace("{opname_id}", ID)
        rute.add((metode, jalur))
    ada = {(mt, j) for mt, j, _ in PETA}
    assert rute <= ada, f"rute opname tanpa peta izin di tes: {sorted(rute - ada)}"
    for mt, j in rute:
        assert izin_untuk_jalur("/api/stock-opnames" + j, mt) is not None
