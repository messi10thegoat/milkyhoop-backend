"""BUG-235/236 (FRONTEND 2 via MASTER, 14 Nov): TEKS dari SERVER untuk detail Barang -- history `jenis_text` (kosakata ledger, tak pernah kode mentah),
activity (deskripsi "Barang ..." + actor_name = nama tampil, email hanya cadangan), `position_text_ket` (subteks tanpa angka stok ganda). Aditif;
SATU kamus di services/barang_teks; tanpa migrasi."""
import pathlib
import re

import pytest

from app.services import barang_daftar as BD
from app.services import barang_teks as BT

APP = pathlib.Path(__file__).resolve().parents[2] / "app"
RUTE = (APP / "routers" / "items.py").read_text()

# kosakata ledger TERUKUR di klon (movement_type, source_type) + kombinasi yang dibuat penulis di kode
TERUKUR = [("ADJUSTMENT", "STOCK_ADJUSTMENT"), ("GOODS_RECEIPT_REVERSAL", "GOODS_RECEIPT_VOID"), ("MATERIAL_ISSUE", "MATERIAL_ISSUE"),
           ("MATERIAL_ISSUE_REVERSAL", "MATERIAL_ISSUE"), ("OPENING_BALANCE", "OPENING_BALANCE"), ("PRODUCTION_OUTPUT", "PRODUCTION_OUTPUT"),
           ("PRODUCTION_OUTPUT_REVERSAL", "PRODUCTION_OUTPUT"), ("PURCHASE", "BILL"), ("PURCHASE", "GOODS_RECEIPT"), ("PURCHASE_RETURN", "BILL_VOID"),
           ("PURCHASE_RETURN", "STOCK_ADJUSTMENT_VOID"), ("RELOKASI_GUDANG_VOID", "RELOKASI_GUDANG_VOID"), ("RELOKASI_KELUAR", "RELOKASI_GUDANG"),
           ("RELOKASI_MASUK", "RELOKASI_GUDANG"), ("REVALUASI_KELUAR", "PPV_REVALUASI"), ("REVALUASI_KELUAR", "PPV_REVALUASI_VOID"),
           ("REVALUASI_MASUK", "PPV_REVALUASI"), ("REVALUASI_MASUK", "PPV_REVALUASI_VOID"), ("SALE", "DELIVERY_NOTE"), ("SALE", "INVOICE_FULFILLMENT"),
           ("SALE", "VENDOR_CREDIT"), ("SALES_RETURN", "CREDIT_NOTE"), ("SALES_RETURN_REVERSAL", "CREDIT_NOTE_VOID"), ("STOCK_ADJUSTMENT", "STOCK_ADJUSTMENT"),
           ("STOCK_ADJUSTMENT_REVERSAL", "STOCK_ADJUSTMENT_VOID"), ("VOID_REVERSAL", "DELIVERY_NOTE_VOID"), ("VOID_REVERSAL", "INVOICE_FULFILLMENT_VOID"),
           ("VOID_REVERSAL", "STOCK_ADJUSTMENT_VOID"), ("VOID_REVERSAL", "VENDOR_CREDIT_VOID")]


@pytest.mark.parametrize("mv,src", TERUKUR)
def test_jenis_text_tiap_kosakata_terukur_bukan_kode_mentah(mv, src):
    t = BT.jenis_text(mv, src)
    assert t and not re.search(r"[A-Z]{3,}|_", t), (mv, src, t)           # tak ada kode mentah (huruf besar beruntun / garis bawah)
    assert t[0].isupper()


def test_jenis_text_contoh_dari_tester():
    assert BT.jenis_text("GOODS_RECEIPT_REVERSAL", "GOODS_RECEIPT_VOID") == "Pembalikan penerimaan barang"
    assert BT.jenis_text("REVALUASI_MASUK", "PPV_REVALUASI") == "Revaluasi masuk"
    assert BT.jenis_text("REVALUASI_KELUAR", "PPV_REVALUASI") == "Revaluasi keluar"
    assert BT.jenis_text("REVALUASI_MASUK", "PPV_REVALUASI_VOID") == "Pembatalan revaluasi masuk"


def test_jenis_text_tak_dikenal_dan_kosong_tak_pernah_mentah():
    assert BT.jenis_text("ZZZ_BARU", "ZZZ_SUMBER") == "Mutasi stok"
    assert BT.jenis_text(None, None) == "Mutasi stok"
    assert BT.jenis_text("PURCHASE", "SUMBER_BARU") == "Pembelian"           # jatuh ke teks per movement_type
    assert BT.jenis_text("SALE", None) == "Penjualan"


# --- aktivitas -----------------------------------------------------------------------------------------------------------
def test_norm_aktivitas_item_jadi_barang_dan_lainnya_tetap():
    assert BT.norm_aktivitas("Item dibuat") == "Barang dibuat"
    assert BT.norm_aktivitas("Item diperbarui") == "Barang diperbarui"
    assert BT.norm_aktivitas("Barang dinonaktifkan") == "Barang dinonaktifkan"
    assert BT.norm_aktivitas("Harga diubah") == "Harga diubah"
    assert BT.norm_aktivitas(None) is None and BT.norm_aktivitas("") == ""


def test_nama_tampil_prioritas_profil_lalu_nama_pengguna_lalu_cadangan():
    assert BT.nama_tampil("Budi Santoso", "Budi", "budi@x.id") == "Budi Santoso"
    assert BT.nama_tampil("  ", "Budi", "budi@x.id") == "Budi"
    assert BT.nama_tampil(None, None, "budi@x.id") == "budi@x.id"            # email hanya cadangan
    assert BT.nama_tampil(None, None, None) is None


# --- position_text_ket ---------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("b,stok,bd,sat", [
    ({"item_type": "goods", "track_inventory": True}, 6, 4, "pcs"),
    ({"item_type": "goods", "track_inventory": True}, 6, 6, "pcs"),
    ({"item_type": "goods", "track_inventory": True}, 6, None, "pcs"),
    ({"item_type": "goods", "track_inventory": True}, -3, None, "pcs"),
    ({"item_type": "goods", "track_inventory": True}, 0, None, "pcs"),
    ({"item_type": "service"}, 0, None, None),
    ({"item_type": "goods", "track_inventory": False}, 0, None, None),
    ({"item_type": "goods", "track_inventory": True, "status": "inactive"}, 5, None, "pcs")])
def test_position_text_ket_satu_sumber_dengan_position_text(b, stok, bd, sat):
    utama = BD.position_text(b, stok, bd, sat)
    ket = BD.position_text_ket(b, stok, bd, sat)
    if ket:
        assert utama.endswith(" · " + ket), (utama, ket)
        assert not re.search(r"^Stok \d", ket)                                # tanpa angka stok yang diulang di nilai besar
    else:
        assert ket is None


def test_position_text_ket_contoh():
    b = {"item_type": "goods", "track_inventory": True}
    assert BD.position_text_ket(b, 6, 4, "pcs") == "bisa dijual 4"
    assert BD.position_text_ket(b, -3, None, "pcs") == "perlu hitung fisik"
    assert BD.position_text_ket(b, 6, None, "pcs") is None


# --- rute ----------------------------------------------------------------------------------------------------------------
def _fn(nama):
    i = RUTE.index(f"async def {nama}(")
    j = RUTE.find("\n@router.", i)
    return RUTE[i:j if j != -1 else len(RUTE)]


def test_history_memuat_jenis_text_dari_kamus_satu_tempat():
    fn = _fn("get_item_history")
    assert '"jenis_text": BT.jenis_text(row["movement_type"], row["source_type"])' in fn
    assert '"movement_type": row["movement_type"]' in fn                     # bentuk lama tetap


def test_activity_memakai_nama_tampil_dan_deskripsi_barang():
    fn = _fn("get_item_activity")
    assert "BT.norm_aktivitas(" in fn and "BT.nama_tampil(" in fn
    assert '"User"' in fn and "user_profiles" in fn                          # nama tampil dari profil/pengguna, bersaring lewat actor_id
    assert "ia.tenant_id = $2" in fn or "tenant_id = $2" in fn


def test_position_text_ket_di_baris_daftar_dan_detail():
    assert RUTE.count("position_text_ket=BD.position_text_ket(") + RUTE.count('"position_text_ket": BD.position_text_ket(') >= 2
    assert "position_text_ket" in (APP / "schemas" / "items.py").read_text()
