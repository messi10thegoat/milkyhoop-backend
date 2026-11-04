"""Ringkasan angka dokumen cetak -- SATU sumber untuk Faktur Penjualan, Nota Kredit, Nota Debit, Faktur Pembelian, Pesanan Pembelian,
Penawaran (PDF seragam 4 Nov 2026, putusan MASTER "pola SaaS Accurate/QBO/Xero"):

    kolom Total baris = SESUDAH diskon baris  ->  Subtotal = SIGMA kolom Total  ->  diskon dokumen  ->  ongkir  ->  DPP/PPN  ->  Total

Baris "Diskon item" DIHAPUS: diskonnya terlihat di kolom Diskon tabel. Semua angka = kolom TERSIMPAN dokumen (Law 19, Decimal Law 9);
satu-satunya turunan = Subtotal neto = subtotal bruto tersimpan - diskon baris tersimpan, dan ia DIPERIKSA sama dengan SIGMA neto baris.
Bila tidak sama (data lama ber-pembulatan lain) -> pola lama (Subtotal bruto + "Diskon item"), supaya kertas tetap menjumlah.
Angka akhir (DPP/PPN/Total/Dibayar/Sisa) tak pernah dihitung di sini -- hanya disalin.

Keluaran: [{label, nilai, kelas}] -- kelas: "" | "kurang" (dicetak -Rp) | "tot" | "bayar" | "sisa" | "status" (nilai teks) | "coret" | "tebal".
Dirender _partials/ringkasan.html (gaya keluarga invoice.css atau penawaran.css)."""
import logging
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

logger = logging.getLogger(__name__)
NOL = Decimal("0")


def _d(v) -> Decimal:
    try:
        return Decimal(str(v)) if v not in (None, "") else NOL
    except InvalidOperation:
        return NOL


def subtotal_baris(subtotal_bruto, diskon_baris, neto_baris: list) -> list:
    """Baris pembuka ringkasan. Neto = bruto - diskon baris bila == SIGMA neto baris (selalu, untuk data yang dihitung kalkulator
    satu-definisi); selain itu pola lama, dicatat."""
    bruto, diskon = _d(subtotal_bruto), _d(diskon_baris)
    neto = bruto - diskon
    sigma = sum((_d(x) for x in neto_baris), NOL)
    if sigma == neto or diskon == NOL and not neto_baris:
        return [{"label": "Subtotal", "nilai": neto, "kelas": ""}]
    logger.warning("[PDF] ringkasan: SIGMA neto baris %s != subtotal %s - diskon baris %s -> pola lama", sigma, bruto, diskon)
    baris = [{"label": "Subtotal", "nilai": bruto, "kelas": ""}]
    if diskon > NOL:
        baris.append({"label": "Diskon item", "nilai": diskon, "kelas": "kurang"})
    return baris


def label_pajak(tarif_kepala, tarif_baris: list, pajak, dpp, campuran_lain: bool = False) -> str:
    """Label baris pajak: tarif kepala -> tarif mayoritas baris (beda tarif = campuran) -> efektif dari DPP (pola templat lama SI/FPB)."""
    tarif = _d(tarif_kepala)
    campuran = False
    if tarif <= NOL:
        ada = [_d(t) for t in tarif_baris if _d(t) > NOL]
        if ada:
            tarif, campuran = ada[0], any(t != ada[0] for t in ada)
    campuran = campuran or (tarif > NOL and campuran_lain)
    if tarif <= NOL and _d(pajak) > NOL and _d(dpp) > NOL:
        tarif = (_d(pajak) / _d(dpp) * 100).quantize(Decimal("0.01"))
    if campuran:
        return "Pajak (campuran)"
    return f"PPN {int(tarif.quantize(Decimal('1'), rounding=ROUND_HALF_UP))}%" if tarif > NOL else "Pajak"


def _jika(label, nilai, kelas=""):
    return [{"label": label, "nilai": _d(nilai), "kelas": kelas}] if _d(nilai) > NOL else []


def susun(*, subtotal_bruto, diskon_baris, neto_baris, potongan=(), ongkir=0, dpp=(), pajak=None, total, coret=False, akhir=(),
          label_total="Total") -> list:
    """potongan: [(label, nilai)] diskon dokumen (dicetak -Rp, hanya bila > 0) · dpp: [(label, nilai)] · pajak: (label, nilai) atau None ·
    akhir: baris sesudah Total (Dibayar / Sisa / Status) apa adanya."""
    rows = subtotal_baris(subtotal_bruto, diskon_baris, neto_baris)
    for label, nilai in potongan:
        rows += _jika(label, nilai, "kurang")
    rows += _jika("Ongkos kirim", ongkir)
    if pajak and _d(pajak[1]) > NOL:
        rows += [{"label": l, "nilai": _d(n), "kelas": ""} for l, n in dpp if n is not None]
        rows.append({"label": pajak[0], "nilai": _d(pajak[1]), "kelas": ""})
    rows.append({"label": label_total, "nilai": _d(total), "kelas": "tot coret" if coret else "tot"})
    return rows + list(akhir)


# ---- adaptor per dokumen (medan TERSIMPAN masing-masing pemuat) ----

def faktur_penjualan(inv: dict) -> list:
    items = inv.get("items") or []
    tarif_lain = _d(inv.get("shipping_tax_amount")) > NOL and _d(inv.get("shipping_tax_rate")) != _d(inv.get("tax_rate"))
    if inv.get("show_dpp_nilai_lain"):
        dpp = [("DPP", inv.get("dpp_harga_jual_total")), ("DPP Nilai Lain", inv.get("dpp_nilai_lain_total"))]
    else:
        dpp = [("DPP", inv.get("dpp"))] if _d(inv.get("dpp")) > NOL else []
    c = inv.get("cetak") or {}
    if c.get("jenis") == "batal":
        akhir = [{"label": "Status", "nilai": "Dibatalkan", "kelas": "status"}]
    elif (c and c.get("jenis") != "draf") or _d(inv.get("amount_paid")) > NOL:
        akhir = [{"label": "Dibayar", "nilai": _d(inv.get("amount_paid")), "kelas": "bayar"},
                 {"label": "Sisa tagihan", "nilai": _d(inv.get("amount_due")), "kelas": "sisa"}]
    else:
        akhir = []
    return susun(subtotal_bruto=inv.get("subtotal"), diskon_baris=inv.get("item_discount_total"),
                 neto_baris=[_d(i.get("subtotal")) - _d(i.get("discount_amount")) for i in items],
                 potongan=[("Diskon faktur", inv.get("invoice_discount_total")), ("Diskon", inv.get("discount_amount")),
                           ("Diskon tunai", inv.get("cash_discount_total"))],
                 ongkir=inv.get("shipping_amount"), dpp=dpp,
                 pajak=(label_pajak(inv.get("tax_rate"), [i.get("tax_rate") for i in items], inv.get("tax_amount"), inv.get("dpp"),
                                    tarif_lain), inv.get("tax_amount")),
                 total=inv.get("total_amount") or inv.get("amount") or inv.get("grand_total"), akhir=akhir)


def nota(n: dict) -> list:
    """Nota Kredit / Nota Debit (bentuk SAMA: subtotal bruto, item_discount_total, discount_amount dokumen, items[].net)."""
    tarif = _d(n.get("tax_rate"))
    return susun(subtotal_bruto=n.get("subtotal"), diskon_baris=n.get("item_discount_total"), neto_baris=[i.get("net") for i in n.get("items") or []],
                 potongan=[("Diskon", n.get("discount_amount"))],
                 pajak=(f"PPN {tarif.normalize():f}%" if tarif > NOL else "PPN", n.get("tax_amount")), total=n.get("total_amount"))


def faktur_pembelian(bill: dict) -> list:
    items = bill.get("items") or []
    c = bill.get("cetak") or {}
    akhir = []
    if not (c and c.get("jenis") in ("batal", "draf")) and _d(bill.get("amount_paid")) > NOL:
        akhir = [{"label": "Dibayar", "nilai": _d(bill.get("amount_paid")), "kelas": "bayar"},
                 {"label": "Sisa tagihan", "nilai": _d(bill.get("amount_due")), "kelas": "sisa"}]
    return susun(subtotal_bruto=bill.get("subtotal"), diskon_baris=bill.get("item_discount_total"), neto_baris=[i.get("total") for i in items],
                 potongan=[("Diskon faktur", bill.get("invoice_discount_total")), ("Diskon tunai", bill.get("cash_discount_total"))],
                 dpp=[("DPP", bill.get("dpp"))] if bill.get("dpp") else [],
                 pajak=(label_pajak(bill.get("tax_rate"), [i.get("tax_rate") for i in items], bill.get("tax_amount"), bill.get("dpp")),
                        bill.get("tax_amount")),
                 total=bill.get("grand_total") or bill.get("amount"), coret=c.get("jenis") == "batal", akhir=akhir)


def _label_diskon(persen) -> str:
    return f"Diskon {_d(persen).normalize():f}%" if _d(persen) > NOL else "Diskon"


def pesanan_pembelian(po: dict) -> list:
    """purchase_orders.subtotal = SIGMA neto baris (po_hitung.hitung_header) -> diskon baris = 0."""
    return susun(subtotal_bruto=po.get("subtotal"), diskon_baris=0, neto_baris=[i.get("line_total") for i in po.get("items") or []],
                 potongan=[(_label_diskon(po.get("discount_percent")), po.get("discount_amount"))],
                 pajak=(po.get("label_pajak") or "Pajak", po.get("tax_amount")), total=po.get("total_amount"))


def penawaran(q: dict, label_pajak_tampil: str) -> list:
    """quotes.subtotal = net_subtotal (sales_doc_calc) -> diskon baris = 0."""
    persen = q.get("discount_value") if q.get("discount_type") == "percentage" else 0
    return susun(subtotal_bruto=q.get("subtotal"), diskon_baris=0, neto_baris=[i.get("line_total") for i in q.get("items") or []],
                 potongan=[(_label_diskon(persen), q.get("discount_amount"))], ongkir=q.get("shipping_amount"),
                 pajak=(label_pajak_tampil, q.get("tax_amount")), total=q.get("total_amount"))


def proforma(p: dict, batal=None) -> list:
    """Proforma (tagihan uang muka/termin/pelunasan atas SO): komposisi Nilai Pesanan (sales_orders.subtotal = SIGMA neto baris,
    diskon baris 0) lalu baris TAGIHAN -- angka proformas_router.muat_pdf_proforma apa adanya."""
    tujuan = p.get("purpose")
    judul = {"PELUNASAN": "Pelunasan yang Ditagih", "TERMIN": "Termin yang Ditagih"}.get(tujuan, "Uang Muka yang Ditagih")
    if tujuan != "PELUNASAN" and p.get("dp_percent_display") is not None:
        judul += f" {p['dp_percent_display']}%"
    tagih = []
    if tujuan == "PELUNASAN":
        tagih += _jika("Uang muka sudah diterima", p.get("received_before"), "kurang")
    tagih.append({"label": judul, "nilai": _d(p.get("amount")), "kelas": "tot tebal"})   # angka yang DITAGIH = baris tebal (pola lama)
    sisa = p.get("remaining_after_this")
    if sisa is not None and (tujuan == "PELUNASAN" or _d(sisa) > NOL):
        label = "Sisa setelah tagihan ini" if tujuan == "PELUNASAN" else \
            f"Pelunasan setelah {'termin ini' if tujuan == 'TERMIN' else 'uang muka'}, ditagih terpisah"
        tagih.append({"label": label, "nilai": _d(sisa), "kelas": ""})
    if p.get("is_partially_paid") or (p.get("is_paid") and not batal):
        tagih += [{"label": "Sudah dibayar", "nilai": _d(p.get("paid_amount")), "kelas": "kurang"},
                  {"label": "Sisa tagihan ini", "nilai": _d(p.get("outstanding_amount")), "kelas": "tot"}]
    items = p.get("order_items") or []
    if items and p.get("order_subtotal") is not None:
        return susun(subtotal_bruto=p.get("order_subtotal"), diskon_baris=0, neto_baris=[i.get("line_total") for i in items],
                     potongan=[("Diskon", p.get("order_discount"))], ongkir=p.get("order_shipping"),
                     pajak=("PPN", p.get("order_tax")), total=p.get("order_total_amount"), label_total="Nilai Pesanan", akhir=tagih)
    awal = [{"label": "Nilai Pesanan", "nilai": _d(p.get("order_total_amount")), "kelas": ""}] if p.get("order_total_amount") else []
    return awal + tagih
