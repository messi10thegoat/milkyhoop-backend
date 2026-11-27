"""G-hapus Gudang (KONTRAK-G-hapus-gudang-20261126): hapus permanen hanya gudang yang belum pernah dipakai (Accurate). JEJAK_GUDANG = SEMUA FK ke
warehouses (pg_constraint prod 26 Nov), penulis & pratinjau memakai penentu SAMA, galat FK jadi 409 berkode, DELETE lama tetap = nonaktifkan."""
import asyncio
import re
from pathlib import Path

from app.middleware.permission_middleware import ROUTE_PERMISSIONS
from app.services import gudang_daftar as GD
from app.services import gudang_hapus as GH

# FK ke warehouses di DB live (SELECT conrelid, attname FROM pg_constraint WHERE confrelid='warehouses'::regclass; 26 Nov 2026), tanpa sales_orders.tenant_id
# (bagian FK komposit). FK baru ke warehouses -> tambahkan di sini DAN di JEJAK_GUDANG / PENANDA_UTAMA.
FK_LIVE = {
    ("batch_warehouse_stock", "warehouse_id"), ("bills", "warehouse_id"), ("branches", "default_warehouse_id"), ("fixed_assets", "warehouse_id"),
    ("goods_receipts", "warehouse_id"), ("inventory_ledger", "warehouse_id"), ("invoice_fulfillments", "warehouse_id"), ("item_serials", "warehouse_id"),
    ("pengiriman_preferensi", "default_lokasi_kirim_id"), ("production_completions", "warehouse_id"), ("production_order_materials", "warehouse_id"),
    ("production_orders", "warehouse_id"), ("purchase_orders", "warehouse_id"), ("sales_invoices", "warehouse_id"), ("sales_orders", "fase_gudang_id"),
    ("sales_orders", "warehouse_id"), ("sales_receipts", "warehouse_id"), ("serial_movements", "from_warehouse_id"), ("serial_movements", "to_warehouse_id"),
    ("stock_adjustment_items", "warehouse_id"), ("stock_adjustments", "warehouse_id"), ("stock_opnames", "warehouse_id"), ("stock_transfers", "from_warehouse_id"),
    ("stock_transfers", "to_warehouse_id"), ("stok_minimum_gudang", "warehouse_id"), ("warehouse_bins", "warehouse_id"), ("warehouse_stock", "warehouse_id"),
    ("work_centers", "warehouse_id"), ("user_warehouse_access", "warehouse_id"),   # V516 (PARITAS-4b)
}
MIG = Path(__file__).parents[3] / "migrations"
ROUTER = (Path(__file__).parents[2] / "app" / "routers" / "warehouses.py").read_text()
W = {"id": "w1", "name": "Gudang B", "code": "GDG-002", "is_system": False, "is_default": False}


def test_jejak_plus_penanda_utama_sama_dengan_fk_live():
    assert {(t, c) for t, c, _ in GH.JEJAK_GUDANG} | set(GH.PENANDA_UTAMA) == FK_LIVE


def test_migrasi_baru_berfk_ke_warehouses_wajib_terdaftar():
    """FK baru di migrasi (REFERENCES warehouses) sesudah V511 -> tabelnya wajib ada di FK_LIVE (lalu di JEJAK_GUDANG)."""
    tabel = {t for t, _ in FK_LIVE}
    for f in sorted(MIG.glob("V*.sql")):
        n = int(re.match(r"V(\d+)", f.name).group(1))
        if n <= 511 or "ROLLBACK" in f.name:
            continue
        s = f.read_text()
        for m in re.finditer(r"CREATE TABLE (?:IF NOT EXISTS )?(\w+)\s*\((.*?)\);", s, re.S | re.I):
            if re.search(r"REFERENCES\s+warehouses", m.group(2), re.I):
                assert m.group(1) in tabel, f"{f.name}: {m.group(1)} merujuk warehouses tapi belum di JEJAK_GUDANG"
        assert not re.search(r"ALTER TABLE.*REFERENCES\s+warehouses", s, re.I | re.S) or all(t in tabel for t in re.findall(r"ALTER TABLE (?:ONLY )?(\w+)", s)), f.name


def test_tiap_sumber_punya_teks_ramah():
    for t, _, _ in GH.JEJAK_GUDANG:
        assert t in GH.LABEL_JEJAK and "_" not in GH.label_jejak(t), t
    assert GH.label_jejak("tabel_baru") == "Transaksi lain"


def test_stok_gudang_nol_bukan_jejak():
    syarat = {t: s for t, _, s in GH.JEJAK_GUDANG}
    assert syarat["warehouse_stock"] == " AND quantity <> 0" and all(s == "" for t, s in syarat.items() if t != "warehouse_stock")


def test_urutan_blok_dan_kalimat():
    assert GH.rencana_hapus(None, False, [])["blocks"][0]["code"] == "GUDANG_TAK_ADA"
    r = GH.rencana_hapus({**W, "is_system": True, "name": "Transit"}, True, [("inventory_ledger", 3)])
    assert r["blocks"][0] == {"code": "GUDANG_SISTEM", "message": "Transit adalah gudang sistem dan tidak bisa dihapus.", "id": "w1"}
    for w, utama in (({**W, "is_default": True}, False), (W, True)):
        r = GH.rencana_hapus(w, utama, [("inventory_ledger", 3)])
        assert r["blocks"][0]["code"] == "GUDANG_UTAMA"
        assert r["blocks"][0]["message"] == "Gudang B adalah gudang utama, jadi tidak bisa dihapus. Pilih gudang utama lain dulu."
        assert r["rencana"]["jenis"] == "tak_bisa"


def test_berjejak_teks_satu_kalimat_gabung_label_dan_saran():
    r = GH.rencana_hapus(W, False, [("stock_transfers", 1), ("stock_transfers", 2), ("inventory_ledger", 4), ("bills", 1), ("warehouse_bins", 1), ("fixed_assets", 0)])
    rc = r["rencana"]
    assert not r["ok"] and rc["jenis"] == "tak_bisa" and rc["saran"] == "Nonaktifkan"
    assert rc["jejak"][0] == {"sumber": "stock_transfers", "sumber_text": "Pemindahan barang", "jumlah": 3}
    assert rc["teks"] == "Gudang B sudah dipakai di Pemindahan barang, Kartu stok, Faktur pembelian dan 1 lainnya, jadi tidak bisa dihapus. Gunakan Nonaktifkan."
    assert r["blocks"][0]["message"] == rc["teks"] and r["blocks"][0]["code"] == "GUDANG_BERJEJAK"


def test_bebas_hapus_permanen():
    r = GH.rencana_hapus(W, False, [(t, 0) for t, _, _ in GH.JEJAK_GUDANG])
    assert r["ok"] and r["blocks"] == [] and r["peringatan"] == []
    assert r["rencana"] == {"jenis": "hapus_permanen", "jejak": [], "saran": None,
                            "teks": "Gudang B belum pernah dipakai. Menghapus bersifat permanen dan tidak bisa dibatalkan."}
    assert GH.status_blok("GUDANG_TAK_ADA") == 404 and GH.status_blok("GUDANG_BERJEJAK") == 409


def test_sql_hitung_dari_daftar_yang_sama():
    for t, c, s in GH.JEJAK_GUDANG:
        assert f"SELECT 1 FROM {t} WHERE {c} = $1{s}" in GH.SQL_HITUNG_JEJAK


def _fn(nama):
    f = ROUTER[ROUTER.index(f"async def {nama}"):]
    return f[:f.index("\n\n\n")]


def test_penulis_penentu_sama_kunci_idem_fk_audit():
    fn = _fn("hapus_gudang")
    assert "GH.ukur_rencana(" in fn and "GH.tolak(r)" in fn
    assert 'f"WAREHOUSE:{ctx[\'tenant_id\']}:{warehouse_id}"' in fn and "mulai_aksi(" in fn
    assert "asyncpg.ForeignKeyViolationError" in fn and '"GUDANG_BERJEJAK"' in fn
    assert "DELETE FROM warehouses WHERE id = $1 AND tenant_id = $2" in fn and '"warehouse.delete"' in fn
    assert "GH.ukur_rencana(" in _fn("preview_hapus_gudang") and "readonly=True" in _fn("preview_hapus_gudang")


def test_delete_lama_tetap_nonaktifkan():
    fn = _fn("delete_warehouse")
    assert "GR.rencana_status(" in fn and "DELETE FROM" not in fn


def _izin(path):
    for pola, metode, modul, huruf in ROUTE_PERMISSIONS:
        if "POST" in metode and re.match(pola, path):
            return modul, huruf


def test_izin_rute_baru_sama_dengan_hapus():
    assert _izin("/api/warehouses/abc/delete") == ("warehouse", "D")
    assert _izin("/api/warehouses/abc/delete/preview") == ("warehouse", "D")


class _Conn:
    def __init__(self, fitur, utama=None, jejak=0):
        self.fitur, self.utama, self.jejak = fitur, utama, jejak

    async def fetch(self, sql, *a):
        assert "tenant_features" in sql
        return [{"feature": f} for f in self.fitur]

    async def fetchval(self, sql, *a):
        if sql == GH.SQL_HITUNG_JEJAK:
            return self.jejak
        return self.utama


def _aksi(conn, w):
    return asyncio.run(GD.aksi_tersedia(conn, "t1", {**W, "is_active": True, **w}))


def test_aksi_hapus_hanya_bila_flag_nyala():
    assert "hapus" not in _aksi(_Conn([]), {})
    assert _aksi(_Conn([GD.FLAG_HAPUS]), {})["hapus"]["tersedia"] is True
    assert _aksi(_Conn([GD.FLAG_HAPUS], jejak=2), {})["hapus"]["tersedia"] is False
    assert _aksi(_Conn([GD.FLAG_HAPUS]), {"is_default": True})["hapus"]["tersedia"] is False
    assert _aksi(_Conn([GD.FLAG_HAPUS], utama=1), {})["hapus"]["tersedia"] is False
