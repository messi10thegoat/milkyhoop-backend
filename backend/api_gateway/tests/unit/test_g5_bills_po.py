"""G5 (22 Okt 2026): hapus/void bill memulihkan qty tertagih PO lewat kait BACKEND2 (services/po_daftar). Nyata: scripts/ops/uji-nyata-g5-bills-po.sh."""
import inspect

from app.services import bills_service as BS


def test_hapus_baca_po_sebelum_delete_lalu_pulihkan():
    # B1b-2 (29 Okt): isi hapus draf dipindah ke inti hapus_draf_tagihan_core (rute tunggal + hapus massal)
    assert "hapus_draf_tagihan_core(" in inspect.getsource(BS.BillsService.delete_bill)
    src = inspect.getsource(BS.hapus_draf_tagihan_core)
    i_po, i_del, i_pul = src.index("po_terkait_bill("), src.index("DELETE FROM bills WHERE id"), src.index("pulihkan_tagih(")
    assert i_po < i_del < i_pul  # bill_items ikut terhapus CASCADE -> PO tertaut WAJIB dibaca sebelum DELETE


def test_void_pulihkan_sesudah_status_void():
    src = inspect.getsource(BS.BillsService.void_bill)
    assert src.index("status_v2 = 'void'") < src.index("po_terkait_bill(") < src.index("pulihkan_tagih(")
