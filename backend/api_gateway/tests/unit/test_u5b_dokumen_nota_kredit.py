"""U5-B (4 Okt 2026): dokumen Nota Kredit = jenis P5 'credit_note' (html/pdf/share + grup panel SO opsional).

Penjaga: (1) daftar jenis kode == CHECK document_shares.kind di migrasi V379 (kind baru tanpa migrasi = INSERT tautan 500);
(2) izin per jenis (credit_note R untuk render/daftar tautan, E untuk bagikan) -- tanpa baris ini rute jatuh ke default
sales_order; (3) isi render: angka TERSIMPAN dicetak apa adanya dan di atas kertas menjumlah ke Total; stempel DRAF/DIBATALKAN;
(4) pemuat memfilter tenant di SETIAP kueri; (5) bundel panel TIDAK berubah tanpa ?sertakan=nota_kredit (FE live aman)."""
import asyncio
import re
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from app.middleware import permission_middleware as PM
from app.routers import dokumen as DK
from app.services import nota_kredit_dokumen as NK
from app.services.pdf_service import get_pdf_service

MIG = Path(__file__).resolve().parents[2].parent / "migrations"


def _kind_di_migrasi(nama):
    teks = (MIG / nama).read_text(encoding="utf-8")
    m = re.search(r"CHECK \(kind IN \(([^)]*)\)\)", teks)
    return set(re.findall(r"'([a-z_]+)'", m.group(1)))


def test_jenis_kode_sama_dengan_check_migrasi_v379():
    assert _kind_di_migrasi("V379__document_shares_kind_credit_note.sql") == set(DK.JENIS)


def test_rollback_v379_kembali_ke_enam_jenis_dan_membuang_tautan_baru():
    teks = (MIG / "V379__document_shares_kind_credit_note_ROLLBACK.sql").read_text(encoding="utf-8")
    assert "DELETE FROM document_shares WHERE kind = 'credit_note'" in teks
    assert _kind_di_migrasi("V379__document_shares_kind_credit_note_ROLLBACK.sql") == set(DK.JENIS) - {"credit_note"}


def test_kontrol_merah_pembanding_jenis_bisa_gagal():
    # alat ukur harus BISA merah: tanpa credit_note di sisi migrasi, kesetaraan runtuh.
    assert _kind_di_migrasi("V379__document_shares_kind_credit_note_ROLLBACK.sql") != set(DK.JENIS)


def test_jenis_label_dan_tabel_keberadaan():
    assert "credit_note" in DK.JENIS and DK.LABEL_JENIS["credit_note"] == "Nota Kredit"
    sql, draf = DK._DOKUMEN_TABEL["credit_note"]
    assert "credit_notes" in sql and "tenant_id = $2" in sql and draf == "draft"  # draf belum boleh dibagikan (409)


@pytest.mark.parametrize("path,method,izin", [
    ("/api/documents/credit_note/X/html", "GET", ("credit_note", "R")),
    ("/api/documents/credit_note/X/pdf", "GET", ("credit_note", "R")),
    ("/api/documents/credit_note/X/shares", "GET", ("credit_note", "R")),
    ("/api/documents/credit_note/X/share", "POST", ("credit_note", "E")),
    ("/api/documents/credit_note/X/share/preview", "POST", ("credit_note", "E")),
    ("/api/documents/credit_note/X/shares/Y/revoke", "POST", ("credit_note", "E")),
    # jenis lain TAK bergeser
    ("/api/documents/invoice/X/pdf", "GET", ("sales_invoice", "R")),
    ("/api/documents/delivery/X/share", "POST", ("sales_invoice", "E")),
])
def test_izin_dokumen_nota_kredit(path, method, izin):
    assert PM.PermissionMiddleware(lambda *a: None, False)._find_permission(path, method) == izin


# ── render ──────────────────────────────────────────────────────────────────────────────────────────────────────

def _data(**ubah):
    d = {
        "credit_note_number": "CN-2610-0099", "credit_note_date": date(2026, 10, 4), "status": "posted",
        "voided_at": None, "voided_reason": None, "customer_name": "PT Contoh Pelanggan",
        "original_invoice_number": "INV-2610-0007", "original_invoice_date": date(2026, 10, 1),
        "sales_order_number": "SO-2610-0003", "order_code_cetak": None,
        "reason": "return", "reason_label": "Retur barang", "reason_detail": "Dua kaos cacat jahitan", "ref_no": "RT-5",
        "notes": None,
        # bruto 300.000; diskon item 30.000; diskon dokumen 27.000; DPP 243.000; PPN 11% = 26.730; total 269.730
        "subtotal": Decimal("300000.00"), "item_discount_total": Decimal("30000.00"),
        "discount_amount": Decimal("27000.00"), "tax_rate": Decimal("11"), "tax_amount": Decimal("26730.00"),
        "total_amount": Decimal("269730.00"),
        "items": [
            {"description": "Kaos Polos Hitam", "quantity": Decimal("2"), "unit": "pcs", "unit_price": Decimal("100000.00"),
             "discount_amount": Decimal("30000.00"), "net": Decimal("170000.00")},
            {"description": "Kaos Polos Putih", "quantity": Decimal("1"), "unit": "pcs", "unit_price": Decimal("100000.00"),
             "discount_amount": Decimal("0"), "net": Decimal("100000.00")},
        ],
    }
    d.update(ubah)
    return d


TENANT = {"name": "Kaos Biru Konveksi", "address": "Jl. Contoh 1", "phone": "0812", "logo_data": None}


def _html(**ubah):
    r = get_pdf_service().render_credit_note(_data(**ubah), TENANT)
    return r.html, r


def test_render_mencetak_angka_tersimpan_dan_menjumlah_di_atas_kertas():
    d = _data()
    # identitas kertas: bruto - diskon item - diskon dokumen + PPN = total (angka TERSIMPAN, bukan hitung ulang di template)
    assert (d["subtotal"] - d["item_discount_total"] - d["discount_amount"] + d["tax_amount"]) == d["total_amount"]
    assert sum((i["net"] for i in d["items"]), Decimal("0")) == d["subtotal"] - d["item_discount_total"]
    html, _ = _html()
    for teks in ("Nota Kredit", "CN-2610-0099", "INV-2610-0007", "SO-2610-0003", "Retur barang", "PT Contoh Pelanggan",
                 "Rp 300.000", "-Rp 30.000", "-Rp 27.000", "PPN (11%)", "Rp 26.730", "Total Nota Kredit", "Rp 269.730",
                 "Rp 170.000", "Dua kaos cacat jahitan", "Kaos Biru Konveksi"):
        assert teks in html, teks
    assert "DIBATALKAN" not in html and "DRAF" not in html


def test_render_tanpa_diskon_dan_ppn_tak_mencetak_barisnya():
    html, _ = _html(item_discount_total=Decimal("0"), discount_amount=Decimal("0"), tax_amount=Decimal("0"),
                    tax_rate=Decimal("0"), subtotal=Decimal("270000"), total_amount=Decimal("270000"),
                    items=[{"description": "Kaos", "quantity": Decimal("2.5"), "unit": None, "unit_price": Decimal("108000"),
                            "discount_amount": Decimal("0"), "net": Decimal("270000")}])
    assert "Diskon" not in html and "PPN" not in html and "-Rp" not in html
    assert "2.5" in html or "2,5" in html


def test_stempel_dibatalkan_dan_draf():
    h_void, _ = _html(status="void", voided_at=datetime(2026, 10, 5, 3, 0, tzinfo=timezone.utc), voided_reason="salah faktur")
    assert "DIBATALKAN" in h_void and "salah faktur" in h_void
    h_draf, _ = _html(status="draft")
    assert "DRAF" in h_draf and "DIBATALKAN" not in h_draf


def test_pdf_terbentuk_satu_halaman_dengan_font_dokumen():
    _, r = _html()
    ps = get_pdf_service()
    pdf = ps.tulis_pdf(r)
    assert pdf[:5] == b"%PDF-"
    assert len(ps.dokumen(r).pages) == 1
    assert r.font is True and "fonts.css" in r.css  # jalur font yang sama dgn proforma/faktur


# ── pemuat ──────────────────────────────────────────────────────────────────────────────────────────────────────

class _Conn:
    def __init__(self, cn, items, faktur):
        self.cn, self.items, self.faktur, self.sql = cn, items, faktur, []

    async def fetchrow(self, sql, *a):
        self.sql.append((sql, a))
        if "FROM credit_notes" in sql:
            return self.cn if a[1] == "t1" else None
        if "FROM sales_invoices" in sql:
            return self.faktur
        if 'FROM "Tenant"' in sql:
            return {"display_name": "Usaha", "address": None, "phone": None, "logo_url": None}
        if "FROM sales_orders" in sql:
            return {"order_code": None, "order_title": None}
        return None

    async def fetch(self, sql, *a):
        self.sql.append((sql, a))
        return self.items

    async def fetchval(self, sql, *a):
        self.sql.append((sql, a))
        return None


def _cn_row():
    import uuid
    return {"id": uuid.uuid4(), "credit_note_number": "CN-1", "credit_note_date": date(2026, 10, 4), "status": "posted",
            "voided_at": None, "voided_reason": None, "customer_name": "X", "original_invoice_id": None,
            "original_invoice_number": "INV-LAMA", "reason": "pricing_error", "reason_detail": None, "ref_no": None,
            "notes": None, "subtotal": Decimal("100"), "discount_amount": Decimal("0"), "tax_rate": Decimal("0"),
            "tax_amount": Decimal("0"), "total_amount": Decimal("100")}


def test_pemuat_neto_dari_bruto_dikurangi_diskon_dan_tipe_decimal():
    cn = _cn_row()
    items = [{"description": "A", "quantity": Decimal("3"), "unit": "pcs", "unit_price": Decimal("50"),
              "discount_amount": Decimal("20"), "subtotal": Decimal("150")}]
    c = _Conn(cn, items, None)
    out = asyncio.run(NK.muat_pdf_nota_kredit(c, {"tenant_id": "t1"}, cn["id"]))
    d = out["credit_note_data"]
    assert d["items"][0]["net"] == Decimal("130") and d["item_discount_total"] == Decimal("20")
    assert all(isinstance(d[k], Decimal) for k in ("subtotal", "discount_amount", "tax_amount", "total_amount"))
    assert d["original_invoice_number"] == "INV-LAMA" and d["reason_label"] == "Koreksi harga"


def test_pemuat_404_bila_tenant_lain_atau_id_rusak():
    from fastapi import HTTPException
    cn = _cn_row()
    for tid, cid in (("t2", cn["id"]), ("t1", "bukan-uuid")):
        with pytest.raises(HTTPException) as e:
            asyncio.run(NK.muat_pdf_nota_kredit(_Conn(cn, [], None), {"tenant_id": tid}, cid))
        assert e.value.status_code == 404


def test_pemuat_setiap_kueri_berfilter_tenant():
    cn = _cn_row()
    cn["original_invoice_id"] = cn["id"]
    c = _Conn(cn, [], {"invoice_number": "INV-9", "invoice_date": date(2026, 9, 1), "sales_order_id": None, "order_number": None})
    asyncio.run(NK.muat_pdf_nota_kredit(c, {"tenant_id": "t1"}, cn["id"]))
    tanpa = [s for s, a in c.sql if "tenant_id" not in s and '"Tenant"' not in s and "credit_note_items" not in s]
    assert not tanpa, tanpa  # credit_note_items dibaca lewat id NK yang SUDAH lolos filter tenant


# ── bundel panel ────────────────────────────────────────────────────────────────────────────────────────────────

def test_bundel_panel_nk_hanya_bila_diminta():
    import inspect
    sig = inspect.signature(DK.susun_dokumen)
    assert sig.parameters["sertakan_nk"].default is False
    sumber = inspect.getsource(DK.susun_dokumen)
    bagian_nk = sumber[sumber.index("if sertakan_nk:"):]
    assert "cn.tenant_id = $1" in bagian_nk and "si.sales_order_id = $2" in bagian_nk
    assert "NOT IN ('draft', 'void')" in bagian_nk and '"key": "nk"' in bagian_nk
    rute = inspect.getsource(DK.dokumen_pesanan)
    assert 'sertakan == "nota_kredit"' in rute
