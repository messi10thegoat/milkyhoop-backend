"""Pesanan Pembelian — hitungan uang MURNI (P-U2 sisi tulis, 22 Okt 2026). Decimal 2dp ROUND_HALF_UP (Law 9/25); nol int()/float.

Dulu: int() memotong, unit_price int (sen hilang), dan TRIGGER DB `update_purchase_order_totals` (variabel BIGINT) menimpa total header = Σ baris
sehingga diskon & pajak header hilang. Trigger di-DROP (V435); semua jalur tulis memakai fungsi di sini (SATU sumber).

Baris : subtotal = qty x harga (bruto) ; diskon = persen x subtotal ATAU nominal (maks = subtotal) ; pajak = (subtotal - diskon) x tarif ; total = subtotal - diskon + pajak.
Header: subtotal = Σ (subtotal - diskon baris) [neto]; diskon header = persen x subtotal ATAU nominal (maks = subtotal);
        pajak = (subtotal - diskon header) x tarif header BILA tarif header > 0, selain itu Σ pajak baris x (subtotal - diskon header)/subtotal; total = subtotal - diskon + pajak.
Pola acuan: NetSuite PO (diskon baris + pajak per baris, total = neto + pajak).
BUG-088 (8 Nov 2026, konfirmasi pemilik LANGSUNG): faktor DPP kode pajak (PMK 131/2024, PPN 12% = DPP 11/12) -- `faktor` (num, den) dari
po_rencana.faktor_masukan, rumus CERMIN tagihan: baris = bills_service._bill_line_tax (DPP dibulatkan 2dp lalu pajak), header = BillCalculator
(DPP tak dibulatkan). Bawaan (1, 1) = DPP penuh = perilaku lama."""
from decimal import Decimal, ROUND_HALF_UP

NOL = Decimal("0")
DUA = Decimal("0.01")
SERATUS = Decimal("100")


def _d(v) -> Decimal:
    return Decimal(str(v)) if v is not None else NOL


def r2(x) -> Decimal:
    return _d(x).quantize(DUA, rounding=ROUND_HALF_UP)


KODE_DISKON_BARIS = "DISKON_BARIS_MELEBIHI"


def diskon_berlebih(item: dict):
    """U-SI-LAW (putusan pemilik LANGSUNG 2 Nov, MASTER: PO simetris): (diskon, subtotal) bila diskon baris > nilai baris, selain itu None."""
    qty, harga = _d(item.get("quantity")), _d(item.get("unit_price"))
    persen, nominal = _d(item.get("discount_percent")), _d(item.get("discount_amount"))
    subtotal = r2(qty * harga)
    diskon = r2(subtotal * persen / SERATUS) if persen > NOL else r2(nominal)
    return (diskon, subtotal) if diskon > subtotal else None


def blok_diskon_baris(items: list) -> list:
    """Blok pratinjau (kalimat SAMA dengan galat 400 penulis) untuk setiap baris berdiskon melebihi nilainya."""
    from .teks_galat import diskon_baris_melebihi
    return [{"code": KODE_DISKON_BARIS, "message": diskon_baris_melebihi(*x), "status": 400}
            for x in (diskon_berlebih(i) for i in items) if x]


def hitung_baris(item: dict, tolak: bool = True, faktor=(1, 1)) -> dict:
    """item: {quantity, unit_price, discount_percent, discount_amount, tax_rate, ...} -> item + subtotal/discount_amount/tax_amount/total (Decimal 2dp).
    U-SI-LAW: diskon > nilai baris DITOLAK (dulu dipotong diam-diam ke subtotal). tolak=True (penulis) -> GalatBerkode 400; tolak=False
    (pratinjau, blok dari blok_diskon_baris) -> angka tampil dipotong supaya tabel tetap terbaca."""
    qty, harga = _d(item.get("quantity")), _d(item.get("unit_price"))
    persen, nominal, tarif = _d(item.get("discount_percent")), _d(item.get("discount_amount")), _d(item.get("tax_rate"))
    subtotal = r2(qty * harga)
    diskon = r2(subtotal * persen / SERATUS) if persen > NOL else r2(nominal)
    if diskon > subtotal:
        if tolak:
            from .galat_berkode import GalatBerkode
            from .teks_galat import diskon_baris_melebihi
            raise GalatBerkode(400, KODE_DISKON_BARIS, diskon_baris_melebihi(diskon, subtotal), field="discount_amount")
        diskon = subtotal
    neto = subtotal - diskon
    n, d = int(faktor[0]), int(faktor[1])
    dpp = neto if n == d else r2(neto * n / d)
    pajak = r2(dpp * tarif / SERATUS) if tarif > NOL else NOL
    return {**item, "subtotal": subtotal, "discount_amount": diskon, "tax_amount": pajak, "total": neto + pajak}


def hitung_header(baris: list, discount_percent=0, discount_amount=0, tax_rate=0, faktor=(1, 1)) -> dict:
    """baris: hasil hitung_baris (atau baris DB dgn subtotal/discount_amount/tax_amount). -> {subtotal, discount_amount, tax_amount, total_amount}."""
    subtotal = sum((_d(b["subtotal"]) - _d(b["discount_amount"]) for b in baris), NOL)
    persen, nominal, tarif = _d(discount_percent), _d(discount_amount), _d(tax_rate)
    diskon = r2(subtotal * persen / SERATUS) if persen > NOL else r2(nominal)
    diskon = min(diskon, subtotal)
    setelah = subtotal - diskon
    if tarif > NOL:
        n, d = int(faktor[0]), int(faktor[1])
        pajak = r2((setelah if n == d else setelah * n / d) * tarif / SERATUS)
    else:  # pajak baris diskalakan oleh diskon header (dasar pajak ikut turun, pola NetSuite)
        tot_pajak = sum((_d(b["tax_amount"]) for b in baris), NOL)
        pajak = r2(tot_pajak * setelah / subtotal) if subtotal > NOL else NOL
    return {"subtotal": r2(subtotal), "discount_amount": r2(diskon), "tax_amount": r2(pajak), "total_amount": r2(setelah + pajak)}
