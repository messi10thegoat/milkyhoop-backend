"""BUG-202: PDF Pesanan Pembelian lebih lengkap: label "Tanggal kirim", pajak per baris (opt-in k.pajak), kode barang
cadangan = SKU master, petunjuk kop kosong (hanya di layar). Paritas nyata (klon) = uji salinan."""
import asyncio
import uuid
from datetime import date
from decimal import Decimal as D

import pytest

from app.services import po_cetak as PC
from app.services.pdf_service import PDFService

TID = "tenant-uji"


class _Conn:
    def __init__(self, po, vendor=None, items=(), tenant=None):
        self.po, self.vendor, self.items, self.sql = po, vendor, list(items), []
        self.tenant = tenant or {"display_name": "Usaha Uji", "address": "Jl. Pabrik 2", "phone": "0812", "logo_url": None, "tax_id": "01.234"}

    async def fetchrow(self, sql, *a):
        self.sql.append((sql, a))
        if "FROM purchase_orders" in sql:
            return self.po
        if "FROM vendors" in sql:
            assert a[1] == TID
            return self.vendor
        if '"Tenant"' in sql:
            return self.tenant
        raise AssertionError(sql)

    async def fetch(self, sql, *a):
        self.sql.append((sql, a))
        return self.items


def _po(**k):
    d = {"id": uuid.uuid4(), "po_number": "PO-2611-0001", "status": "sent", "po_date": date(2026, 11, 13), "expected_date": date(2026, 11, 20), "ref_no": None,
         "ship_to_address": None, "warehouse_name": "Gudang Utama", "notes": None, "cancelled_at": None, "cancelled_reason": None, "vendor_id": uuid.uuid4(),
         "vendor_name": "CV X", "subtotal": D("100000"), "discount_percent": D("0"), "discount_amount": D("0"), "tax_rate": D("11"), "tax_amount": D("11000"),
         "total_amount": D("111000")}
    d.update(k)
    return d


def _item(**k):
    d = {"description": "Kain", "item_code": "BRG-1", "quantity": D("2"), "unit": "m", "unit_price": D("50000"), "discount_percent": D("0"), "discount_amount": D("0"),
         "subtotal": D("100000"), "total": D("111000"), "tax_rate": D("11"), "tax_amount": D("1100")}
    d.update(k)
    return d


def _html(po=None, vendor=None, items=None, tenant=None):
    c = _Conn(po or _po(), vendor if vendor is not None else {"name": "CV X", "address": None, "city": None, "province": None, "postal_code": None, "phone": None,
                                                            "mobile_phone": None, "email": None, "tax_id": None, "payment_terms_days": 30},
              items if items is not None else [_item()], tenant)
    m = asyncio.run(PC.muat_pdf_po(c, {"tenant_id": TID}, uuid.uuid4()))
    return PDFService().render_purchase_order(m["po_data"], m["tenant_info"]).html, m["po_data"], c


def test_label_tanggal_kirim_bukan_jadwal_terima():
    h, _, _ = _html()
    assert "<b>Tanggal kirim</b>" in h and "Jadwal terima" not in h
    h2, _, _ = _html(po=_po(expected_date=None))
    assert "Tanggal kirim" not in h2.split("</style>", 1)[-1]                        # tanpa tanggal = baris tak dicetak


def test_pajak_per_baris_hanya_bila_ada_dan_opt_in():
    h, _, _ = _html()
    assert "Pajak 11% · Rp 1.100" in h and "Pajak 11%" in h                          # baris kecil di bawah nama barang + baris ringkasan header
    h0, _, _ = _html(items=[_item(tax_amount=D("0"), tax_rate=D("0"))])
    assert "Pajak 0%" not in h0 and "· Rp" not in h0.split("</style>", 1)[-1].split("Catatan")[0].split("<tbody>", 1)[-1].split("</tbody>")[0]
    # opt-in: tabel_barang TANPA k.pajak (dokumen lain) tak mencetak pajak walau barisnya punya
    ps = PDFService()
    t = ps.jinja_env.from_string('{% from "_partials/tabel_barang.html" import tabel_barang %}{{ tabel_barang(items, k, false) }}')
    keluar = t.render(items=[{"description": "A", "quantity": 1, "unit_price": 100, "line_total": 100, "tax_rate": 11, "tax_amount": 11}],
                      k={"desk": ["description"], "qty": "quantity", "harga": "unit_price", "jumlah": "line_total"})
    assert "Pajak" not in keluar.split("<tbody>", 1)[1]


def test_kode_barang_cadangan_sku_master_dan_pajak_di_kueri_baris():
    _, _, c = _html()
    sql = next(s for s, _ in c.sql if "FROM purchase_order_items" in s)
    assert "COALESCE(NULLIF(i.item_code, ''), pr.item_code) AS item_code" in sql and "pr.tenant_id = $2" in sql and "i.tax_amount" in sql
    h, _, _ = _html(items=[_item(item_code="BRG-9")])
    assert "<th>Kode</th>" in h and "BRG-9" in h
    h2, _, _ = _html(items=[_item(item_code=None)])
    assert "<th>Kode</th>" not in h2                                               # tanpa SKU sama sekali = kolom tak dicetak


def test_petunjuk_kop_kosong_hanya_layar():
    kosong = {"display_name": "Usaha Uji", "address": None, "phone": None, "logo_url": None, "tax_id": None}
    h, d, _ = _html(tenant=kosong)
    assert d["kop_kosong"] is True and 'class="petunjuk-kop"' in h and "Lengkapi profil perusahaan" in h
    css = h.split("</style>", 1)[0]
    assert ".petunjuk-kop{display:none}" in css and "@media screen{.petunjuk-kop{display:block" in css      # tersembunyi di cetak/PDF, tampil di layar
    h2, d2, _ = _html()
    assert d2["kop_kosong"] is False and 'class="petunjuk-kop"' not in h2
    h3, d3, _ = _html(tenant={"display_name": "U", "address": None, "phone": None, "logo_url": None, "tax_id": "01.234"})
    assert d3["kop_kosong"] is False
