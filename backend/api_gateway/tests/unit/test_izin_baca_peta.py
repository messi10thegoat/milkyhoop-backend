"""izin-baca-peta (25 Nov 2026): 8 rute BACA yang dulu tak terpeta (tertutup untuk selain owner) kini punya izin modulnya;
GET /api/payments TETAP tertutup (gabungan pembayaran jual+beli, satu modul tak cukup); /items/{id}/transactions dihapus."""
import pytest
from pathlib import Path
from app.middleware.permission_middleware import izin_untuk_jalur

ID = "11111111-1111-1111-1111-111111111111"
PETA = [
    ("/api/budgets", "reports"), ("/api/budgets/variance-alerts", "reports"), (f"/api/budgets/{ID}", "reports"),
    (f"/api/budgets/{ID}/summary", "reports"), (f"/api/budgets/{ID}/vs-actual", "reports"),
    ("/api/items/impor/ekspor", "item"), (f"/api/bills/{ID}/deposit-plan", "purchase_invoice"),
]


@pytest.mark.parametrize("path,modul", PETA)
def test_baca_terpeta_huruf_R_modul_benar(path, modul):
    assert izin_untuk_jalur(path, "GET") == (modul, "R")


def test_payments_gabungan_tetap_tertutup():
    assert izin_untuk_jalur("/api/payments", "GET") is None


def test_tulis_anggaran_tak_bergeser():
    assert izin_untuk_jalur("/api/budgets", "POST") == ("reports", "C")
    assert izin_untuk_jalur(f"/api/budgets/{ID}", "DELETE") == ("reports", "D")


def test_transactions_barang_dihapus():
    src = (Path(__file__).parents[2] / "app" / "routers" / "items.py").read_text()
    assert "/transactions" not in src and "get_item_transactions" not in src
    assert "ItemTransaction" not in (Path(__file__).parents[2] / "app" / "schemas" / "items.py").read_text()
