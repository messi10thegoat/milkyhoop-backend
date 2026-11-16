"""BUG-272 (16 Nov 2026; putusan MASTER): teks tampil SERVER ADITIF untuk laporan persediaan -- Penjualan per Barang (GET /api/reports/profitability/items) dan Kartu Stok
(GET /api/items/{id}/history, K9). Medan lama TIDAK berubah; FE menampilkan teks ini apa adanya (aturan teks-server L1-L4). Uang = angka_laporan (tanpa "Rp", ribuan titik,
negatif dalam kurung; presisi tenant untuk total/nilai, 2 desimal-bila-ada-sen untuk harga/HPP satuan agar 500,50 tak jadi 501); qty = angka ringkas Indonesia + satuan; persen = koma, minus.
Decimal, ROUND_HALF_UP; hanya penyajian."""
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from . import barang_daftar as BD
from . import teks_galat as tg

SATUAN_TEXT = "(Disajikan dalam Rupiah)"
STATUS_REKON = {"match": "Cocok dengan Laba Rugi", "minor_drift": "Selisih kecil dengan Laba Rugi", "major_drift": "Selisih besar dengan Laba Rugi"}


def _d(x) -> Decimal:
    return Decimal(str(x if x is not None else 0))


def uang(x, presisi: int = 0) -> Optional[str]:
    return None if x is None else tg.angka_laporan(_d(x), presisi)


def harga(x) -> Optional[str]:
    """Harga/HPP per satuan: sen tampil bila ada (presisi 2 = ',50' hanya bila sen != 0)."""
    return None if x is None else tg.angka_laporan(_d(x), 2)


def qty(x, satuan: Optional[str] = None) -> Optional[str]:
    if x is None:
        return None
    return BD._q(_d(x)) + (f" {satuan}" if satuan else "")


def persen(x) -> Optional[str]:
    if x is None:
        return None
    d = _d(x).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    teks = f"{abs(d):f}".rstrip("0").rstrip(".").replace(".", ",") if d != d.to_integral_value() else f"{abs(int(d))}"
    return f"{'-' if d < 0 else ''}{teks}%"


def tambah_teks_penjualan_barang(data: dict, presisi: int) -> dict:
    """data = isi `data` respons profitability/items ({period, summary, items, pagination, reconciliation}). Mengubah di tempat."""
    p = data["period"]
    data["periode_text"] = tg.rentang_tanggal(p["start_date"], p["end_date"])
    data["satuan_text"] = SATUAN_TEXT
    for it in data["items"]:
        u = it.get("unit") or None
        it.update(
            qty_sold_text=qty(it["qty_sold"], u), revenue_gross_text=uang(it["revenue_gross"], presisi), total_discount_text=uang(it["total_discount"], presisi),
            revenue_with_tax_text=uang(it["revenue_with_tax"], presisi), cogs_text=uang(it["cogs"], presisi), gross_profit_text=uang(it["gross_profit"], presisi),
            margin_text=persen(it["margin_pct"]), invoice_count_text=f"{it['invoice_count']} faktur", min_price_text=harga(it["min_price"]),
            max_price_text=harga(it["max_price"]), avg_price_text=harga(it["avg_price"]), qty_retur_text=qty(it["qty_retur"], u), retur_value_text=uang(it["retur_value"], presisi))
    s = data["summary"]
    s.update(total_revenue_gross_text=uang(s["total_revenue_gross"], presisi), total_discount_text=uang(s["total_discount"], presisi),
             total_revenue_with_tax_text=uang(s["total_revenue_with_tax"], presisi), total_cogs_text=uang(s["total_cogs"], presisi),
             total_gross_profit_text=uang(s["total_gross_profit"], presisi), overall_margin_text=persen(s["overall_margin_pct"]),
             total_qty_sold_text=qty(s["total_qty_sold"]), unique_items_text=f"{s['unique_items']} barang", unique_invoices_text=f"{s['unique_invoices']} faktur",
             total_retur_text=uang(s["total_retur"], presisi))
    r = data.get("reconciliation")
    if r:
        r["status_text"] = STATUS_REKON.get(r["severity"], r["severity"])
        r["drift_amount_text"] = uang(r["drift_amount"], presisi)
    return data


def tambah_teks_kartu_stok(hasil: dict, satuan: Optional[str], presisi: int) -> dict:
    """hasil = respons history K9 (data[], saldo_awal, ringkasan, filter, item). Mengubah di tempat; item.satuan dipasang."""
    hasil["item"]["satuan"] = satuan
    hasil["satuan_text"] = SATUAN_TEXT
    f = hasil["filter"]
    hasil["periode_text"] = tg.rentang_tanggal(f["date_from"], f["date_to"]) if f["date_from"] and f["date_to"] else (
        f"Sejak {tg.tanggal_pendek(f['date_from'])}" if f["date_from"] else (f"Sampai {tg.tanggal_pendek(f['date_to'])}" if f["date_to"] else None))
    hasil["saldo_awal_text"] = qty(hasil["saldo_awal"], satuan)
    for m in hasil["data"]:
        masuk, keluar = _d(m["quantity_in"]), _d(m["quantity_out"])
        m.update(tanggal_text=tg.tanggal_pendek(m["movement_date"]) if m["movement_date"] else None,
                 arah="masuk" if masuk > 0 and keluar == 0 else ("keluar" if keluar > 0 and masuk == 0 else "campur" if masuk > 0 else "nol"),
                 quantity_in_text=qty(masuk, satuan) if masuk else None, quantity_out_text=qty(keluar, satuan) if keluar else None,
                 perubahan_text=(f"+{qty(masuk - keluar, satuan)}" if masuk - keluar > 0 else qty(masuk - keluar, satuan)),
                 quantity_balance_text=qty(m["quantity_balance"], satuan), unit_cost_text=harga(m["unit_cost"]), total_cost_text=uang(m["total_cost"], presisi),
                 average_cost_text=harga(m["average_cost"]) if m["average_cost"] is not None else None)
    rg = hasil.get("ringkasan")
    if rg:
        rg.update(saldo_awal_text=qty(rg["saldo_awal"], satuan), total_masuk_text=qty(rg["total_masuk"], satuan), total_keluar_text=qty(rg["total_keluar"], satuan),
                  saldo_akhir_text=qty(rg["saldo_akhir"], satuan))
    return hasil
