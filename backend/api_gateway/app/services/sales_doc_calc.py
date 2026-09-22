"""Satu kalkulator PPN + diskon untuk dokumen penjualan (Faktur, Pesanan, SO->Faktur).

Dipakai oleh SEMUA jalur berikut -- jangan buat salinan aritmetika pajak di tempat lain:
  sales_invoices: create, update, /calculate, /preview-journal
  sales_orders:   create, update, /{id}/to-invoice

ATURAN (Law 9: Decimal + ROUND_HALF_UP, 2 desimal):
  1. Neto baris = qty x harga - diskon baris, dibulatkan HALF_UP 2dp PER BARIS.
     (diskon baris: persen bila > 0, selain itu nominal -- sama dengan perilaku lama.)
  2. Diskon dokumen (nominal, ATAU persen dari SIGMA neto baris SESUDAH diskon baris)
     dialokasikan PRO-RATA ke SEMUA baris menurut neto baris -- TERMASUK baris tak kena
     pajak. Baris TERAKHIR menyerap sisa pembulatan. Ini pola yang sama dengan alokasi
     PSAK-72 `allocated_amount` di _post_invoice, sehingga per baris DPP == allocated_amount
     (bila tanpa ongkir).
  3. Per baris: DPP = neto - alokasi diskon dokumen; PPN = DPP x tarif baris, HALF_UP 2dp.
  4. Header = JUMLAH baris, tidak pernah dihitung ulang terpisah. Ongkir ditambahkan ke
     total DI LUAR DPP (perilaku lama SO). Apakah ongkir kena PPN = PERTANYAAN KEBIJAKAN
     untuk konsultan pajak pemilik -- sengaja TIDAK diputuskan di kode.
  5. DPP per baris yang benar-benar dipakai dikembalikan untuk disimpan.

DI LUAR CAKUPAN (jangan ditambahkan di sini tanpa gerbangnya sendiri): tarif tetap persis
yang dikirim baris / tax_codes; TIDAK ada DPP nilai lain 11/12; TIDAK ada PPN diskon tunai;
Tagihan, Nota Kredit, e-Faktur = fase 3.

Modul ini MURNI (tanpa DB, tanpa FastAPI) supaya gerbangnya bisa diulang tanpa efek samping.
"""
from decimal import Decimal, ROUND_HALF_UP

_C = Decimal("0.01")
_ZERO = Decimal("0")
_HUNDRED = Decimal("100")


class DocumentDiscountError(ValueError):
    """Diskon dokumen melebihi jumlah neto baris (DPP negatif). Router memetakan ke 400."""


def d(v) -> Decimal:
    if v is None or v == "":
        return _ZERO
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def q2(v: Decimal) -> Decimal:
    return v.quantize(_C, rounding=ROUND_HALF_UP)


def line_net(item: dict) -> dict:
    """Aturan 1. Kembalikan gross (2dp), diskon baris (2dp), neto (2dp).

    Identitas yang DIJAMIN: subtotal - discount_amount == net (tepat), jadi pembaca yang
    menurunkan neto dari dua kolom tersimpan mendapat angka yang sama dengan pajaknya.
    """
    qty = d(item.get("quantity"))
    price = d(item.get("unit_price"))
    gross = qty * price
    pct = d(item.get("discount_percent"))
    disc = gross * pct / _HUNDRED if pct > 0 else d(item.get("discount_amount"))
    net = q2(gross - disc)
    sub = q2(gross)
    return {"subtotal": sub, "discount_amount": sub - net, "net": net}


def resolve_document_discount(net_total: Decimal, amount=0, percent=0) -> Decimal:
    """Persen (bila > 0) dihitung dari SIGMA neto SESUDAH diskon baris; selain itu nominal."""
    pct = d(percent)
    if pct > 0:
        return q2(net_total * pct / _HUNDRED)
    return q2(d(amount))


def allocate(total: Decimal, weights: list) -> list:
    """Aturan 2. Pro-rata `total` menurut `weights`; baris terakhir menyerap sisa."""
    n = len(weights)
    if n == 0:
        return []
    wsum = sum(weights, _ZERO)
    if wsum == 0 or total == 0:
        out = [_ZERO] * n
        out[-1] = total  # total != 0 dengan wsum == 0 ditolak pemanggil; di sini tetap jumlah == total
        return out
    out = [q2(total * w / wsum) for w in weights[:-1]]
    out.append(total - sum(out, _ZERO))
    return out


def compute_document(items: list, doc_discount_amount=0, doc_discount_percent=0,
                     shipping_amount=0) -> dict:
    """Hitung satu dokumen. `items`: dict dengan quantity, unit_price, discount_percent,
    discount_amount (opsional), tax_rate. Mengembalikan baris (dengan kunci asli
    dipertahankan) + header. Semua angka Decimal 2dp."""
    lines = [dict(it, **line_net(it)) for it in items]
    net_total = sum((ln["net"] for ln in lines), _ZERO)
    doc_disc = resolve_document_discount(net_total, doc_discount_amount, doc_discount_percent)
    if doc_disc < 0:
        raise DocumentDiscountError("Diskon dokumen tidak boleh negatif.")
    if doc_disc > net_total:
        raise DocumentDiscountError(
            f"Diskon dokumen {doc_disc} melebihi jumlah baris setelah diskon {net_total}."
        )
    allocs = allocate(doc_disc, [ln["net"] for ln in lines])
    tax_total = _ZERO
    for ln, a in zip(lines, allocs):
        dpp = ln["net"] - a
        rate = d(ln.get("tax_rate"))
        tax = q2(dpp * rate / _HUNDRED) if rate > 0 else _ZERO
        ln["doc_discount_allocated"] = a
        ln["dpp"] = dpp
        ln["tax_amount"] = tax
        # `total` baris = neto + PPN baris (arti lama dipertahankan: nilai baris SEBELUM
        # diskon dokumen, ditambah PPN-nya). Header total = SIGMA total baris - diskon
        # dokumen + ongkir.
        ln["total"] = ln["net"] + tax
        tax_total += tax
    shipping = q2(d(shipping_amount))
    gross_total = sum((ln["subtotal"] for ln in lines), _ZERO)
    return {
        "items": lines,
        "gross_subtotal": gross_total,
        "line_discount_total": gross_total - net_total,
        "net_subtotal": net_total,
        "doc_discount": doc_disc,
        "dpp_total": net_total - doc_disc,
        "tax_amount": tax_total,
        "shipping_amount": shipping,
        "total_amount": net_total - doc_disc + tax_total + shipping,
    }


def plan_so_invoice(so_items: list, invoice_qty: dict, so_discount, so_shipping,
                    prior_discount=0, prior_shipping=0, is_last=False) -> dict:
    """SO -> Faktur (penuh atau parsial). Diskon & ongkir SO dibawa PRO-RATA menurut neto
    yang ditagih; faktur yang MENUNTASKAN SO menyerap sisa (SO - yang sudah dibawa faktur
    sebelumnya yang tidak void), jadi SIGMA semua faktur parsial == total SO.
    Pajak DIHITUNG ULANG lewat compute_document -- tidak disalin dari SO.

    so_items: SEMUA baris SO (id, quantity, unit_price, discount_percent, tax_rate, ...).
    invoice_qty: {so_item_id: qty yang ditagih di faktur ini}.
    """
    full_net = sum((line_net(it)["net"] for it in so_items), _ZERO)
    inv_items = []
    for it in so_items:
        q = invoice_qty.get(str(it["id"]))
        if q is None or d(q) <= 0:
            continue
        inv_items.append(dict(it, quantity=d(q), so_quantity=d(it["quantity"])))
    inv_net = sum((line_net(it)["net"] for it in inv_items), _ZERO)
    so_disc, so_ship = q2(d(so_discount)), q2(d(so_shipping))
    if is_last:
        disc = so_disc - q2(d(prior_discount))
        ship = so_ship - q2(d(prior_shipping))
    elif full_net > 0:
        disc = q2(so_disc * inv_net / full_net)
        ship = q2(so_ship * inv_net / full_net)
    else:
        disc = ship = _ZERO
    return compute_document(inv_items, doc_discount_amount=disc, shipping_amount=ship)
