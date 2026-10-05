"""PENJAGA urutan rute bulk (5 Okt 2026, insiden U1b): rute bulk F1-F5 diuji lewat TABEL RUTE app.main.app (rute PERTAMA yang cocok, seperti
FastAPI/Starlette), BUKAN dengan memanggil handler. Cacat yang lolos: "/api/sales-orders/{order_id}/confirm" didaftarkan lebih dulu dan
menelan "/api/sales-orders/bulk/confirm" (id = "bulk") -> 404 "Pesanan penjualan tidak ditemukan." untuk SEMUA permintaan massal; tes
handler langsung (dan harness nyata yang memanggil handler) tak pernah melihatnya."""
import os

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")  # impor app.main butuh satu penyedia LLM (pola test_f4_klaim_perangkat)

from starlette.routing import Match

from app.main import app

MODUL = ["sales-orders", "quotes", "proformas", "sales-invoices", "customer-deposits", "receive-payments", "deliveries", "credit-notes"]
AKSI = {"sales-orders": ["export", "pdf", "share", "share/preview", "confirm", "confirm/preview", "delete", "delete/preview"],
        "quotes": ["export", "pdf", "share", "share/preview", "send", "send/preview", "delete", "delete/preview"],
        "proformas": ["export", "pdf", "share", "share/preview", "cancel", "cancel/preview"]}
for _m in MODUL:
    AKSI.setdefault(_m, ["export", "pdf", "share", "share/preview"])


def pemenang(path: str, metode: str = "POST"):
    scope = {"type": "http", "method": metode, "path": path, "root_path": "", "headers": []}
    for r in app.routes:
        match, _ = r.matches(scope)
        if match == Match.FULL:
            return r
    return None


def _nama_modul(r) -> str:
    return getattr(getattr(r, "endpoint", None), "__module__", "") or ""


def test_semua_rute_bulk_dimenangkan_router_bulk_bukan_rute_id():
    salah = []
    for m in MODUL:
        for a in AKSI[m]:
            path = f"/api/{m}/bulk/{a}"
            r = pemenang(path)
            mod = _nama_modul(r) if r else "TAK ADA"
            if r is None or not mod.rsplit(".", 1)[-1].startswith("bulk"):
                salah.append((path, getattr(r, "path", None), mod))
    assert not salah, f"rute bulk dikalahkan rute lain: {salah}"
    assert sum(len(AKSI[m]) for m in MODUL) == 42  # kontrol positif: semua jalur terhitung


def test_kontrol_penjaga_bisa_merah_rute_id_memang_menelan_bulk_bila_lebih_dulu():
    """Kontrol alat: pencocokan memang menangkap rute {id}. Kunci '/bulk/' sengaja TIDAK ada pada rute tunggal; cocokkan jalur
    tunggal sungguhan untuk membuktikan penjaga ini memakai pencocokan yang sama dengan produksi."""
    r = pemenang("/api/sales-orders/ABC/confirm")
    assert r is not None and r.path == "/api/sales-orders/{order_id}/confirm"
    r2 = pemenang("/api/proformas/ABC/cancel")
    assert r2 is not None and r2.path == "/api/proformas/{proforma_id}/cancel"
