"""Gudang + batas retur barang NOTA DEBIT (ND-GUDANG, 3 Nov 2026; desain OK MASTER, putusan B: biaya = WAC saat ini, patuh inventory Rule 3).

Dulu: posting ND berbarang SELALU mengeluarkan stok dari gudang PERTAMA tenant tanpa cek stok/sisa -> stok tak ada di gudang itu = 500
(CHECK chk_ws_quantity); multi-gudang = stok keluar dari gudang yang SALAH diam-diam. Kini (paritas NK `_keluar_faktur_untuk_retur`):
  * dengan tagihan asal: gudang = gudang MASUK terbesar barang itu lewat tagihan tsb (kartu stok 'BILL' − 'BILL_VOID', ditambah GRN yang
    tertaut ke baris tagihan lewat goods_receipt_bill_links aktif non-legacy, GRN tak batal); sisa = masuk − sudah diretur ND LAIN (kartu
    'VENDOR_CREDIT' − 'VENDOR_CREDIT_VOID'); belum pernah masuk -> 400; melebihi sisa -> 400.
  * tanpa tagihan asal: gudang DEFAULT (is_default = tempat FPB memasukkan stok; putusan MASTER), cadangan gudang pertama dibuat.
  * lalu stok gudang terpilih (warehouse_stock, yang dijaga chk_ws_quantity) >= qty, kalau tidak 400.
SATU penentu untuk penulis (post_vendor_credit, kunci=True: advisory lock tagihan asal + FOR UPDATE baris stok SEBELUM menghitung) DAN
pratinjau posting/buat ND (kunci=False, READ ONLY). Kalimat dari teks_galat."""
from decimal import Decimal
from typing import Optional

from . import teks_galat as tg

NOL = Decimal("0")


async def asal_retur(conn, tid: str, bill_id, product_id, nd_id) -> Optional[dict]:
    """{gudang, masuk, sudah_retur, sisa} barang lewat tagihan asal; None = tak pernah masuk lewat tagihan itu."""
    per = {}
    for r in await conn.fetch(
            """SELECT warehouse_id, COALESCE(SUM(quantity_in), 0) - COALESCE(SUM(quantity_out), 0) AS q FROM inventory_ledger
               WHERE tenant_id = $1 AND source_id = $2 AND product_id = $3 AND source_type IN ('BILL', 'BILL_VOID') AND warehouse_id IS NOT NULL
               GROUP BY warehouse_id""", tid, bill_id, product_id):
        per[r["warehouse_id"]] = per.get(r["warehouse_id"], NOL) + Decimal(str(r["q"]))
    for r in await conn.fetch(
            # QTY-DASAR R1: qty tautan = satuan dokumen (PO); dikali rasio kartu stok baris GRN (quantity_in dasar / qty GRN) supaya
            # sebanding dengan kartu BILL & qty ND (keduanya satuan dasar). GRN lama (1:1) -> rasio 1; tanpa baris kartu -> 1.
            """SELECT g.warehouse_id, COALESCE(SUM(l.quantity * COALESCE(il.quantity_in / NULLIF(gi.quantity, 0), 1)), 0) AS q
               FROM goods_receipt_bill_links l
               JOIN bill_items bi ON bi.id = l.bill_item_id JOIN goods_receipt_items gi ON gi.id = l.goods_receipt_item_id
               LEFT JOIN inventory_ledger il ON il.id = gi.inventory_ledger_id
               JOIN goods_receipts g ON g.id = gi.goods_receipt_id AND g.tenant_id = $1
               WHERE bi.bill_id = $2 AND COALESCE(bi.product_id, gi.item_id) = $3 AND l.status = 'active' AND l.kind <> 'legacy'
                 AND g.status <> 'void' AND gi.ber_stok GROUP BY g.warehouse_id""", tid, bill_id, product_id):
        per[r["warehouse_id"]] = per.get(r["warehouse_id"], NOL) + Decimal(str(r["q"]))
    per = {g: q for g, q in per.items() if q > 0}
    if not per:
        return None
    masuk = sum(per.values(), NOL)
    gudang = max(per.items(), key=lambda x: (x[1], str(x[0])))[0]
    sudah = Decimal(str(await conn.fetchval(
        """SELECT COALESCE(SUM(il.quantity_out), 0) - COALESCE(SUM(il.quantity_in), 0) FROM inventory_ledger il
           JOIN vendor_credits v ON v.id = il.source_id AND v.tenant_id = il.tenant_id
           WHERE il.tenant_id = $1 AND il.source_type IN ('VENDOR_CREDIT', 'VENDOR_CREDIT_VOID') AND v.original_bill_id = $2
             AND il.product_id = $3 AND ($4::uuid IS NULL OR v.id <> $4::uuid)""", tid, bill_id, product_id, nd_id) or 0))
    return {"gudang": gudang, "masuk": masuk, "sudah_retur": sudah, "sisa": masuk - sudah}


async def rencana_keluar(conn, tid: str, vc, items, kunci: bool, bl) -> list:
    """Per baris barang dilacak: {item_row_id, product_id, nama, qty, gudang_id, gudang}. Blok lewat bl.galat (penulis: dilempar pertama)."""
    if kunci and vc["original_bill_id"]:
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"ND_RETUR_TAGIHAN:{vc['original_bill_id']}")
    bawaan = await conn.fetchval(  # == bills_service: FPB memasukkan stok ke gudang is_default
        "SELECT id FROM warehouses WHERE tenant_id = $1 ORDER BY is_default DESC NULLS LAST, created_at LIMIT 1", tid)
    keluar, butuh, asal_cache = [], {}, {}
    for it in items:
        if not it["item_id"]:
            continue
        p = await conn.fetchrow("SELECT id, item_code, nama_produk, track_inventory FROM products WHERE id = $1", it["item_id"])
        if not p or not p["track_inventory"]:
            continue
        nama = p["nama_produk"] or p["item_code"]
        # QTY-DASAR R2: ND = jalur KELUAR -> qty dokumen ke SATUAN DASAR (penentu SATU; penulis + pratinjau): batas sisa (asal_retur
        # sudah dasar), stok gudang, kartu, dan HPP dalam dasar; tanpa konversi -> blok berkalimat
        from . import satuan_dasar as SD
        h_sd = SD.hitung(await SD.muat(conn, tid, [p["id"]] if SD.normal(it.get("unit")) else []), p["id"], it.get("unit"), it["quantity"], "konversi")
        if h_sd["blok"]:
            bl.galat(h_sd["blok"]["code"], h_sd["blok"]["status"], h_sd["blok"]["message"])
            continue
        qty = h_sd["qty_dasar"]
        if vc["original_bill_id"]:
            if p["id"] not in asal_cache:
                asal_cache[p["id"]] = await asal_retur(conn, tid, vc["original_bill_id"], p["id"], vc.get("id"))
            asal = asal_cache[p["id"]]
            if asal is None:
                bl.galat("ND_BARANG_BELUM_MASUK", 400, tg.nd_barang_belum_masuk(nama, vc["original_bill_number"]))
                continue
            gudang = asal["gudang"]
        else:
            if not bawaan:
                continue
            gudang, asal = bawaan, None
        kunci_b = (p["id"], gudang)
        butuh[kunci_b] = butuh.get(kunci_b, NOL) + qty
        keluar.append({"item_row_id": it.get("id"), "product_id": p["id"], "nama": nama, "qty": qty, "gudang_id": gudang, "_asal": asal})
    # batas sisa (per produk, Σ baris) lalu stok gudang (FOR UPDATE di penulis: dua posting serentak tak sama-sama lolos)
    for (pid, gudang), q in sorted(butuh.items(), key=lambda x: (str(x[0][0]), str(x[0][1]))):
        k = next(x for x in keluar if x["product_id"] == pid and x["gudang_id"] == gudang)
        if k["_asal"] is not None and q > k["_asal"]["sisa"]:
            bl.galat("ND_RETUR_MELEBIHI_SISA", 400, tg.nd_retur_melebihi(k["nama"], q, max(k["_asal"]["sisa"], NOL)))
            continue
        stok = await conn.fetchval(
            "SELECT quantity FROM warehouse_stock WHERE tenant_id = $1 AND warehouse_id = $2 AND item_id = $3" + (" FOR UPDATE" if kunci else ""),
            tid, gudang, pid)
        stok = Decimal(str(stok or 0))
        if stok < q:
            nama_g = await conn.fetchval("SELECT name FROM warehouses WHERE id = $1 AND tenant_id = $2", gudang, tid)
            bl.galat("ND_STOK_KURANG", 400, tg.nd_stok_kurang(k["nama"], nama_g, stok, q))
    for k in keluar:
        k.pop("_asal", None)
        k["gudang"] = await conn.fetchval("SELECT name FROM warehouses WHERE id = $1 AND tenant_id = $2", k["gudang_id"], tid)
    return keluar


async def catatan_sudah_diretur(conn, tid: str, bill_id, baris_tagihan) -> list:
    """BUG-081 (6 Nov 2026, putusan MASTER; tanpa migrasi): catatan Tinjau ND per PRODUK atas tagihan asal -- "sudah diretur X dari Y"
    (ND terbit non-void) + ND DRAF sebagai peringatan, semua dalam SATUAN DASAR (satuan_dasar.hitung "konversi"; baris tak terkonversi
    dilewati). Bukan blok: batas qty yang MENOLAK tetap asal_retur saat posting (barang dilacak). Per-baris (original_bill_item_id) = tiket
    sesudah migrasi. -> [{code, message, product_id}] hanya untuk produk yang sudah/sedang diretur."""
    from . import satuan_dasar as SD
    pids = [r["product_id"] for r in baris_tagihan if r["product_id"]]
    if not pids:
        return []
    nd = await conn.fetch(
        """SELECT v.credit_number, v.status, i.item_id, i.quantity, i.unit FROM vendor_credits v
           JOIN vendor_credit_items i ON i.vendor_credit_id = v.id
           WHERE v.tenant_id = $1 AND v.original_bill_id = $2 AND v.status <> 'void' AND i.item_id IS NOT NULL
           ORDER BY v.credit_number""", tid, bill_id)
    if not nd:
        return []
    ktx = await SD.muat(conn, tid, pids)
    masuk, terbit, draf, nomor_draf, nama = {}, {}, {}, {}, {}
    for r in baris_tagihan:
        if not r["product_id"]:
            continue
        h = SD.hitung(ktx, r["product_id"], r["unit"], r["quantity"], "konversi")
        k = str(r["product_id"])
        nama[k] = r["product_name"] or r["description"] or "-"
        if not h["blok"]:
            masuk[k] = masuk.get(k, NOL) + h["qty_dasar"]
    for r in nd:
        k = str(r["item_id"])
        if k not in masuk:
            continue
        h = SD.hitung(ktx, r["item_id"], r["unit"], r["quantity"], "konversi")
        if h["blok"]:
            continue
        if r["status"] == "draft":
            draf[k] = draf.get(k, NOL) + h["qty_dasar"]
            nomor_draf.setdefault(k, []).append(r["credit_number"])
        else:
            terbit[k] = terbit.get(k, NOL) + h["qty_dasar"]
    out = []
    for k in masuk:
        x, z = terbit.get(k, NOL), draf.get(k, NOL)
        if x == 0 and z == 0:
            continue
        dasar = (ktx["barang"].get(k) or {}).get("dasar") or ""
        pesan = f"{nama[k]}: sudah diretur {tg.qty(x)} dari {tg.qty(masuk[k])} {dasar}".rstrip()
        if z:
            pesan += f"; {tg.qty(z)} lagi di draf {', '.join(sorted(set(nomor_draf[k])))}"
        lebih = x + z > masuk[k]
        out.append({"code": "ND_RETUR_MELEBIHI_TAGIHAN" if lebih else "ND_SUDAH_DIRETUR",
                    "message": pesan + (" -- melebihi jumlah di faktur." if lebih else "."), "product_id": k})
    return out
