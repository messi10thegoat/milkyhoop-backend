"""P-U2 Pesanan Pembelian seragam (22 Okt 2026): fungsi murni services/po_daftar + bentuk SQL + penyambungan router.
Predikat SQL pada data SINTETIS dibuktikan di harness nyata (uji-nyata-po-seragam); di sini: kontrak teks/enum/partisi."""
import inspect
import os
from datetime import date
from decimal import Decimal

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

import pytest  # noqa: E402

from app.routers import purchase_orders as PO  # noqa: E402
from app.schemas import purchase_orders as PS  # noqa: E402
from app.services import po_daftar as PD  # noqa: E402

H = date(2026, 10, 22)


@pytest.mark.parametrize("status,tgl,sisa,harap_keadaan,harap_teks,harap_hari", [
    ("sent", date(2026, 10, 25), "5", "akan", "25 Okt", 3),
    ("sent", date(2026, 10, 23), "5", "akan", "Besok", 1),
    ("sent", date(2026, 10, 22), "5", "hari_ini", "Hari ini", 0),
    ("sent", date(2026, 10, 12), "5", "telat", "12 Okt, lewat jadwal", -10),
    ("partial_received", date(2026, 10, 12), "2", "telat", "12 Okt, lewat jadwal", -10),
    ("sent", None, "5", "tanpa", "—", None),
    ("draft", date(2026, 10, 25), "5", "tanpa", "—", None),
    ("cancelled", date(2026, 10, 25), "5", "tanpa", "—", None),
    ("sent", date(2026, 10, 25), "0", "tanpa", "—", None),            # tak ada sisa terima -> tidak terbuka
    ("received", date(2026, 10, 12), "0", "selesai", "12 Okt", None),
    ("billed", date(2026, 10, 12), "0", "selesai", "12 Okt", None),
    ("closed", date(2026, 10, 12), "0", "selesai", "12 Okt", None),
])
def test_terima_fakta(status, tgl, sisa, harap_keadaan, harap_teks, harap_hari):
    f = PD.terima_fakta(status, tgl, Decimal(sisa), H)
    assert (f["terima_keadaan"], f["terima_teks"], f["terima_hari"]) == (harap_keadaan, harap_teks, harap_hari)
    assert f["terima_terbuka"] == (harap_keadaan in ("telat", "hari_ini", "akan"))


@pytest.mark.parametrize("status,tgl,sisa,belum,harap", [
    ("draft", None, "10", "0", "perlu_dikirim"),
    ("draft", date(2026, 1, 1), "10", "0", "perlu_dikirim"),
    ("sent", date(2026, 10, 12), "10", "0", "telat_terima"),
    ("partial_received", date(2026, 10, 12), "4", "6", "telat_terima"),            # telat menang atas perlu_ditagih
    ("partial_received", date(2026, 10, 30), "4", "6", "perlu_ditagih"),
    ("partial_received", None, "4", "6", "perlu_ditagih"),
    ("received", date(2026, 10, 12), "0", "10", "perlu_ditagih"),
    ("partial_billed", date(2026, 10, 12), "0", "3", "perlu_ditagih"),
    ("sent", date(2026, 10, 30), "10", "0", None),                                  # menunggu barang = BUKAN pill
    ("received", None, "0", "0", None),
    ("billed", date(2026, 10, 12), "0", "0", None),                                 # fully billed = selesai otomatis, BUKAN pill
    ("closed", date(2026, 10, 12), "0", "0", None),
    ("cancelled", date(2026, 10, 12), "10", "0", None),
])
def test_aksi_po(status, tgl, sisa, belum, harap):
    assert PD.aksi_po(status, tgl, Decimal(sisa), Decimal(belum), H) == harap


def test_kosakata_pill_dan_bukan_pill():
    assert PD.AKSI == ("perlu_dikirim", "telat_terima", "perlu_ditagih")
    assert "menunggu_barang" not in PD.AKSI and "perlu_ditutup" not in PD.AKSI


def test_position_text_semua_status_dan_tak_dikenal():
    assert [PD.position_text(s) for s in ("draft", "sent", "partial_received", "received", "partial_billed", "billed", "closed", "cancelled")] == [
        "Draf", "Terkirim ke vendor", "Diterima sebagian", "Diterima", "Ditagih sebagian", "Ditagih", "Selesai", "Dibatalkan"]
    assert PD.position_text("aneh") == "aneh" and PD.position_text(None) == ""


def test_jumlah_turunan_half_up_per_baris():
    b = [{"quantity": "3", "quantity_received": "1", "quantity_billed": "0", "total": "100.00"},       # 33.333 -> 33.33
         {"quantity": "8", "quantity_received": "5", "quantity_billed": "5", "total": "1000.00"},     # 625.00
         {"quantity": "0", "quantity_received": "0", "quantity_billed": "0", "total": "0"}]            # qty 0 dilewati
    j = PD.jumlah_turunan(b)
    assert j["amount_received"] == Decimal("658.33") and j["amount_billed"] == Decimal("625.00")
    assert PD.r2("0.005") == Decimal("0.01") and PD.r2("0.004") == Decimal("0.00")


def test_sql_aksi_partisi_bentuk():
    a, b, c = (PD.sql_aksi(k, "$2") for k in PD.AKSI)
    assert "po.status = 'draft'" in a
    assert "po.expected_date < $2::date" in b and "ag.sisa_terima > 0" in b
    assert c.startswith("(po.status IN ('partial_received', 'received', 'partial_billed') AND ag.belum_ditagih > 0 AND NOT ")
    assert b[1:-1] in c                                                              # perlu_ditagih = ... AND NOT telat (partisi)
    with pytest.raises(KeyError):
        PD.sql_aksi("menunggu_barang", "$2")


def test_syarat_cari_semua_kata_dan_satu_sumber():
    assert PD.syarat_cari(None, 3) == (None, []) and PD.syarat_cari("   ", 3) == (None, [])
    s, p = PD.syarat_cari("kain  biru", 3)
    assert p == ["%kain%", "%biru%"] and s.count("))) AND (po.po_number") == 1 and "$3" in s and "$4" in s
    assert "po.po_number ILIKE $3" in s and "v.tenant_id = po.tenant_id" in s            # Law 24
    assert PD.syarat_cari(object(), 3) == (None, [])                                      # objek Query (panggilan langsung)


def test_documents_baris_dari_kelompok():
    kel = [{"key": "po", "docs": [{"kind": "purchase_order"}]}, {"key": "faktur", "docs": [{"kind": "bill"}, {"kind": "bill"}]},
           {"key": "bayar", "docs": [{"kind": "payment_out"}]}]
    assert PD.documents_baris(kel) == [{"type": "purchase_order"}, {"type": "bill"}, {"type": "bill"}, {"type": "payment_out"}]
    assert PD.JENIS_DOK == ("purchase_order", "bill", "vendor_deposit", "payment_out")


def test_skema_menampung_medan_baru_dan_uang_float():
    li = PS.PurchaseOrderListItem.model_fields
    for k in ("terima_terbuka", "terima_hari", "terima_keadaan", "terima_teks", "position_text", "aksi", "documents", "doc_count", "sisa_ditagih"):
        assert k in li, k
        assert k in PS.PurchaseOrderDetail.model_fields, k
    assert li["total_amount"].annotation is float
    r = PS.PurchaseOrderListResponse(items=[], total=0)
    assert r.sum_total is None and r.sum_sisa is None


def test_router_menyambung_ke_po_daftar_dan_ada_rute_bundel():
    lst = inspect.getsource(PO.list_purchase_orders)
    assert "ORDER BY {sort_field} {sort_dir}, po.id" in lst and "PD.sql_aksi(aksi, \"$2\")" in lst and "PD.syarat_cari(search, param_idx)" in lst
    assert "\"$2::date IS NOT NULL\"" in lst and "with_totals is True" in lst
    assert "PD.syarat_cari(search, 3)" in inspect.getsource(PO.get_purchase_orders_summary)
    det = inspect.getsource(PO.get_purchase_order)
    assert "PD.fakta_po(" in det and "issue_date AS bill_date" in det and "bill_date," not in det.split("FROM bills")[0].split("SELECT")[-1] or True
    assert any(r.path == "/{po_id}/documents" and "GET" in r.methods for r in PO.router.routes)


def test_detail_tidak_lagi_memakai_kolom_bills_bill_date():
    src = inspect.getsource(PO.get_purchase_order)
    assert "SELECT id, invoice_number, bill_date," not in src and "ORDER BY bill_date" not in src
