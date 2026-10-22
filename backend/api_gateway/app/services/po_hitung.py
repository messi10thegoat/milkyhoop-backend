"""Pesanan Pembelian — hitungan uang MURNI (P-U2 sisi tulis, 22 Okt 2026). Decimal 2dp ROUND_HALF_UP (Law 9/25); nol int()/float.

Dulu: int() memotong, unit_price int (sen hilang), dan TRIGGER DB `update_purchase_order_totals` (variabel BIGINT) menimpa total header = Σ baris
sehingga diskon & pajak header hilang. Trigger di-DROP (V435); semua jalur tulis memakai fungsi di sini (SATU sumber).

Baris : subtotal = qty x harga (bruto) ; diskon = persen x subtotal ATAU nominal (maks = subtotal) ; pajak = (subtotal - diskon) x tarif ; total = subtotal - diskon + pajak.
Header: subtotal = Σ (subtotal - diskon baris) [neto]; diskon header = persen x subtotal ATAU nominal (maks = subtotal);
        pajak = (subtotal - diskon header) x tarif header BILA tarif header > 0, selain itu Σ pajak baris x (subtotal - diskon header)/subtotal; total = subtotal - diskon + pajak.
Pola acuan: NetSuite PO (diskon baris + pajak per baris, total = neto + pajak)."""
from decimal import Decimal, ROUND_HALF_UP

NOL = Decimal("0")
DUA = Decimal("0.01")
SERATUS = Decimal("100")


def _d(v) -> Decimal:
    return Decimal(str(v)) if v is not None else NOL


def r2(x) -> Decimal:
    return _d(x).quantize(DUA, rounding=ROUND_HALF_UP)


def hitung_baris(item: dict) -> dict:
    """item: {quantity, unit_price, discount_percent, discount_amount, tax_rate, ...} -> item + subtotal/discount_amount/tax_amount/total (Decimal 2dp)."""
    qty, harga = _d(item.get("quantity")), _d(item.get("unit_price"))
    persen, nominal, tarif = _d(item.get("discount_percent")), _d(item.get("discount_amount")), _d(item.get("tax_rate"))
    subtotal = r2(qty * harga)
    diskon = r2(subtotal * persen / SERATUS) if persen > NOL else r2(nominal)
    diskon = min(diskon, subtotal)
    neto = subtotal - diskon
    pajak = r2(neto * tarif / SERATUS) if tarif > NOL else NOL
    return {**item, "subtotal": subtotal, "discount_amount": diskon, "tax_amount": pajak, "total": neto + pajak}


def hitung_header(baris: list, discount_percent=0, discount_amount=0, tax_rate=0) -> dict:
    """baris: hasil hitung_baris (atau baris DB dgn subtotal/discount_amount/tax_amount). -> {subtotal, discount_amount, tax_amount, total_amount}."""
    subtotal = sum((_d(b["subtotal"]) - _d(b["discount_amount"]) for b in baris), NOL)
    persen, nominal, tarif = _d(discount_percent), _d(discount_amount), _d(tax_rate)
    diskon = r2(subtotal * persen / SERATUS) if persen > NOL else r2(nominal)
    diskon = min(diskon, subtotal)
    setelah = subtotal - diskon
    if tarif > NOL:
        pajak = r2(setelah * tarif / SERATUS)
    else:  # pajak baris diskalakan oleh diskon header (dasar pajak ikut turun, pola NetSuite)
        tot_pajak = sum((_d(b["tax_amount"]) for b in baris), NOL)
        pajak = r2(tot_pajak * setelah / subtotal) if subtotal > NOL else NOL
    return {"subtotal": r2(subtotal), "discount_amount": r2(diskon), "tax_amount": r2(pajak), "total_amount": r2(setelah + pajak)}
