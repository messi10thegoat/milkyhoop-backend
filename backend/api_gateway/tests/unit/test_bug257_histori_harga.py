"""BUG-257 (16 Nov 2026): GET /api/customers/{id}/price-history -- histori harga jual per pelanggan+barang dari faktur terposting non-void;
konversi satuan di SERVER (harga + qty), satuan tak dikenal -> 400 berkalimat, exclude_doc_id, limit dijepit, izin baca SALES_INVOICE. Hanya baca."""
import asyncio
import pathlib
import re
from datetime import date
from decimal import Decimal as D

import pytest

from app.services import histori_harga_pelanggan as HH
from app.services.galat_berkode import GalatBerkode

BE = pathlib.Path(__file__).resolve().parents[3]
ITEM = "11111111-1111-1111-1111-111111111111"
CUS = "22222222-2222-2222-2222-222222222222"


def baris(nomor, tgl, qty, unit, harga, disk=0, status="posted"):
    return {"doc_id": f"d-{nomor}", "nomor": nomor, "tanggal": tgl, "status": status, "quantity": D(str(qty)), "unit": unit,
            "unit_price": D(str(harga)), "discount_percent": D(str(disk))}


class Konn:
    """Kunci per SQL: product_units kosong; products = 1 barang dasar pcs; unit_conversions = karton 12 pcs; riwayat = baris yang disetel."""
    def __init__(self, rows, n=None):
        self.rows, self.n, self.panggilan = rows, len(rows) if n is None else n, []

    async def fetch(self, sql, *a):
        self.panggilan.append((sql, a))
        if "product_units" in sql:
            return []
        if "FROM products" in sql:
            return [{"id": ITEM, "nama_produk": "Aqua", "dasar": "pcs"}]
        if "unit_conversions" in sql:
            return [{"product_id": ITEM, "base_unit": "pcs", "conversion_unit": "karton", "conversion_factor": D("12")}]
        return self.rows

    async def fetchval(self, sql, *a):
        self.panggilan.append((sql, a))
        return self.n


def jalan(rows, unit=None, limit=10, exclude=None, n=None):
    k = Konn(rows, n)
    return asyncio.run(HH.riwayat(k, "t1", CUS, ITEM, unit, limit, exclude)), k


def test_tanpa_unit_satuan_asli_apa_adanya():
    r, _ = jalan([baris("INV-2", date(2026, 11, 3), 2, "karton", 120000, 5), baris("INV-1", date(2026, 10, 1), 10, "pcs", 10000)])
    a, b = r["items"]
    assert (a["nomor"], a["tanggal"], a["qty"], a["unit"], a["harga"], a["diskon_pct"], a["dikonversi"]) == ("INV-2", "2026-11-03", 2, "karton", 120000.0, 5.0, False)
    assert (b["unit"], b["harga"], b["dikonversi"]) == ("pcs", 10000.0, False) and r["total"] == 2
    assert a["status_text"] == "Terposting" and a["doc_type"] == "sales_invoice"


def test_unit_pcs_karton_dikonversi_ke_pcs_server():
    r, _ = jalan([baris("INV-2", date(2026, 11, 3), 2, "karton", 120000)], unit="pcs")
    x = r["items"][0]
    assert (x["unit"], x["qty"], x["harga"], x["dikonversi"]) == ("pcs", 24, 10000.0, True)


def test_unit_karton_dari_baris_pcs_dan_baris_tanpa_satuan():
    r, _ = jalan([baris("A", date(2026, 11, 3), 24, "pcs", 10000), baris("B", date(2026, 11, 2), 12, None, 10000)], unit="karton")
    assert [(x["unit"], x["qty"], x["harga"]) for x in r["items"]] == [("karton", 2, 120000.0), ("karton", 1, 120000.0)]


def test_satuan_sama_tak_berubah_dan_pembulatan_2dp():
    r, _ = jalan([baris("A", date(2026, 11, 3), 3, "pcs", 10000)], unit="pcs")
    assert (r["items"][0]["qty"], r["items"][0]["harga"], r["items"][0]["dikonversi"]) == (3, 10000.0, True)
    r, _ = jalan([baris("B", date(2026, 11, 3), 1, "karton", 100000)], unit="pcs")   # 100000/12 = 8333,333 -> 8333,33 (HALF_UP 2dp)
    assert r["items"][0]["harga"] == 8333.33 and r["items"][0]["qty"] == 12


def test_satuan_diminta_tak_dikenal_barang_400_berkalimat():
    with pytest.raises(GalatBerkode) as e:
        jalan([baris("A", date(2026, 11, 3), 1, "pcs", 10000)], unit="dus")
    assert e.value.status_code == 400 and e.value.code == "SATUAN_TAK_TERKONVERSI"


def test_baris_satuan_tak_terkonversi_tampil_apa_adanya_bukan_angka_karangan():
    r, _ = jalan([baris("A", date(2026, 11, 3), 1, "dus", 50000)], unit="pcs")
    x = r["items"][0]
    assert (x["unit"], x["qty"], x["harga"], x["dikonversi"]) == ("dus", 1, 50000.0, False)


def test_kosong_tak_membaca_baris_dan_total_nol():
    r, k = jalan([], n=0)
    assert r == {"items": [], "total": 0} and len(k.panggilan) == 1


def test_total_semua_baris_limit_dijepit_dan_exclude_diteruskan():
    r, k = jalan([baris("A", date(2026, 11, 3), 1, "pcs", 10000)], limit=999, exclude="dd-1", n=37)
    assert r["total"] == 37
    sql, a = next(p for p in k.panggilan if "LIMIT" in p[0])
    assert a[-1] == HH.BATAS_MAKS == 50 and a[3] == "dd-1" and a[0] == "t1"
    jalan([baris("A", date(2026, 11, 3), 1, "pcs", 10000)], limit=0)   # tak meledak; dijepit ke 1


def test_sql_sumber_faktur_terposting_non_void_berpagar_tenant_urutan_terbaru():
    q = HH.SQL_RIWAYAT
    assert "si.tenant_id = $1" in q and "si.customer_id = $2" in q and "sii.item_id = $3" in q
    assert "si.status NOT IN ('draft', 'void')" in q and "NOT si.is_opening_balance" in q and "$4::uuid IS NULL OR si.id <> $4" in q
    assert "ORDER BY si.invoice_date DESC, si.created_at DESC, sii.line_number DESC LIMIT $5" in q
    assert "si.tenant_id = $1" in HH.SQL_TOTAL and "LIMIT" not in HH.SQL_TOTAL
    assert not re.search(r"\b(INSERT|UPDATE|DELETE)\b", q + HH.SQL_TOTAL, re.I)   # hanya baca


def test_rute_dan_izin_baca_sales_invoice():
    rute = (BE / "api_gateway" / "app" / "routers" / "customers.py").read_text()
    assert '@router.get("/{customer_id}/price-history")' in rute and "histori_harga.riwayat(" in rute
    izin = (BE / "api_gateway" / "app" / "middleware" / "permission_middleware.py").read_text()
    assert '(r"^/api/customers/[^/]+/price-history$", ["GET"], "sales_invoice", "R")' in izin
