"""Faktur Pembelian -- PENENTU hitung + blok (kontrak pratinjau form tahap 2, 22 Okt 2026, putusan MASTER; pola services/po_rencana BACKEND2).

SATU definisi, dipakai:
  * TULIS  : BillsService.create_bill_v2 (POST /bills/v2) dan update_bill_v2 (PATCH /bills/v2/{id}) -- blok pertama -> pesan galat lama,
             angka header + per baris dari `hitung` (tak ada kalkulator kedua);
  * PRATINJAU: POST /bills/calculate dan POST /bills/{id}/edit/preview -- SEMUA blok {code, message}, 200, transaksi READ ONLY tanpa kunci,
             `payload` = badan endpoint tulisnya.

Aturan hitung (TIDAK diubah; diangkat apa adanya dari create/update v2 supaya /calculate berhenti menyimpang):
  1. ada baris ber-tax_rate > 0 -> tarif pajak KEPALA = 0 (hindari dobel); selain itu tarif kepala = request.tax_rate.
  2. BillCalculator.calculate (diskon baris -> diskon faktur -> diskon tunai -> DPP faktor kode pajak -> pajak kepala).
  3. per baris: neto (sesudah diskon baris) − bagian diskon kepala (_bill_line_allocs 3(b2), hanya bila pajak per baris & tanpa dpp_manual)
     -> _bill_line_tax (faktor DPP kode pajak baris, PPN 2dp HALF_UP).
  4. Σ PPN baris > 0 -> tax_amount = Σ PPN baris dan grand_total = grand_total langkah 2 (pajak kepala 0, jadi = neto sesudah semua diskon)
     + Σ PPN baris. Jadi PPN per baris IKUT grand_total (== create/update v2 sejak 3(b)).
Blok: {"code", "message"}; pesan = teks galat create/update v2 yang sudah ada (FE/chat mencocokkan teks)."""
from decimal import Decimal
from typing import Optional

NOL = Decimal("0")


def blok(code: str, message: str) -> dict:
    return {"code": code, "message": message}


async def blok_buat(conn, tenant_id: str, req: dict) -> list:
    """Blok POST /bills/v2 (urutan = urutan pemeriksaan create_bill_v2). vendor_name tanpa vendor_id = vendor baru dibuat saat tulis (bukan blok)."""
    bloks = []
    vid = req.get("vendor_id")
    if vid:
        if not await conn.fetchval("SELECT 1 FROM vendors WHERE id = $1 AND tenant_id = $2", vid, tenant_id):
            bloks.append(blok("VENDOR_TIDAK_ADA", f"Vendor with ID {vid} not found"))
    elif not req.get("vendor_name"):
        bloks.append(blok("VENDOR_WAJIB", "Either vendor_id or vendor_name is required"))
    if not req.get("items"):
        bloks.append(blok("ITEM_KOSONG", "Minimal satu item harus diisi"))
    if not req.get("due_date"):
        bloks.append(blok("JATUH_TEMPO_WAJIB", "Tanggal jatuh tempo (due_date) wajib diisi"))
    bloks += blok_baris(req.get("items") or [])
    return bloks


def blok_baris(items: list) -> list:
    """qty/price wajib + angka (teks galat lama create_bill_v2; skema pydantic sudah menjaga jalur HTTP, ini untuk pemanggil dict/chat)."""
    bloks = []
    for idx, it in enumerate(items, start=1):
        if it.get("qty") is None:
            bloks.append(blok("ITEM_QTY_WAJIB", f"Item {idx}: qty wajib diisi"))
        elif it.get("price") is None:
            bloks.append(blok("ITEM_HARGA_WAJIB", f"Item {idx}: price wajib diisi"))
        else:
            try:
                Decimal(str(it["qty"])) + Decimal(str(it["price"]))
            except Exception:
                bloks.append(blok("ITEM_BUKAN_ANGKA", f"Item {idx}: qty dan price harus berupa angka"))
    return bloks


async def blok_ubah(conn, tenant_id: str, bill, req: dict) -> list:
    """Blok PATCH /bills/v2/{id}: hanya draf; vendor_id baru wajib milik tenant (Law 24; dulu id asing tetap ditulis); due_date eksplisit
    kosong ditolak; items eksplisit kosong ditolak."""
    bloks = []
    st = bill["status_v2"]
    if st != "draft":
        bloks.append(blok("BUKAN_DRAF", f"Cannot edit bill with status '{st}'. Only draft bills can be edited."))
    vid = req.get("vendor_id")
    if vid and str(vid) != str(bill["vendor_id"]) and not await conn.fetchval(
            "SELECT 1 FROM vendors WHERE id = $1 AND tenant_id = $2", vid, tenant_id):
        bloks.append(blok("VENDOR_TIDAK_ADA", f"Vendor with ID {vid} not found"))
    if "due_date" in req and not req["due_date"]:
        bloks.append(blok("JATUH_TEMPO_WAJIB", "Tanggal jatuh tempo (due_date) wajib diisi"))
    if "items" in req and req["items"] is not None and len(req["items"]) == 0:
        bloks.append(blok("ITEM_KOSONG", "Minimal satu item harus diisi"))
    bloks += blok_baris(req.get("items") or [])
    return bloks


async def hitung(conn, tenant_id: str, req: dict) -> dict:
    """-> {header (BillCalculator, SEBELUM Σ PPN baris; dipakai INSERT kepala lama), baris[{item_calc, alloc, dpp, ppn}], akhir {tax_amount,
    grand_total} (SESUDAH Σ PPN baris = nilai tersimpan), pajak_per_baris}. Satu-satunya kalkulator create/update/calculate/edit-preview."""
    from .bills_service import BillCalculator, _bill_line_allocs, _bill_line_tax
    from .tax_factor import resolve_dpp_factor
    items = req.get("items") or []
    pajak_baris = any(i.get("tax_rate") and float(i.get("tax_rate", 0)) > 0 for i in items)
    tarif_kepala = 0 if pajak_baris else req.get("tax_rate", 0)
    faktor = await resolve_dpp_factor(conn, tenant_id, req.get("tax_code_id"), tarif_kepala, "input")
    header = BillCalculator.calculate(
        items=items,
        invoice_discount_percent=Decimal(str(req.get("invoice_discount_percent", 0) or 0)),
        invoice_discount_amount=req.get("invoice_discount_amount", 0) or 0,
        cash_discount_percent=Decimal(str(req.get("cash_discount_percent", 0) or 0)),
        cash_discount_amount=req.get("cash_discount_amount", 0) or 0,
        tax_rate=tarif_kepala,
        dpp_manual=req.get("dpp_manual"),
        dpp_factor=faktor,
    )
    allocs = _bill_line_allocs(items, header, req, pajak_baris)
    baris, total_ppn = [], NOL
    for idx, it in enumerate(items):
        ic = BillCalculator.calculate_item_total(Decimal(str(it["qty"])), Decimal(str(it["price"])), Decimal(str(it.get("discount_percent", 0) or 0)))
        dpp, ppn = await _bill_line_tax(conn, tenant_id, ic, it.get("tax_code_id"), float(it.get("tax_rate") or 0), None, allocs[idx])
        baris.append({"item_calc": ic, "alloc": allocs[idx], "dpp": dpp, "ppn": ppn})
        total_ppn += Decimal(str(ppn))
    akhir = {"tax_amount": header["tax_amount"], "grand_total": header["grand_total"]}
    if total_ppn > 0:  # == langkah 6b create/update v2 (Σ tax_amount bill_items)
        akhir = {"tax_amount": float(total_ppn), "grand_total": float(header["grand_total"]) + float(total_ppn)}
    return {"header": header, "baris": baris, "akhir": akhir, "pajak_per_baris": pajak_baris}


def _r(v) -> float:
    return float(Decimal(str(v or 0)).quantize(Decimal("0.01")))


def hitung_json(req: dict, h: dict) -> dict:
    """Bentuk respons pratinjau (angka float 2dp, sejajar PO /calculate): kepala == nilai yang AKAN tersimpan, per baris."""
    hd = h["header"]
    sub, tax, tot = _r(hd["subtotal"]), _r(h["akhir"]["tax_amount"]), _r(h["akhir"]["grand_total"])
    disk = _r(Decimal(str(_r(hd["item_discount_total"]))) + Decimal(str(_r(hd["invoice_discount_total"]))) + Decimal(str(_r(hd["cash_discount_total"]))))
    return {
        # Ringkasan pembaca bersama SO/PO (WORKSPACE 22 Okt): subtotal - discount_amount + tax_amount + adjustment == total_amount PERSIS.
        # adjustment = 0 kecuali dpp_manual (total = DPP manual + pajak) atau selisih pembulatan sen diskon faktur % -> FE tampilkan baris terpisah.
        "discount_amount": disk, "total_amount": tot,
        "adjustment": _r(Decimal(str(tot)) - (Decimal(str(sub)) - Decimal(str(disk)) + Decimal(str(tax)))),
        "subtotal": _r(hd["subtotal"]), "item_discount_total": _r(hd["item_discount_total"]),
        "invoice_discount_total": _r(hd["invoice_discount_total"]), "cash_discount_total": _r(hd["cash_discount_total"]),
        "dpp": _r(hd["dpp"]), "tax_amount": _r(h["akhir"]["tax_amount"]), "grand_total": _r(h["akhir"]["grand_total"]),
        "pajak_per_baris": h["pajak_per_baris"],
        "items": [{"line_number": n, "product_name": it.get("product_name"), "qty": _r(it.get("qty")), "price": _r(it.get("price")),
                   "discount_percent": float(it.get("discount_percent") or 0), "tax_rate": float(it.get("tax_rate") or 0),
                   "subtotal": _r(b["item_calc"]["subtotal"]), "discount_amount": _r(b["item_calc"]["discount_amount"]),
                   "total": _r(b["item_calc"]["total"]), "header_discount_share": _r(b["alloc"]), "dpp": _r(b["dpp"]), "ppn": _r(b["ppn"]),
                   "total_with_ppn": _r(Decimal(str(b["item_calc"]["total"])) - Decimal(str(b["alloc"] or 0)) + Decimal(str(b["ppn"])))}
                  for n, (it, b) in enumerate(zip(req.get("items") or [], h["baris"]), start=1)],
    }
