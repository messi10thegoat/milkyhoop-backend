"""NK POTONGAN BRUTO di tenant PKP (putusan pemilik LANGSUNG 21 Okt 2026): nominal potongan yang diketik = BRUTO (termasuk PPN).
Server memecahnya menjadi harga jual + PPN keluaran mengikuti KOMPOSISI PAJAK FAKTUR ASAL, sehingga PPN keluaran turun sebesar porsi
pajaknya (pola NetSuite/SAP credit memo). Non-PKP / faktur tanpa PPN / bentuk lain = tak tersentuh (jalur kalkulator lama persis).

ATURAN (Law 9: Decimal + ROUND_HALF_UP 2dp; Law 4: Σ baris == header):
  1. Kelompok faktur = baris faktur per (kode pajak, tarif) + ONGKIR sebagai baris sendiri (kode efektif: kode eksplisit, atau kode barang
     bertarif sama bila tepat satu -> ongkir null = IKUT barang). Tiap kelompok: B = bruto (harga jual sesudah diskon dokumen + PPN),
     T = PPN, H = harga jual, D = DPP yang dikenai tarif (11/12 dsb) -- semuanya angka TERSIMPAN faktur, tak dihitung ulang.
  2. Bruto potongan G dibagi pro-rata ke kelompok menurut B (sales_doc_calc.allocate: kelompok terakhir menyerap sisa) -> Σ G_i == G.
  3. Per kelompok: PPN_i = q2(G_i × T/B); harga jual_i = G_i − PPN_i  -> Σ(harga jual + PPN) == G PERSIS.
     DPP_i = q2(harga jual_i × D/H) (faktor DPP faktur itu sendiri; untuk dasar DTL). Selisih ≤ 1 sen antara PPN_i dan q2(DPP_i × tarif)
     mungkin terjadi (bruto tak selalu terjangkau rumus eksklusif) -- total dijamin, PPN mengikuti rasio faktur.
"""
from decimal import Decimal
from typing import Optional

from .sales_doc_calc import allocate, d, q2

_ZERO = Decimal("0")
ALASAN_POTONGAN = frozenset({"discount", "pricing_error"})  # == routers.credit_notes.ALASAN_POTONGAN (diuji berpasangan)


def bentuk_potongan_bruto(reason, items: list, doc_discount_percent=0, doc_discount_amount=0, header_tax_rate=0) -> bool:
    """Bentuk form potongan nominal: alasan potongan, baris nominal polos (tanpa barang/baris faktur/pajak/diskon), tanpa diskon/tarif header."""
    if reason not in ALASAN_POTONGAN or not items:
        return False
    if d(doc_discount_percent) != 0 or d(doc_discount_amount) != 0 or d(header_tax_rate) != 0:
        return False
    for it in items:
        if it.get("item_id") or it.get("original_invoice_item_id") or it.get("tax_code") or it.get("tax_code_id"):
            return False
        if d(it.get("tax_rate")) != 0 or d(it.get("discount_percent")) != 0 or d(it.get("discount_amount")) != 0:
            return False
    return True


def pecah_bruto(bruto: Decimal, kelompok: list) -> list:
    """MURNI. kelompok: [{tax_code_id, tax_rate, bruto, ppn, harga_jual, dpp}] (angka faktur). -> bagian [{tax_code_id, tax_rate,
    bruto, ppn, harga_jual, dpp}] dengan Σ bruto == bruto, harga_jual + ppn == bruto per bagian. Bagian bernilai 0 dibuang."""
    g = q2(d(bruto))
    kel = [k for k in kelompok if d(k["bruto"]) > 0]
    if g <= 0 or not kel:
        return []
    out = []
    for k, gi in zip(kel, allocate(g, [d(k["bruto"]) for k in kel])):
        if gi == 0:
            continue
        b, t, h, dp = d(k["bruto"]), d(k["ppn"]), d(k["harga_jual"]), d(k["dpp"])
        ppn = q2(gi * t / b) if t > 0 else _ZERO
        hj = gi - ppn
        dpp = q2(hj * dp / h) if (t > 0 and h > 0 and dp > 0) else hj
        out.append({"tax_code_id": k.get("tax_code_id"), "tax_rate": d(k.get("tax_rate")), "bruto": gi, "ppn": ppn,
                    "harga_jual": hj, "dpp": dpp})
    return out


async def kelompok_faktur(conn, tenant_id: str, invoice_id) -> list:
    """Komposisi pajak faktur asal dari angka TERSIMPAN (baris + ongkir). Baris lama tanpa dpp_harga_jual: harga jual = total − PPN."""
    rows = await conn.fetch(
        """SELECT sii.tax_code_id, COALESCE(sii.tax_rate, 0) AS tax_rate,
                  SUM(COALESCE(sii.dpp_harga_jual, sii.total - COALESCE(sii.tax_amount, 0))) AS harga_jual,
                  SUM(COALESCE(sii.tax_amount, 0)) AS ppn,
                  SUM(COALESCE(sii.dpp, sii.dpp_harga_jual, sii.total - COALESCE(sii.tax_amount, 0))) AS dpp
           FROM sales_invoice_items sii JOIN sales_invoices si ON si.id = sii.invoice_id
           WHERE si.tenant_id = $1 AND si.id = $2
           GROUP BY sii.tax_code_id, COALESCE(sii.tax_rate, 0)""", tenant_id, invoice_id)
    kel = {}
    for r in rows:
        kunci = (str(r["tax_code_id"]) if r["tax_code_id"] else None, d(r["tax_rate"]))
        kel[kunci] = {"tax_code_id": kunci[0], "tax_rate": kunci[1], "harga_jual": d(r["harga_jual"]), "ppn": d(r["ppn"]),
                      "dpp": d(r["dpp"])}
    s = await conn.fetchrow(
        """SELECT COALESCE(shipping_amount, 0) AS ongkir, COALESCE(shipping_tax_amount, 0) AS ppn, COALESCE(shipping_tax_rate, 0) AS rate,
                  shipping_tax_code_id, COALESCE(shipping_dpp, shipping_amount, 0) AS dpp
           FROM sales_invoices WHERE tenant_id = $1 AND id = $2""", tenant_id, invoice_id)
    if s and d(s["ongkir"]) > 0:
        rate = d(s["rate"]) if d(s["ppn"]) > 0 else _ZERO
        kode = str(s["shipping_tax_code_id"]) if s["shipping_tax_code_id"] else None
        if kode is None and rate > 0:  # ongkir null = IKUT kode barang bertarif sama (tepat satu)
            sama = [k for k in kel if k[1] == rate and k[0]]
            kode = sama[0][0] if len(sama) == 1 else None
        kunci = (kode, rate)
        g = kel.setdefault(kunci, {"tax_code_id": kode, "tax_rate": rate, "harga_jual": _ZERO, "ppn": _ZERO, "dpp": _ZERO})
        g["harga_jual"] += d(s["ongkir"]); g["ppn"] += d(s["ppn"]) if rate > 0 else _ZERO
        g["dpp"] += d(s["dpp"]) if rate > 0 else d(s["ongkir"])
    out = []
    for k in kel.values():
        k["bruto"] = k["harga_jual"] + k["ppn"]
        out.append(k)
    # urutan deterministik: kena pajak dulu (tarif turun), lalu kode
    out.sort(key=lambda k: (-k["tax_rate"], k["tax_code_id"] or ""))
    return out


async def doc_potongan_bruto(conn, tenant_id: str, invoice_id, items: list) -> Optional[dict]:
    """Dokumen NK (bentuk SAMA dgn sales_doc_calc.compute_document) untuk potongan bruto PKP, atau None bila tak berlaku
    (tenant non-PKP, faktur tanpa PPN). Satu baris pengguna -> satu baris NK per kelompok pajak faktur."""
    if not invoice_id:
        return None
    if await conn.fetchval('SELECT is_pkp FROM "Tenant" WHERE id = $1', tenant_id) is not True:
        return None
    kel = await kelompok_faktur(conn, tenant_id, invoice_id)
    if not any(k["ppn"] > 0 for k in kel):
        return None
    lines = []
    for it in items:
        g = q2(d(it.get("quantity")) * d(it.get("unit_price"))) if it.get("line_amount") is None else q2(d(it["line_amount"]))
        for b in pecah_bruto(g, kel):
            lines.append({
                "item_id": None, "item_code": it.get("item_code"), "description": it["description"], "unit": it.get("unit"),
                "quantity": Decimal("1"), "unit_price": b["harga_jual"], "discount_percent": 0, "discount_amount": _ZERO,
                "tax_code": None, "tax_code_id": b["tax_code_id"], "tax_rate": b["tax_rate"], "tax_amount": b["ppn"],
                "subtotal": b["harga_jual"], "net": b["harga_jual"], "doc_discount_allocated": _ZERO,
                "dpp_harga_jual": b["harga_jual"], "dpp": b["dpp"], "total": b["bruto"],
                "original_invoice_item_id": None, "line_amount": None, "jumlah_baris": None,
            })
    if not lines:
        return None
    hj = sum((ln["subtotal"] for ln in lines), _ZERO)
    ppn = sum((ln["tax_amount"] for ln in lines), _ZERO)
    return {"items": lines, "gross_subtotal": hj, "line_discount_total": _ZERO, "net_subtotal": hj, "doc_discount": _ZERO,
            "dpp_total": hj, "line_tax_amount": ppn, "tax_amount": ppn, "shipping_amount": _ZERO, "shipping_tax_rate": _ZERO,
            "shipping_dpp": _ZERO, "shipping_tax_amount": _ZERO, "shipping_tax_code_id_effective": None,
            "total_amount": hj + ppn, "potongan_bruto_pkp": True}


def gabung_tersimpan(rows: list) -> Optional[list]:
    """Suntingan draf TANPA baris baru (hitung ulang dari baris tersimpan): baris potongan PKP yang sudah dipecah (ada PPN) digabung
    kembali menjadi SATU baris bruto (Σ total) supaya dipecah ulang, bukan dihitung eksklusif (PPN di ATAS) -> None bila tak berlaku."""
    if not rows or any(r.get("item_id") or r.get("original_invoice_item_id") for r in rows):
        return None
    if not any(d(r.get("tax_amount")) > 0 for r in rows):
        return None
    total = sum((d(r.get("total")) for r in rows), _ZERO)
    r0 = rows[0]
    return [{"item_id": None, "item_code": r0.get("item_code"), "description": r0["description"], "quantity": Decimal("1"),
             "unit": r0.get("unit"), "unit_price": total, "discount_percent": 0, "discount_amount": _ZERO, "tax_code": None,
             "tax_code_id": None, "tax_rate": 0, "original_invoice_item_id": None, "jumlah_baris": None}]
