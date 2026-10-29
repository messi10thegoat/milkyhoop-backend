"""Penentu aksi Faktur Pembelian (22 Okt 2026): post/void/delete + pratinjau memakai SATU penentu (services/tagihan_aksi).
Paritas pratinjau == tulisan pada data nyata: scripts/ops/uji-nyata-tagihan-aksi.sh."""
import inspect

import pytest

from app.middleware.permission_middleware import izin_untuk_jalur
from app.routers import bills as BR
from app.services import bills_service as BS
from app.services import tagihan_aksi as TA


def test_tulis_memakai_penentu_satu():
    post = inspect.getsource(BS.BillsService.post_bill)
    assert "TA.blok_posting(" in post and "resolve_account_id_by_role(" not in post
    assert "TA.hitung_debit(" in inspect.getsource(BS.BillsService._emit_bill_debit_lines)
    # B1b-2 (29 Okt): penentu hapus dipanggil inti hapus_draf_tagihan_core (rute tunggal + hapus massal)
    assert "hapus_draf_tagihan_core(" in inspect.getsource(BS.BillsService.delete_bill)
    assert "TA.blok_hapus(" in inspect.getsource(BS.hapus_draf_tagihan_core)
    void = inspect.getsource(BS.BillsService.void_bill)
    assert "TA.blok_void(" in void and "Cannot void bill with payments" not in void
    # putusan pemilik 22 Okt: UM vendor dilepas otomatis lewat layanan BACKEND2, di transaksi void, SESUDAH blok
    assert void.index("TA.blok_void(") < void.index("lepas_penerapan(") and 'sumber="api:bills.void"' in void


def test_pratinjau_readonly_tanpa_kunci():
    src = inspect.getsource(BR._pratinjau_aksi)
    assert "readonly=True" in src and "pg_advisory" not in src and "FOR UPDATE" not in src
    for f in (BR.preview_post_bill, BR.preview_void_bill, BR.preview_delete_bill):
        assert "_pratinjau_aksi(" in inspect.getsource(f)


def test_blok_void_nd_dan_bayar_sebut_nomor():
    src = inspect.getsource(TA.blok_void)
    assert "ADA_PEMBAYARAN" in src and "ADA_NOTA_DEBIT" in src and "vca.status = 'active'" in src and "payment_number" in src


@pytest.mark.parametrize("st,v2,paid,ref,kode", [
    ("void", "void", 0, None, "SUDAH_BATAL"), ("posted", "posted", 0, None, "BUKAN_DRAF"),
    ("draft", "draft", 10, None, "ADA_PEMBAYARAN"), ("draft", "draft", 0, "aset tetap", "ADA_REFERENSI"), ("draft", "draft", 0, None, None),
])
def test_blok_hapus(st, v2, paid, ref, kode):
    b = {"status": st, "status_v2": v2, "amount_paid": paid, "accounting_status": None, "operational_status": None}
    bl = TA.blok_hapus(b, ref)
    assert (bl[0]["code"] if bl else None) == kode


@pytest.mark.parametrize("aksi,modul", [("post", "P"), ("void", "V"), ("delete", "D")])
def test_izin_pratinjau_sama_dengan_tulis(aksi, modul):
    assert izin_untuk_jalur(f"/api/bills/x/{aksi}/preview", "POST")[:2] == ("purchase_invoice", modul)
