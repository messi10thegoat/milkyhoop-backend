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
# B1a (28 Okt 2026): Pembelian = ekspor + PDF massal saja (tanpa bagikan)
MODUL_BELI = ["purchase-orders", "goods-receipts", "bills", "bill-payments", "vendor-deposits", "vendor-credits"]
for _m in MODUL_BELI:
    AKSI[_m] = ["export", "pdf"]
# B1b-1 (28 Okt 2026): PO kirim + hapus draf + batal massal (POST /{po_id}/send|cancel|delete/preview menelan bila lebih dulu)
AKSI["purchase-orders"] += ["send", "send/preview", "delete", "delete/preview", "cancel", "cancel/preview"]
MODUL = MODUL + MODUL_BELI


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
    assert sum(len(AKSI[m]) for m in MODUL) == 60  # kontrol positif: semua jalur terhitung (42 Penjualan + 12 Pembelian B1a + 6 PO B1b-1)


def test_kontrol_penjaga_bisa_merah_rute_id_memang_menelan_bulk_bila_lebih_dulu():
    """Kontrol alat: pencocokan memang menangkap rute {id}. Kunci '/bulk/' sengaja TIDAK ada pada rute tunggal; cocokkan jalur
    tunggal sungguhan untuk membuktikan penjaga ini memakai pencocokan yang sama dengan produksi."""
    r = pemenang("/api/sales-orders/ABC/confirm")
    assert r is not None and r.path == "/api/sales-orders/{order_id}/confirm"
    r2 = pemenang("/api/proformas/ABC/cancel")
    assert r2 is not None and r2.path == "/api/proformas/{proforma_id}/cancel"


def test_kontrol_rute_po_tunggal_memang_menelan_bulk_po_bila_lebih_dulu():
    """B1b-1 SABOTASE di dalam tes: app kecil dengan router PO DIDAFTARKAN LEBIH DULU daripada bulk_po -> rute {po_id} menang untuk
    /bulk/send, /bulk/cancel, /bulk/send|cancel|delete/preview (penjaga di atas pasti merah bila main.py membalik urutan). Urutan
    main.py yang benar (bulk_po dulu) -> bulk menang."""
    from fastapi import FastAPI
    from app.routers import bulk_po, purchase_orders

    def menang(a, path):
        scope = {"type": "http", "method": "POST", "path": path, "root_path": "", "headers": []}
        for r in a.routes:
            if r.matches(scope)[0] == Match.FULL:
                return r
        return None

    salah = FastAPI()
    salah.include_router(purchase_orders.router, prefix="/api/purchase-orders")
    salah.include_router(bulk_po.router, prefix="/api/purchase-orders")
    benar = FastAPI()
    benar.include_router(bulk_po.router, prefix="/api/purchase-orders")
    benar.include_router(purchase_orders.router, prefix="/api/purchase-orders")
    ditelan = ["send", "cancel", "send/preview", "cancel/preview", "delete/preview"]
    for a in ditelan:
        r = menang(salah, f"/api/purchase-orders/bulk/{a}")
        assert r is not None and "{po_id}" in r.path, (a, getattr(r, "path", None))
    for a in AKSI["purchase-orders"][2:]:
        r = menang(benar, f"/api/purchase-orders/bulk/{a}")
        assert r is not None and r.endpoint.__module__.endswith("bulk_po"), a
