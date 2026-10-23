"""Faktur Pembelian -- PENENTU aksi detail (pratinjau aksi CW, 22 Okt 2026; pola po_rencana BACKEND2 / SI _rencana_void_faktur).

SATU definisi aturan per aksi, dipakai DUA jalur:
  * TULIS   : BillsService.post_bill / delete_bill / void_bill -- blok PERTAMA -> pesan galat (teks lama dipertahankan), tanpa blok -> tulis;
  * PRATINJAU: POST /bills/{id}/post|void|delete/preview -- SEMUA blok {code, message}, 200, transaksi READ ONLY tanpa kunci, payload =
             badan endpoint tulisnya. Jurnal pratinjau posting dibangun dari fungsi baris debit YANG SAMA dengan posting (hitung_debit).
Putusan pemilik LANGSUNG 22 Okt (void): ND aktif diterapkan -> BLOK (sejajar VOID_HAS_CREDIT_NOTES SI); UM vendor diterapkan -> DILEPAS
otomatis di transaksi void (sejajar SI Unit 6). Pembayaran posted -> blok (perilaku lama, kini menyebut nomor + nominal, sejajar SI).
Blok: {"code", "message"}. Semua kueri berpagar tenant."""
from decimal import Decimal
from typing import Optional

NOL = Decimal("0")


def blok(code: str, message: str) -> dict:
    return {"code": code, "message": message}


def _rp(v) -> str:
    from .teks_galat import rp
    try:
        return rp(v)
    except Exception:
        return f"Rp{v}"


async def periode_tertutup(conn, tid: str, tanggal) -> Optional[str]:
    """Status periode fiskal yang memuat `tanggal` bila BUKAN OPEN (Law 5), else None (pola unapply UM vendor BACKEND2)."""
    r = await conn.fetchrow("SELECT status FROM fiscal_periods WHERE tenant_id = $1 AND start_date <= $2 AND end_date >= $2", tid, tanggal)
    return r["status"] if r and r["status"] != "OPEN" else None


# ---------------------------------------------------------------- GRN 3-way match (flag penerimaan_barang_v1; kontrak grn_tagih BACKEND2,
# KONTRAK-grn-20261023 §3; putusan pemilik P1-P6 22 Okt). Flag MATI -> rencana kosong -> posting/pratinjau BYTE-IDENTIK dgn hari ini.
def neto_baris(rows, subtotal_dec: Decimal) -> dict:
    """{bill_item_id: nilai neto SEMUA diskon tanpa PPN} = bi.total - bagian diskon KEPALA (invoice + tunai) proporsional nilai baris (2dp
    HALF_UP, baris terakhir bernilai menyerap sisa) -> Σ == subtotal_dec (dasar jurnal). SATU sumber nilai_neto untuk grn_tagih (review BACKEND)."""
    from decimal import ROUND_HALF_UP
    net = [(r["id"], Decimal(str(r["net"] or 0))) for r in rows]
    jumlah = sum((n for _, n in net), NOL)
    kepala = jumlah - subtotal_dec
    if jumlah == NOL or kepala == NOL:
        return {i: n for i, n in net}
    out, terbagi = {}, NOL
    terakhir = max((k for k, (_, n) in enumerate(net) if n != NOL), default=len(net) - 1)
    for k, (i, n) in enumerate(net):
        bagian = (kepala - terbagi) if k == terakhir else (kepala * n / jumlah).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        terbagi += bagian if k != terakhir else NOL
        out[i] = n - bagian
    return out


async def neto_tagihan_terposting(conn, tid: str, bill_ids) -> dict:
    """{bill_item_id: Decimal} nilai NETO (B) baris tagihan yang SUDAH diposting -- hasil YANG SAMA dengan saat posting: neto_baris atas
    baris urut line_number dengan subtotal_dec = grand_total - tax_amount TERSIMPAN (== post_bill / rencana_posting; header beku Law 19
    sesudah posting). Untuk sisi PENERIMAAN aturan D GRN (BACKEND2, pasangan 'tagih_dulu'). Baca saja, TANPA kunci (pemanggil memegang
    kunci PO). Tagihan draf / void / bukan milik tenant -> dilewati (tak ada di hasil)."""
    out = {}
    for b in await conn.fetch(
            """SELECT id, grand_total, tax_amount FROM bills
               WHERE tenant_id = $1 AND id = ANY($2::uuid[]) AND status_v2 NOT IN ('draft', 'void')""", tid, list(bill_ids)):
        rows = await conn.fetch(
            "SELECT bi.id, COALESCE(bi.total, 0) AS net FROM bill_items bi WHERE bi.bill_id = $1 ORDER BY bi.line_number", b["id"])
        out.update(neto_baris(rows, Decimal(str(b["grand_total"] or 0)) - Decimal(str(b["tax_amount"] or 0))))
    return out


async def rencana_grn(conn, tid: str, bill_id, subtotal_dec: Decimal, kunci: bool = False) -> dict:
    """-> {rencana: {bill_item_id: butir}, blocks: [...], akun: {grni, ppv}|None}. Tulis (kunci=True): PO terkait dikunci FOR UPDATE SESUDAH
    tagihan (urutan bill -> PO; jalur GRN tak pernah mengunci bill, BACKEND2 23 Okt). Blok: butir.blok (SATUAN_TAK_TERKONVERSI) ·
    GRN_MELEBIHI_TERIMA (baris stok ber-GRN terbuka ditagih > diterima - tertagih, 7c ketat) · BONUS_GRN_BELUM (qty bonus atas baris ber-GRN)."""
    rows = await conn.fetch(
        """SELECT bi.id, bi.quantity, bi.unit, bi.subtotal, bi.discount_amount, COALESCE(bi.total, 0) AS net, bi.purchase_order_item_id,
                  COALESCE(bi.bonus_qty, 0) AS bonus, poi.unit AS po_unit, poi.po_id
           FROM bill_items bi LEFT JOIN purchase_order_items poi ON poi.id = bi.purchase_order_item_id
           WHERE bi.bill_id = $1 ORDER BY bi.line_number""", bill_id)
    kosong = {"rencana": {}, "blocks": [], "akun": None}
    if not any(r["purchase_order_item_id"] for r in rows):
        return kosong
    if kunci:
        for po in sorted({r["po_id"] for r in rows if r["po_id"]}, key=str):
            await conn.fetchrow("SELECT id FROM purchase_orders WHERE id = $1 AND tenant_id = $2 FOR UPDATE", po, tid)
    from . import grn_tagih as GT
    neto = neto_baris(rows, subtotal_dec)
    sama = lambda r: not r["unit"] or not r["po_unit"] or r["unit"].strip().lower() == r["po_unit"].strip().lower()  # noqa: E731
    items = [{"id": r["id"], "quantity": r["quantity"], "unit": r["unit"], "subtotal": r["subtotal"], "discount_amount": r["discount_amount"],
              "purchase_order_item_id": r["purchase_order_item_id"], "nilai_neto": neto[r["id"]],
              "qty_dasar": r["quantity"] if sama(r) else None} for r in rows]
    rencana = await GT.rencana_posting_baris(conn, tid, items)
    if not rencana:
        return kosong
    bonus = {r["id"]: Decimal(str(r["bonus"])) for r in rows}
    bloks = []
    for b in rencana:
        if b.get("blok"):
            bloks.append(blok(b["blok"]["code"], b["blok"]["message"]))
        elif b["qty_inbound"] > NOL:
            bloks.append(blok("GRN_MELEBIHI_TERIMA", "Jumlah ditagih melebihi barang yang sudah diterima dan belum ditagih. "
                                                     "Catat penerimaan barangnya dulu atau kurangi jumlah tagihan."))
        elif bonus.get(b["bill_item_id"], NOL) > NOL:
            bloks.append(blok("BONUS_GRN_BELUM", "Qty bonus pada baris yang sudah diterima lewat penerimaan barang belum didukung."))
    from .role_resolver import AccountRole, resolve_account_id_by_role
    akun = {"grni": await resolve_account_id_by_role(conn, tid, AccountRole.GOODS_RECEIVED_NOT_INVOICED),
            "ppv": await resolve_account_id_by_role(conn, tid, AccountRole.PURCHASE_PRICE_VARIANCE)}
    return {"rencana": {b["bill_item_id"]: b for b in rencana}, "blocks": bloks, "akun": akun}


# ---------------------------------------------------------------- POSTING
async def hitung_debit(conn, tid: str, bill_id, subtotal_dec: Decimal, default_inv_acct_id, grn: Optional[dict] = None) -> list:
    """Baris debit tagihan PER-BARIS (diangkat APA ADANYA dari BillsService._emit_bill_debit_lines; SATU fungsi untuk posting & pratinjau):
    goods+track -> Persediaan; service -> COGS_SERVICE; lainnya -> products.purchase_account_id bila ada, else COGS_SALES. Peran tak
    terpetakan -> HTTPException 422 ACCOUNT_DEFAULT_UNMAPPED. Residu -> sink default (Law 4). -> [(account_id, jumlah, is_inv)] urut, tanpa nol."""
    from fastapi import HTTPException
    from .inventory_helpers import resolve_inventory_accounts
    from .role_resolver import AccountRole
    rows = await conn.fetch(
        "SELECT bi.id, bi.product_id, COALESCE(bi.total, 0) AS net, p.item_type, p.track_inventory, p.purchase_account_id "
        "FROM bill_items bi LEFT JOIN products p ON p.id = bi.product_id WHERE bi.bill_id = $1 ORDER BY bi.line_number", bill_id)
    # GRN (rencana_grn): baris ber-GRN -> Dr GRNI nilai terima + selisih harga ke PPV (debit +, kredit -), BUKAN Persediaan; grn None = lama.
    rencana_grn_, akun_grn = (grn or {}).get("rencana") or {}, (grn or {}).get("akun") or {}
    tot, is_inv, urut = {}, {}, []

    def _acc(aid, amt, inv):
        if aid not in tot:
            tot[aid] = NOL
            is_inv[aid] = inv
            urut.append(aid)
        tot[aid] += amt

    for dr in rows:
        net = Decimal(str(dr["net"] or 0))
        pid = dr["product_id"]
        if dr["id"] in rencana_grn_:
            b = rencana_grn_[dr["id"]]
            _acc(akun_grn["grni"], b["nilai_grni"], True)
            _acc(akun_grn["ppv"], b["selisih"], False)
            continue
        if dr["item_type"] == "goods" and dr["track_inventory"]:
            _acc((await resolve_inventory_accounts(conn, tid, pid))["inventory_account_id"], net, True)
            continue
        ov = dr["purchase_account_id"] if pid is not None else None
        if ov:
            _acc(ov, net, False)
            continue
        role = AccountRole.COGS_SERVICE if dr["item_type"] == "service" else AccountRole.COGS_SALES
        acct = await conn.fetchval("SELECT account_id FROM account_roles WHERE tenant_id = $1 AND role_key = $2", tid, role)
        if not acct:
            raise HTTPException(status_code=422, detail={
                "code": "ACCOUNT_DEFAULT_UNMAPPED", "message": "Akun default pembelian belum diatur. Atur di Pengaturan -> Akun Default."})
        _acc(acct, net, False)
    residu = subtotal_dec - sum(tot.values(), NOL)
    if residu != NOL:
        if rencana_grn_ and default_inv_acct_id not in tot:
            _acc(akun_grn["ppv"], residu, False)  # GRN: sisa sen diskon kepala ke selisih harga, BUKAN ke GRNI (saldo GRNI tetap tepat)
        else:
            _acc(default_inv_acct_id if default_inv_acct_id in tot else (urut[0] if urut else default_inv_acct_id), residu, True)
    return [(aid, tot[aid], is_inv.get(aid)) for aid in urut if tot[aid] != NOL]


def memo_debit(aid, inv, vendor_name: str, grn: Optional[dict]) -> str:
    """Memo baris debit (SATU sumber posting & pratinjau). GRN: akun GRNI / selisih harga bermemo sendiri."""
    akun = (grn or {}).get("akun") or {}
    if grn and grn.get("rencana") and aid == akun.get("grni"):
        return f"Barang diterima belum ditagih - {vendor_name}"
    if grn and grn.get("rencana") and aid == akun.get("ppv"):
        return f"Selisih harga pembelian - {vendor_name}"
    return f"Pembelian - {vendor_name}" if inv else f"Pembelian jasa/beban - {vendor_name}"


async def blok_posting(conn, tid: str, bill) -> tuple:
    """-> (blocks, akun{ap, debit, vat, debit_role}). Urutan = pemeriksaan post_bill. bill = baris bills (status_v2, issue_date, tax_amount, id)."""
    from .role_resolver import PESAN_NON_PKP_PEMBELIAN, AccountRole, resolve_account_id_by_role, resolve_account_id_by_role_if_pkp
    bloks = []
    if bill["status_v2"] != "draft":
        bloks.append(blok("BUKAN_DRAF", f"Cannot post bill with status '{bill['status_v2']}'. Only draft bills can be posted."))
    st = await periode_tertutup(conn, tid, bill["issue_date"])
    if st:
        bloks.append(blok("PERIODE_TERTUTUP", f"Periode akuntansi untuk tanggal faktur sudah {st}."))
    sc = await conn.fetchval("SELECT EXISTS(SELECT 1 FROM production_subcontracts WHERE bill_id = $1)", bill["id"])
    debit_role = AccountRole.WIP_SUBCONTRACT if sc else AccountRole.INVENTORY_MERCHANDISE
    ap = await resolve_account_id_by_role(conn, tid, AccountRole.AP_TRADE)
    debit = await resolve_account_id_by_role(conn, tid, debit_role)
    vat = None
    if Decimal(str(bill["tax_amount"] or 0)) > 0:
        vat = await resolve_account_id_by_role_if_pkp(conn, tid, AccountRole.VAT_INPUT)
        if vat is None:
            bloks.append(blok("NON_PKP_PPN", PESAN_NON_PKP_PEMBELIAN))
    if not ap:
        bloks.append(blok("AKUN_AP_HILANG", "Akun AP_TRADE tidak ter-resolve"))
    if not debit:
        bloks.append(blok("AKUN_DEBIT_HILANG", f"Akun debit ({debit_role} / INVENTORY_MERCHANDISE) tidak ter-resolve"))
    return bloks, {"ap": ap, "debit": debit, "vat": vat, "debit_role": debit_role}


async def rencana_posting(conn, tid: str, bill_id) -> Optional[dict]:
    """Pratinjau POSTING: blok + jurnal yang AKAN dibuat (baris SAMA dengan post_bill: debit per baris via hitung_debit, PPN Masukan, Cr Utang).
    None = tagihan tak ada di tenant ini."""
    from fastapi import HTTPException
    b = await conn.fetchrow(
        """SELECT id, status_v2, invoice_number, vendor_name, issue_date, grand_total, tax_amount FROM bills WHERE id = $1 AND tenant_id = $2""",
        bill_id, tid)
    if not b:
        return None
    bloks, akun = await blok_posting(conn, tid, b)
    total = Decimal(str(b["grand_total"] or 0))
    pajak = Decimal(str(b["tax_amount"] or 0))
    grn = await rencana_grn(conn, tid, b["id"], total - pajak)  # GRN: penentu SAMA dgn post_bill (tanpa kunci); flag mati -> kosong
    bloks += grn["blocks"]
    baris = []
    if akun["debit"]:
        try:
            for aid, amt, inv in await hitung_debit(conn, tid, b["id"], total - pajak, akun["debit"], grn if grn["rencana"] else None):
                baris.append({"account_id": aid, "debit": amt if amt > NOL else NOL, "credit": -amt if amt < NOL else NOL,
                              "memo": memo_debit(aid, inv, b["vendor_name"], grn)})
        except HTTPException as e:
            d = e.detail if isinstance(e.detail, dict) else {"code": "AKUN_DEBIT_HILANG", "message": str(e.detail)}
            bloks.append(blok(d.get("code", "ACCOUNT_DEFAULT_UNMAPPED"), d.get("message", "")))
    if pajak > 0 and akun["vat"]:
        baris.append({"account_id": akun["vat"], "debit": pajak, "credit": NOL, "memo": "PPN Masukan"})
    if akun["ap"]:
        baris.append({"account_id": akun["ap"], "debit": NOL, "credit": total, "memo": f"Hutang ke {b['vendor_name']}"})
    return {"bill": b, "blocks": bloks, "baris": await _bernama(conn, tid, baris), "grand_total": total, "tax_amount": pajak}


async def _bernama(conn, tid: str, baris: list) -> list:
    ids = list({x["account_id"] for x in baris if x["account_id"]})
    nama = {r["id"]: (r["account_code"], r["name"]) for r in await conn.fetch(
        "SELECT id, account_code, name FROM chart_of_accounts WHERE tenant_id = $1 AND id = ANY($2::uuid[])", tid, ids)} if ids else {}
    return [{"account_id": str(x["account_id"]), "account_code": nama.get(x["account_id"], (None, None))[0],
             "account_name": nama.get(x["account_id"], (None, None))[1], "debit": f"{Decimal(str(x['debit'])):.2f}",
             "credit": f"{Decimal(str(x['credit'])):.2f}", "memo": x["memo"]} for x in baris]


# ---------------------------------------------------------------- HAPUS (draf)
def blok_hapus(bill, ada_ref: Optional[str]) -> list:
    """Aturan delete_bill (teks lama). ada_ref = label referensi non-cascade pertama yang ada (atau None)."""
    from .status_helpers import derive_doc_status
    ds = derive_doc_status(dict(bill))
    if ds == "void":
        return [blok("SUDAH_BATAL", "Cannot delete voided bill")]
    bloks = []
    if ds != "draft":
        bloks.append(blok("BUKAN_DRAF", f"Cannot delete bill with status '{ds}'. Only draft bills can be deleted. Use void instead."))
    if (bill["amount_paid"] or 0) > 0:
        bloks.append(blok("ADA_PEMBAYARAN", "Cannot delete bill with payments. Void the bill instead."))
    if ada_ref:
        bloks.append(blok("ADA_REFERENSI", f"Tidak dapat menghapus faktur draft: masih ada referensi {ada_ref}. Lepas referensi terlebih dahulu."))
    return bloks


REF_HAPUS = (("bill_payment_allocations", "pembayaran tagihan"), ("vendor_deposit_applications", "aplikasi deposit vendor"),
             ("fixed_assets", "aset tetap"), ("asset_maintenance", "pemeliharaan aset"), ("production_subcontracts", "subkontrak produksi"))


async def ref_hapus(conn, bill_id) -> Optional[str]:
    for tabel, label in REF_HAPUS:
        if await conn.fetchval(f"SELECT 1 FROM {tabel} WHERE bill_id = $1 LIMIT 1", bill_id):  # nosec B608 (literal)
            return label
    return None


# ---------------------------------------------------------------- VOID
async def blok_void(conn, tid: str, bill) -> list:
    """Aturan void_bill. Pembayaran posted (jurnal BP efektif) -> blok sebut nomor + nominal; ND aktif diterapkan -> blok (putusan pemilik)."""
    if bill["status"] == "void" or (bill.get("status_v2") or "") == "void":
        return [blok("SUDAH_BATAL", "Bill is already voided")]
    bloks = []
    bayar = await conn.fetch(
        """SELECT bp.payment_number, SUM(bpa.amount_applied) AS jumlah
           FROM bill_payment_allocations bpa JOIN bill_payments_v2 bp ON bp.id = bpa.payment_id AND bp.tenant_id = $2
           JOIN journal_entries je ON je.id = bp.journal_id AND je.status = 'POSTED' AND je.reversed_by_id IS NULL
           WHERE bpa.bill_id = $1 AND bp.status = 'posted' GROUP BY bp.payment_number ORDER BY bp.payment_number""", bill["id"], tid)
    if bayar:
        rinci = ", ".join(f"{r['payment_number']} {_rp(r['jumlah'])}" for r in bayar)
        bloks.append(blok("ADA_PEMBAYARAN", f"Faktur pembelian {bill['invoice_number']} sudah dibayar ({rinci}). "
                                            "Batalkan pembayaran itu dulu, lalu batalkan fakturnya."))
    nd = await conn.fetch(
        """SELECT vc.credit_number FROM vendor_credit_applications vca JOIN vendor_credits vc ON vc.id = vca.vendor_credit_id AND vc.tenant_id = $2
           WHERE vca.tenant_id = $2 AND vca.bill_id = $1 AND vca.status = 'active' ORDER BY vc.credit_number""", bill["id"], tid)
    if nd:
        bloks.append(blok("ADA_NOTA_DEBIT", f"Faktur pembelian {bill['invoice_number']} punya nota debit terkait ("
                                            + ", ".join(r["credit_number"] for r in nd) + "). Lepas nota debit itu dulu, lalu batalkan fakturnya."))
    return bloks


async def um_aktif(conn, tid: str, bill_id) -> list:
    """Penerapan UM vendor AKTIF atas tagihan ini (dilepas otomatis saat void; putusan pemilik 22 Okt). Urut deposit_id (urutan kunci)."""
    return [dict(r) for r in await conn.fetch(
        """SELECT a.id AS application_id, a.vendor_deposit_id AS deposit_id, a.amount, vd.deposit_number
           FROM vendor_deposit_applications a JOIN vendor_deposits vd ON vd.id = a.vendor_deposit_id AND vd.tenant_id = $2
           WHERE a.bill_id = $1 AND a.status = 'active' ORDER BY a.vendor_deposit_id, a.id""", bill_id, tid)]


async def rencana_void(conn, tid: str, bill_id) -> Optional[dict]:
    """Pratinjau VOID: blok + jurnal pembalik (cermin jurnal BILL asli yang efektif) + akibat (UM dilepas, stok, PO). None = tak ada."""
    b = await conn.fetchrow(
        """SELECT id, invoice_number, status, status_v2, journal_id, grand_total FROM bills WHERE id = $1 AND tenant_id = $2""", bill_id, tid)
    if not b:
        return None
    bloks = await blok_void(conn, tid, b)
    baris = []
    if b["journal_id"]:
        je = await conn.fetchrow("SELECT id, status, reversed_by_id FROM journal_entries WHERE id = $1 AND tenant_id = $2", b["journal_id"], tid)
        if je and je["status"] == "POSTED" and not je["reversed_by_id"]:
            baris = [{"account_id": r["account_id"], "debit": r["credit"], "credit": r["debit"], "memo": f"Pembalik - {r['memo'] or ''}"}
                     for r in await conn.fetch("SELECT account_id, debit, credit, memo FROM journal_lines WHERE journal_id = $1 ORDER BY line_number",
                                               je["id"])]
    um = await um_aktif(conn, tid, b["id"])
    akibat = []
    if um:
        akibat.append("Uang muka dilepas otomatis: " + ", ".join(f"{u['deposit_number']} {_rp(u['amount'])}" for u in um))
    if await conn.fetchval("SELECT EXISTS(SELECT 1 FROM inventory_ledger WHERE tenant_id = $1 AND source_id = $2)", tid, b["id"]):
        akibat.append("Stok barang terlacak dikembalikan (keluar dari persediaan).")
    from .po_daftar import po_terkait_bill
    if await po_terkait_bill(conn, tid, b["id"]):
        akibat.append("Jumlah tertagih pesanan pembelian asal dipulihkan.")
    return {"bill": b, "blocks": bloks, "baris": await _bernama(conn, tid, baris), "uang_muka_dilepas": um, "akibat": akibat}
