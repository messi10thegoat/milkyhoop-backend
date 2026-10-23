"""Penerimaan Barang (GRN, PEMBELIAN-SERAGAM T3) -- penentu + penulis + void, DI BALIK flag tenant `penerimaan_barang_v1`.

Rancangan & putusan pemilik LANGSUNG 22 Okt (backend/docs/RANCANGAN-penerimaan-barang-grn-20261022.md §6, §7):
  P1 baris BER-STOK (products.item_type='goods' AND track_inventory): stok masuk SAAT TERIMA (inventory_helpers.record_inventory_inbound,
     WAC Rule 3, source GOODS_RECEIPT) + jurnal Dr Persediaan (akun produk -> INVENTORY_MERCHANDISE) / Cr Barang Diterima Belum Ditagih
     (peran GOODS_RECEIVED_NOT_INVOICED, 2-10150 LIABILITY -- BUKAN hutang vendor). Nilai = qty x harga satuan PO NETO diskon baris, TANPA
     PPN (PPN Masukan diakui di tagihan). Diskon header PO tidak dialokasikan (selisihnya jatuh ke Selisih Harga Pembelian di tagihan).
  P2 baris non-stok/jasa: dokumen + qty saja, TANPA jurnal (grapgrap seluruhnya non_inventory -> nol jurnal GRN).
  P4 penghitung lama tak di-backfill; P6 flag mati = /receive persis perilaku lama (tanpa dokumen).
Iron Laws: 2/26 (void = pembalik, asli tetap POSTED + reversed_by_id), 4 (Σ debit == Σ kredit, diverifikasi _jurnal), 5 (periode terbuka),
6 (source_type GOODS_RECEIPT* terdaftar V452), 13 (PO dikunci FOR UPDATE -- urutan kunci SAMA dengan /receive), 14 (idempotensi di rute),
20/23 (DRAFT -> POSTED dalam transaksi pemanggil), 24 (tenant_id eksplisit), 27 (akun lewat peran runtime). Inventory Rule 1/3/9.
Penentu = SATU fungsi untuk pratinjau (kunci=False, read-only) dan tulis (kunci=True).

ATURAN (D) (putusan LANGSUNG pemilik 23 Okt, pola SAP GR/IR, kontrak BACKEND): urutan bebas. Tagihan atas baris PO ber-stok yang datang LEBIH
DULU = Dr GRNI sebesar B (neto tagihan) tanpa stok ("tertunda"). Penerimaan sesudahnya MEMASANG diri ke tertunda (FIFO, grn_pasang.pasangkan):
stok masuk qty penuh @ harga terima R; jurnal Dr Persediaan R / Cr GRNI (R bebas + Σ B) / selisih Σ(B - R) ke Selisih Harga Pembelian;
tautan kind 'tagih_dulu' (grni_amount = R, nilai_tagih = B). Tagihan LAMA (kartu stok BILL, sebelum flag) = 'legacy': stok SUDAH masuk ->
baris GRN terpisah ber_stok=false + tautan 'legacy' tanpa stok/jurnal. Void simetris: dokumen yang LEBIH DULU tak bisa dibatalkan selama
tercocok (GRN: tautan 'terima_dulu'); GRN yang datang sesudah tagihan BOLEH dibatalkan -> tautan 'tagih_dulu'/'legacy' dibalik, tagihan
kembali tertunda."""
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional
from uuid import UUID

from fastapi import HTTPException

NOL = Decimal("0")
SEN = Decimal("0.01")
FLAG = "penerimaan_barang_v1"
SUMBER = "GOODS_RECEIPT"
SUMBER_BALIK = "GOODS_RECEIPT_REVERSAL"
# Nomor: dokumen GRN-YYMM-NNNN; jurnal GRJ-... / pembalik GRJ-REV-... (awalan LITERAL di pemanggil -- penjaga test_nomor_jurnal_satu_ruang)


def _d(v) -> Decimal:
    return Decimal(str(v)) if v is not None else NOL


def r2(v: Decimal) -> Decimal:
    return v.quantize(SEN, rounding=ROUND_HALF_UP)


def blok(code: str, message: str, status: int = 400) -> dict:
    return {"code": code, "message": message, "status": status}


async def aktif(conn, tenant_id: str) -> bool:
    return bool(await conn.fetchval(
        "SELECT 1 FROM tenant_features WHERE tenant_id = $1 AND feature = $2 AND enabled", tenant_id, FLAG))


def ber_stok(item_type, track_inventory) -> bool:
    """Baris yang menggerakkan stok + jurnal GRNI (P1); selain itu dokumen saja (P2)."""
    return item_type == "goods" and bool(track_inventory)


def harga_satuan_terima(po_item) -> Decimal:
    """Harga satuan PO NETO diskon baris, tanpa PPN: (subtotal - diskon baris) / qty (presisi penuh; nilai baris dibulatkan 2dp)."""
    q = _d(po_item["quantity"])
    if q <= NOL:
        return NOL
    return (_d(po_item["subtotal"]) - _d(po_item["discount_amount"])) / q


async def _periode_terbuka(conn, tenant_id: str, tanggal) -> Optional[str]:
    from .sj_so import _periode_terbuka as periode
    return await periode(conn, tenant_id, tanggal)


async def _gudang(conn, tenant_id: str, diminta, po_gudang):
    """-> baris gudang (id, name, is_active) atau None: diminta -> gudang PO -> gudang bawaan tenant (pola /fulfill)."""
    for kandidat in (diminta, po_gudang):
        if kandidat is not None:
            return await conn.fetchrow("SELECT id, name, is_active FROM warehouses WHERE id = $1 AND tenant_id = $2", kandidat, tenant_id)
    return await conn.fetchrow(
        "SELECT id, name, is_active FROM warehouses WHERE tenant_id = $1 AND is_active ORDER BY is_default DESC NULLS LAST, created_at LIMIT 1",
        tenant_id)


# Tagihan tertunda (D): baris tagihan TERPOSTING atas baris PO ber-stok yang qty-nya belum habis tertaut (tautan aktif jenis apa pun).
# FIFO = jurnal tagihan terlama. legacy = kartu stok BILL masuk untuk produk itu (predikat BACKEND 23 Okt); produk sama > 1 baris di tagihan
# legacy = ambigu -> blok (tak ditebak). Dipakai penerimaan (rencana_grn) DAN penjaga #1 (Σ nilai_terbuka) -- SATU sumber.
SQL_TERTUNDA = """
    SELECT bi.id AS bill_item_id, bi.bill_id, bi.purchase_order_item_id, bi.quantity, bi.unit, poi.unit AS satuan_po,
           COALESCE((SELECT SUM(l.quantity) FROM goods_receipt_bill_links l WHERE l.bill_item_id = bi.id AND l.status = 'active'), 0) AS qty_tertaut,
           COALESCE((SELECT SUM(l.nilai_tagih) FROM goods_receipt_bill_links l WHERE l.bill_item_id = bi.id AND l.status = 'active'
                     AND l.kind <> 'legacy'), 0) AS nilai_tertaut,
           (EXISTS (SELECT 1 FROM inventory_ledger il WHERE il.tenant_id = $1 AND il.source_type = 'BILL' AND il.source_id = b.id
                    AND il.product_id = COALESCE(bi.product_id, poi.item_id) AND il.quantity_in > 0)
            AND NOT EXISTS (SELECT 1 FROM inventory_ledger iv WHERE iv.tenant_id = $1 AND iv.source_type = 'BILL_VOID' AND iv.source_id = b.id))
             AS legacy,
           (SELECT count(*) FROM bill_items x WHERE x.bill_id = b.id AND x.product_id = COALESCE(bi.product_id, poi.item_id)) > 1 AS ganda
    FROM bill_items bi JOIN bills b ON b.id = bi.bill_id
    JOIN purchase_order_items poi ON poi.id = bi.purchase_order_item_id
    JOIN products p ON p.id = poi.item_id AND p.tenant_id = $1
    LEFT JOIN journal_entries je ON je.id = b.journal_id
    WHERE b.tenant_id = $1 AND b.status_v2 NOT IN ('draft', 'void') AND p.item_type = 'goods' AND p.track_inventory
      AND ($2::uuid[] IS NULL OR bi.purchase_order_item_id = ANY($2::uuid[]))
    ORDER BY je.journal_date NULLS LAST, b.posted_at NULLS LAST, b.created_at, bi.line_number
"""


async def tertunda(conn, tenant_id: str, po_item_ids=None) -> dict:
    """-> {str(po_item_id): [{bill_item_id, bill_id, qty_terbuka, nilai_terbuka, legacy, blok?}]} urut FIFO. Baca saja (pemanggil mengunci PO).
    nilai_terbuka = B neto (tagihan_aksi.neto_tagihan_terposting, SAMA dgn posting) - Σ nilai_tagih tautan aktif; legacy -> 0 (bukan GRNI)."""
    from .tagihan_aksi import neto_tagihan_terposting
    rows = await conn.fetch(SQL_TERTUNDA, tenant_id, [UUID(str(x)) for x in po_item_ids] if po_item_ids is not None else None)
    bersisa = [r for r in rows if _d(r["quantity"]) - _d(r["qty_tertaut"]) > NOL]
    neto = await neto_tagihan_terposting(conn, tenant_id, list({r["bill_id"] for r in bersisa if not r["legacy"]})) if bersisa else {}
    out = {}
    for r in bersisa:
        t = {"bill_item_id": r["bill_item_id"], "bill_id": r["bill_id"], "qty_terbuka": _d(r["quantity"]) - _d(r["qty_tertaut"]),
             "legacy": bool(r["legacy"]),
             "nilai_terbuka": NOL if r["legacy"] else max(_d(neto.get(r["bill_item_id"])) - _d(r["nilai_tertaut"]), NOL)}
        sama = not r["unit"] or not r["satuan_po"] or r["unit"].strip().lower() == r["satuan_po"].strip().lower()
        if r["legacy"] and r["ganda"]:
            t["blok"] = blok("TAGIHAN_LAMA_PRODUK_GANDA", "Tagihan lama memuat barang yang sama di lebih dari satu baris; penerimaan tak bisa "
                             "dicocokkan otomatis. Hubungi admin.", 409)
        elif not sama:
            t["blok"] = blok("SATUAN_TAK_TERKONVERSI", f"Satuan tagihan ({r['unit']}) beda dengan satuan pesanan ({r['satuan_po']}).", 409)
        out.setdefault(str(r["purchase_order_item_id"]), []).append(t)
    return out


async def rencana_grn(conn, tenant_id: str, po, terima: dict, warehouse_id=None) -> dict:
    """terima = hasil po_rencana.rencana_terima (blok status/tanggal/qty sudah di sana). -> {blocks, baris[], gudang, jurnal[], nilai_grni}.
    Hanya membaca (pemanggil yang mengunci PO). Baris: {po_item_id, item_id, kode, nama, qty, harga_satuan, nilai, ber_stok}."""
    bloks = []
    akan = {k: v for k, v in terima["akan"].items() if v > NOL}
    peta = {str(r["id"]): r for r in terima["semua"]}
    ids = [peta[k]["id"] for k in akan if k in peta]
    detail = {str(r["id"]): r for r in await conn.fetch(
        """SELECT poi.id, poi.item_id, poi.quantity, poi.subtotal, poi.discount_amount, poi.description,
                  p.kode_produk, p.nama_produk, p.item_type, p.track_inventory
           FROM purchase_order_items poi LEFT JOIN products p ON p.id = poi.item_id AND p.tenant_id = $1
           WHERE poi.po_id = $2 AND poi.id = ANY($3::uuid[])""", tenant_id, po["id"], ids)} if ids else {}
    baris = []
    for pid, qty in akan.items():
        r = detail.get(pid)
        if r is None:
            continue
        stok = ber_stok(r["item_type"], r["track_inventory"])
        hs = harga_satuan_terima(r)
        baris.append({"po_item_id": pid, "item_id": r["item_id"], "kode": r["kode_produk"], "nama": r["nama_produk"] or r["description"],
                      "qty": qty, "harga_satuan": hs, "nilai": r2(qty * hs), "ber_stok": stok})
    stok_baris = [b for b in baris if b["ber_stok"]]
    gudang = None
    if stok_baris:
        gudang = await _gudang(conn, tenant_id, warehouse_id, po.get("warehouse_id"))
        if gudang is None or not gudang["is_active"]:
            bloks.append(blok("GUDANG_WAJIB", "Pilih gudang penerima: barang ber-stok wajib masuk ke gudang yang aktif."))
    tanggal = terima.get("payload", {}).get("receive_date")
    if stok_baris and tanggal:
        from datetime import date
        pesan = await _periode_terbuka(conn, tenant_id, date.fromisoformat(tanggal))
        if pesan:
            bloks.append(blok("PERIODE_TUTUP", pesan, 409))
    jurnal, nilai_grni = [], NOL
    if stok_baris:
        from .grn_pasang import pasangkan
        tt = await tertunda(conn, tenant_id, [b["po_item_id"] for b in stok_baris])
        for b in stok_baris:  # (D) pasang ke tagihan yang lebih dulu; tanpa tertunda = seluruhnya bebas (R)
            b["pasang"] = p = pasangkan(b["qty"], b["harga_satuan"], tt.get(str(b["po_item_id"]), []))
            terpakai = {x["bill_item_id"] for x in p["legacy"] + p["pasangan"]}
            for t in tt.get(str(b["po_item_id"]), []):
                if t.get("blok") and t["bill_item_id"] in terpakai:
                    bloks.append(t["blok"])
        from .inventory_helpers import resolve_inventory_accounts
        from .role_resolver import AccountRole, AccountRoleUnmappedError, resolve_account_id_by_role
        try:
            akun_grni = await resolve_account_id_by_role(conn, tenant_id, AccountRole.GOODS_RECEIVED_NOT_INVOICED)
            per_akun, kredit, selisih = {}, NOL, NOL
            for b in stok_baris:
                j = b["pasang"]["jurnal"]
                if j["persediaan"] > NOL:
                    a = (await resolve_inventory_accounts(conn, tenant_id, b["item_id"]))["inventory_account_id"]
                    per_akun[a] = per_akun.get(a, NOL) + j["persediaan"]
                kredit += j["grni_kredit"]
                selisih += j["selisih"]
            nilai_grni = kredit
            if per_akun or kredit:
                jurnal = [{"account_id": a, "debit": v, "credit": NOL, "memo": "Persediaan masuk (penerimaan barang)"} for a, v in per_akun.items()]
                if selisih != NOL:
                    akun_ppv = await resolve_account_id_by_role(conn, tenant_id, AccountRole.PURCHASE_PRICE_VARIANCE)
                    jurnal.append({"account_id": akun_ppv, "debit": max(selisih, NOL), "credit": max(-selisih, NOL),
                                   "memo": "Selisih harga tagihan vs penerimaan"})
                jurnal.append({"account_id": akun_grni, "debit": NOL, "credit": kredit, "memo": "Barang diterima belum ditagih"})
        except AccountRoleUnmappedError as e:
            bloks.append(blok("PERAN_AKUN_BELUM_DIPETAKAN", f"Akun belum dipetakan: {e}", 409))
    return {"blocks": bloks, "baris": baris, "gudang": gudang, "jurnal": jurnal, "nilai_grni": nilai_grni}


def untuk_respons(r: dict) -> dict:
    """Bentuk pratinjau JSON (uang string 2dp)."""
    return {"warehouse": ({"id": str(r["gudang"]["id"]), "name": r["gudang"]["name"]} if r.get("gudang") else None),
            "items": [{"po_item_id": b["po_item_id"], "description": b["nama"], "qty": str(b["qty"]), "unit_cost": str(r2(b["harga_satuan"])),
                       "amount": str(b["nilai"]), "stocked": b["ber_stok"],
                       # (D) pemasangan ke tagihan yang lebih dulu (kosong = tak ada tagihan tertunda)
                       "matched_bills": [{"bill_item_id": str(x["bill_item_id"]), "qty": str(x["qty"]), "received_amount": str(x["nilai_terima"]),
                                          "billed_amount": str(x["nilai_tagih"]), "variance": str(x["selisih"])}
                                         for x in (b.get("pasang") or {}).get("pasangan", [])],
                       "legacy_qty": str(sum((x["qty"] for x in (b.get("pasang") or {}).get("legacy", [])), NOL))} for b in r["baris"]],
            "journal": [{"account_id": str(j["account_id"]), "debit": str(r2(j["debit"])), "credit": str(r2(j["credit"])), "memo": j["memo"]}
                        for j in r["jurnal"]],
            "grni_amount": str(r2(r["nilai_grni"]))}


async def tulis_grn(conn, ctx: dict, po, receive_date, rencana: dict, notes: Optional[str] = None) -> dict:
    """Di transaksi PEMANGGIL sesudah PO dikunci & penghitung diterima ditulis. Blok rencana WAJIB sudah ditolak pemanggil.
    -> {goods_receipt_id, receipt_number, journal_id}."""
    from uuid import uuid4
    from .inventory_helpers import record_inventory_inbound
    from .sj_so import _jurnal
    tid, uid = ctx["tenant_id"], ctx.get("user_id")
    gid = uuid4()
    nomor = await conn.fetchval("SELECT get_next_journal_number($1, 'GRN', $2)", tid, receive_date)
    gudang_id = rencana["gudang"]["id"] if rencana.get("gudang") else None
    await conn.execute(
        """INSERT INTO goods_receipts (id, tenant_id, receipt_number, purchase_order_id, vendor_id, receipt_date, warehouse_id, status,
                                      notes, created_by) VALUES ($1, $2, $3, $4, $5, $6, $7, 'posted', $8, $9)""",
        gid, tid, nomor, po["id"], po.get("vendor_id"), receive_date, gudang_id, notes, uid)
    jid = None
    if rencana["jurnal"]:
        jid, _ = await _jurnal(conn, tid, uid, prefix="GRJ", tanggal=receive_date, source_type=SUMBER, source_id=gid,
                               deskripsi=f"Penerimaan barang {nomor} ({po['po_number']})",
                               baris=[(j["account_id"], r2(j["debit"]), r2(j["credit"]), j["memo"]) for j in rencana["jurnal"]])
        await conn.execute("UPDATE goods_receipts SET journal_id = $2 WHERE id = $1 AND tenant_id = $3", gid, jid, tid)
    n = 0

    async def baris_grn(b, qty, nilai, stok, ledger):
        nonlocal n
        n += 1
        return await conn.fetchval(
            """INSERT INTO goods_receipt_items (goods_receipt_id, purchase_order_item_id, item_id, quantity, unit_cost, amount, ber_stok,
                                               inventory_ledger_id, line_number) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9) RETURNING id""",
            gid, UUID(str(b["po_item_id"])), b["item_id"], qty, b["harga_satuan"], nilai, stok, ledger, n)

    async def tautan(gr_item, bill_item, qty, grni, variance, kind, nilai_tagih):
        await conn.execute(
            """INSERT INTO goods_receipt_bill_links (goods_receipt_item_id, bill_item_id, quantity, grni_amount, variance_amount, status, kind,
                                                     nilai_tagih) VALUES ($1, $2, $3, $4, $5, 'active', $6, $7)""",
            gr_item, bill_item, qty, grni, variance, kind, nilai_tagih)

    for b in rencana["baris"]:
        p = b.get("pasang")
        if not b["ber_stok"] or p is None:
            await baris_grn(b, b["qty"], b["nilai"], b["ber_stok"], None)
            continue
        if p["qty_stok"] > NOL:  # (D) stok masuk qty bukan-legacy @ harga terima; tautan tagih_dulu ke baris ini
            ledger = (await record_inventory_inbound(
                conn, tid, b["item_id"], b["kode"], b["nama"], gudang_id, p["qty_stok"], b["harga_satuan"], SUMBER, gid, nomor, uid,
                f"Penerimaan barang {nomor}", movement_date=receive_date, journal_id=jid))["ledger_id"]
            gi = await baris_grn(b, p["qty_stok"], p["nilai_stok"], True, ledger)
            for x in p["pasangan"]:
                await tautan(gi, x["bill_item_id"], x["qty"], x["nilai_terima"], x["selisih"], "tagih_dulu", x["nilai_tagih"])
        if p["legacy"]:  # stok SUDAH masuk lewat tagihan lama -> dokumen saja
            q = sum((x["qty"] for x in p["legacy"]), NOL)
            gi = await baris_grn(b, q, r2(q * b["harga_satuan"]), False, None)
            for x in p["legacy"]:
                await tautan(gi, x["bill_item_id"], x["qty"], NOL, NOL, "legacy", None)
    return {"goods_receipt_id": gid, "receipt_number": nomor, "journal_id": jid}


# ---------------------------------------------------------------- void
async def rencana_void(conn, tenant_id: str, gr_id, alasan: Optional[str], hari_ini, kunci: bool = False) -> dict:
    """-> {blocks, gr, items, ulang}. Ditolak bila: sudah ditagih (tautan aktif), alasan kosong, periode tutup, atau stok sudah keluar
    melebihi saldo gudang (Rule 7: void tak boleh membuat stok gudang negatif). Sudah void = ulang (idempoten)."""
    gr = await conn.fetchrow("SELECT * FROM goods_receipts WHERE id = $1 AND tenant_id = $2", gr_id, tenant_id)
    if gr is None:
        raise HTTPException(status_code=404, detail={"code": "GRN_TIDAK_DITEMUKAN", "message": "Penerimaan barang tidak ditemukan."})
    if kunci:
        await conn.fetchrow("SELECT id FROM purchase_orders WHERE id = $1 AND tenant_id = $2 FOR UPDATE", gr["purchase_order_id"], tenant_id)
        gr = await conn.fetchrow("SELECT * FROM goods_receipts WHERE id = $1 AND tenant_id = $2 FOR UPDATE", gr_id, tenant_id)
    items = await conn.fetch("SELECT * FROM goods_receipt_items WHERE goods_receipt_id = $1 ORDER BY line_number", gr_id)
    if gr["status"] == "void":
        return {"blocks": [], "gr": gr, "items": items, "ulang": True}
    bloks = []
    if await conn.fetchval(
            """SELECT count(*) FROM goods_receipt_bill_links l JOIN goods_receipt_items i ON i.id = l.goods_receipt_item_id
               WHERE i.goods_receipt_id = $1 AND l.status = 'active' AND l.kind = 'terima_dulu'""", gr_id):  # (D) void simetris
        bloks.append(blok("GRN_SUDAH_DITAGIH", "Penerimaan ini sudah ditagih. Batalkan tagihannya dulu, atau buat retur lewat Nota Debit.", 409))
    if not (alasan or "").strip():
        bloks.append(blok("ALASAN_WAJIB", "Alasan pembatalan wajib diisi.", 422))
    if gr["journal_id"] is not None:
        pesan = await _periode_terbuka(conn, tenant_id, hari_ini)
        if pesan:
            bloks.append(blok("PERIODE_TUTUP", pesan, 409))
        for i in items:
            if not i["ber_stok"]:
                continue
            saldo = _d(await conn.fetchval(
                """SELECT quantity FROM warehouse_stock WHERE tenant_id = $1 AND warehouse_id = $2 AND item_id = $3""",
                tenant_id, gr["warehouse_id"], i["item_id"]))
            if saldo < _d(i["quantity"]) - _d(i["quantity_returned"]):
                bloks.append(blok("STOK_SUDAH_KELUAR", f"Stok barang di gudang tinggal {saldo}, kurang dari jumlah yang akan dibatalkan.", 409))
    return {"blocks": bloks, "gr": gr, "items": items, "ulang": False}


async def void_grn(conn, ctx: dict, gr_id, alasan: str, hari_ini) -> dict:
    """Di transaksi PEMANGGIL. Pembalik jurnal (asli POSTED + reversed_by_id), stok dibalik (record_inventory_reversal), penghitung PO
    turun, status PO disinkronkan (po_daftar.sinkron_tagih). -> {goods_receipt_id, reversal_journal_id, ulang}."""
    from . import po_daftar as PD
    from .inventory_helpers import record_inventory_reversal
    from .sj_so import _jurnal
    tid, uid = ctx["tenant_id"], ctx.get("user_id")
    r = await rencana_void(conn, tid, gr_id, alasan, hari_ini, kunci=True)
    if r["ulang"]:
        return {"goods_receipt_id": gr_id, "reversal_journal_id": r["gr"]["reversal_journal_id"], "ulang": True}
    if r["blocks"]:
        b = r["blocks"][0]
        raise HTTPException(status_code=b["status"], detail={"code": b["code"], "message": b["message"]})
    gr, rid = r["gr"], None
    if gr["journal_id"] is not None:
        asli = await conn.fetch("SELECT account_id, debit, credit, memo FROM journal_lines WHERE journal_id = $1 ORDER BY line_number", gr["journal_id"])
        rid, _ = await _jurnal(conn, tid, uid, prefix="GRJ-REV", tanggal=hari_ini, source_type=SUMBER_BALIK, source_id=gr_id,
                               deskripsi=f"Pembatalan penerimaan barang {gr['receipt_number']}",
                               baris=[(x["account_id"], x["credit"], x["debit"], f"Pembalik: {x['memo'] or ''}") for x in asli],
                               reversal_of=gr["journal_id"], alasan=alasan)
        await conn.execute("UPDATE journal_entries SET reversed_by_id = $2, reversed_at = NOW() WHERE id = $1 AND tenant_id = $3",
                           gr["journal_id"], rid, tid)
        await record_inventory_reversal(conn, tid, source_type=SUMBER, source_id=gr_id, reversal_journal_id=rid, created_by=uid,
                                        reversal_date=hari_ini, notes_prefix="VOID")
    # (D) penerimaan yang datang SESUDAH tagihan: tautan dibalik -> tagihan kembali tertunda (Dr GRNI-nya tetap, sah)
    await conn.execute(
        """UPDATE goods_receipt_bill_links SET status = 'reversed', reversed_at = NOW()
           WHERE status = 'active' AND kind IN ('tagih_dulu', 'legacy')
             AND goods_receipt_item_id IN (SELECT id FROM goods_receipt_items WHERE goods_receipt_id = $1)""", gr_id)
    for i in r["items"]:
        await conn.execute(
            "UPDATE purchase_order_items SET quantity_received = GREATEST(0, COALESCE(quantity_received, 0) - $2) WHERE id = $1",
            i["purchase_order_item_id"], _d(i["quantity"]) - _d(i["quantity_returned"]))
    await conn.execute(
        """UPDATE goods_receipts SET status = 'void', voided_at = NOW(), voided_by = $3, voided_reason = $4, reversal_journal_id = $5
           WHERE id = $1 AND tenant_id = $2""", gr_id, tid, uid, alasan.strip(), rid)
    semua = await conn.fetch("SELECT quantity, quantity_received, quantity_billed, total FROM purchase_order_items WHERE po_id = $1",
                             gr["purchase_order_id"])
    await conn.execute("UPDATE purchase_orders SET amount_received = $3, updated_at = NOW() WHERE id = $1 AND tenant_id = $2",
                       gr["purchase_order_id"], tid, PD.jumlah_turunan([dict(x) for x in semua])["amount_received"])
    await PD.sinkron_tagih(conn, tid, gr["purchase_order_id"])
    return {"goods_receipt_id": gr_id, "reversal_journal_id": rid, "ulang": False}
