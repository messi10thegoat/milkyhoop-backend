"""Kontrak GRN <-> posting tagihan (RANCANGAN-penerimaan-barang-grn §7b(3), §7c) -- SATU penentu nilai GRNI & selisih harga.

Dipanggil BACKEND (bills_service / tagihan_rencana) di jalur PRATINJAU, POSTING, dan VOID tagihan. BACKEND2 memiliki berkas ini.
  rencana_posting_baris(conn, tenant_id, bill_items) -> [ {bill_item_id, po_item_id, qty_tagih, qty_grn, qty_inbound, nilai_grni,
      nilai_tagih_grn, selisih, perlu_inbound, tautan: [{gr_item_id, qty, nilai}], blok?} ]
    * bill_items: {id, quantity, unit, subtotal, discount_amount, purchase_order_item_id, nilai_neto, qty_dasar} (nilai_neto & qty_dasar
      dari pemanggil -- review BACKEND 23 Okt).
    * flag `penerimaan_barang_v1` MATI -> []  (posting tagihan PERSIS perilaku sekarang).
    * ATURAN (D) (putusan LANGSUNG pemilik 23 Okt, pola SAP GR/IR, kontrak disepakati BACKEND): SETIAP baris tagihan yang bertaut baris PO
      BER-STOK (products goods + track_inventory) diproses, ADA atau TIDAK penerimaan terbuka; baris PO non-stok -> tak ada butir (lama).
    * FIFO per GRN (penerimaan terlama -- receipt_date, created_at, line_number -- ditagih dulu); qty terbuka baris GRN =
      quantity - quantity_returned - Σ tautan AKTIF. Satu baris tagihan boleh menutup beberapa GRN.
    * nilai_grni = Σ qty_tautan x harga satuan TERIMA (2dp per tautan) -> Dr 2-10150 (GRNI) di jurnal tagihan.
      nilai_tagih_grn = r2(nilai_neto x qty_grn / qty) -- nilai_neto = neto SEMUA diskon (baris + header invoice/tunai) tanpa PPN, DIKIRIM
      pemanggil dari tagihan_rencana.hitung (fungsi sama dgn jurnal). Qty = `qty_dasar` (satuan dasar PO/GRN; pemanggil mengonversi);
      satuan beda tanpa qty_dasar -> butir `blok` SATUAN_TAK_TERKONVERSI (pemanggil menolak posting).
      selisih = nilai_tagih_grn - nilai_grni -> Selisih Harga Pembelian 5-10400 (debit bila positif, kredit bila negatif; P3, tanpa
      revaluasi Persediaan/HPP).
    * (D) tagihan atas baris PO ber-stok TAK PERNAH memasukkan stok: qty_inbound = 0, perlu_inbound = False (stok masuk HANYA lewat GRN).
      Bagian BELUM diterima: qty_tertunda = qty - qty_grn, nilai_tertunda = nilai_neto - nilai_tagih_grn (B, tutup sen ke nilai_neto) ->
      Dr GRNI saat tagihan (saldo debit sementara; selisih harga bagian ini dibukukan GRN yang datang kemudian, tautan 'tagih_dulu').
      Dr GRNI tagihan = nilai_grni + nilai_tertunda; Dr/Cr PPV = selisih; Σ = nilai_neto.
    * Penentu read-only (tanpa kunci) -- pemanggil posting WAJIB sudah mengunci tagihan + PO (FOR UPDATE PO -- urutan sama dgn /receive).
  tulis_tautan(conn, rencana)   -> menulis goods_receipt_bill_links (status active, kind 'terima_dulu', nilai_tagih = bagian B tautan,
      variance = nilai_tagih - grni_amount) di transaksi posting pemanggil.
  balik_tautan(conn, bill_item_ids) -> menandai tautan 'reversed' di transaksi void pemanggil (qty terbuka GRN kembali).
Jurnal tagihan tetap ditulis BACKEND: Σ baris == header (Law 4) -- penentu ini hanya memberi nilai."""
from decimal import ROUND_HALF_UP, Decimal

NOL = Decimal("0")
SEN = Decimal("0.01")


def _d(v) -> Decimal:
    return Decimal(str(v)) if v is not None else NOL


def r2(v: Decimal) -> Decimal:
    return v.quantize(SEN, rounding=ROUND_HALF_UP)


def qty_dasar(bill_item) -> Decimal:
    """Qty tagihan dalam SATUAN DASAR baris PO/GRN (review BACKEND 23 Okt): pemanggil mengirim `qty_dasar` hasil convert_to_base_unit; tanpa
    itu = `quantity` (satuan sama)."""
    return _d(bill_item["qty_dasar"]) if bill_item.get("qty_dasar") is not None else _d(bill_item["quantity"])


def nilai_neto_tagih(bill_item) -> Decimal:
    """Nilai baris tagihan NETO SEMUA diskon (baris + bagian diskon header invoice/tunai), tanpa PPN -- DIKIRIM pemanggil dari
    tagihan_rencana.hitung (fungsi SAMA dengan jurnal tagihan; review BACKEND 23 Okt). Cadangan (tes/tanpa header): subtotal - diskon baris."""
    if bill_item.get("nilai_neto") is not None:
        return _d(bill_item["nilai_neto"])
    return _d(bill_item["subtotal"]) - _d(bill_item.get("discount_amount"))


def alokasi_fifo(qty_tagih: Decimal, terbuka: list) -> list:
    """Murni. terbuka: [{gr_item_id, qty_terbuka, harga_satuan, sisa_nilai}] urut FIFO. -> [{gr_item_id, qty, nilai}].
    nilai = qty x harga terima (2dp); tautan yang MENGHABISKAN baris GRN memakai sisa_nilai persis, supaya Σ nilai tautan == nilai baris GRN
    (saldo GRNI kembali NOL tepat, tanpa sisa sen pembulatan antar-tagihan sebagian)."""
    sisa, out = qty_tagih, []
    for t in terbuka:
        if sisa <= NOL:
            break
        q = min(sisa, t["qty_terbuka"])
        if q <= NOL:
            continue
        nilai = t["sisa_nilai"] if q == t["qty_terbuka"] and "sisa_nilai" in t else r2(q * t["harga_satuan"])
        out.append({"gr_item_id": t["gr_item_id"], "qty": q, "nilai": nilai})
        sisa -= q
    return out


def rencana_baris(bill_item, terbuka: list) -> dict:
    """Murni: satu baris tagihan atas daftar penerimaan terbuka (FIFO) -> butir kontrak. Qty = satuan dasar PO/GRN (qty_dasar);
    nilai_tagih_grn = r2(nilai_neto x qty_grn / qty) -- bagian ber-GRN dari nilai neto yang SAMA dengan jurnal tagihan."""
    qty = qty_dasar(bill_item)
    tautan = alokasi_fifo(qty, terbuka)
    qty_grn = sum((t["qty"] for t in tautan), NOL)
    nilai_grni = sum((t["nilai"] for t in tautan), NOL)
    neto = nilai_neto_tagih(bill_item)
    nilai_tagih_grn = (neto if qty_grn == qty else r2(neto * qty_grn / qty)) if qty > NOL else NOL
    dibagi = NOL
    for k, t in enumerate(tautan):  # bagian B per tautan: pro rata qty, tautan terakhir menutup nilai_tagih_grn persis
        t["nilai_tagih"] = (nilai_tagih_grn - dibagi) if k == len(tautan) - 1 else r2(nilai_tagih_grn * t["qty"] / qty_grn)
        dibagi += t["nilai_tagih"]
    return {"bill_item_id": bill_item["id"], "po_item_id": bill_item["purchase_order_item_id"], "qty_tagih": qty, "qty_grn": qty_grn,
            "qty_inbound": NOL, "perlu_inbound": False,  # (D) stok masuk HANYA lewat GRN
            "qty_tertunda": qty - qty_grn, "nilai_tertunda": neto - nilai_tagih_grn,
            "nilai_grni": nilai_grni, "nilai_tagih_grn": nilai_tagih_grn, "selisih": nilai_tagih_grn - nilai_grni, "tautan": tautan}


SQL_TERBUKA = """
    SELECT i.id AS gr_item_id, i.purchase_order_item_id, i.unit_cost, poi.unit AS satuan_po,
           i.quantity - i.quantity_returned - COALESCE((SELECT SUM(l.quantity) FROM goods_receipt_bill_links l
                                                         WHERE l.goods_receipt_item_id = i.id AND l.status = 'active'), 0) AS qty_terbuka,
           ROUND((i.quantity - i.quantity_returned) * i.unit_cost, 2)
             - COALESCE((SELECT SUM(l.grni_amount) FROM goods_receipt_bill_links l
                         WHERE l.goods_receipt_item_id = i.id AND l.status = 'active'), 0) AS sisa_nilai
    FROM goods_receipt_items i JOIN goods_receipts g ON g.id = i.goods_receipt_id
    JOIN purchase_order_items poi ON poi.id = i.purchase_order_item_id
    WHERE g.tenant_id = $1 AND g.status = 'posted' AND i.ber_stok AND i.purchase_order_item_id = ANY($2::uuid[])
    ORDER BY g.receipt_date, g.created_at, i.line_number
"""


SQL_BARIS_PO = """
    SELECT poi.id, poi.unit AS satuan_po, p.item_type, p.track_inventory
    FROM purchase_order_items poi JOIN purchase_orders po ON po.id = poi.po_id AND po.tenant_id = $1
    LEFT JOIN products p ON p.id = poi.item_id AND p.tenant_id = $1
    WHERE poi.id = ANY($2::uuid[])
"""


async def rencana_posting_baris(conn, tenant_id: str, bill_items: list) -> list:
    """bill_items: baris bill_items (dict/Record: id, quantity, subtotal, discount_amount, purchase_order_item_id). Lihat docstring modul."""
    from .grn_rencana import aktif, ber_stok
    if not await aktif(conn, tenant_id):
        return []
    bertaut = [b for b in bill_items if b.get("purchase_order_item_id")]
    if not bertaut:
        return []
    ids = list({b["purchase_order_item_id"] for b in bertaut})
    baris_po = {r["id"]: r for r in await conn.fetch(SQL_BARIS_PO, tenant_id, ids)}
    satuan_po = {k: r["satuan_po"] for k, r in baris_po.items()}
    bertaut = [b for b in bertaut if b["purchase_order_item_id"] in baris_po  # (D) hanya baris PO BER-STOK
               and ber_stok(baris_po[b["purchase_order_item_id"]]["item_type"], baris_po[b["purchase_order_item_id"]]["track_inventory"])]
    if not bertaut:
        return []
    per_po_item = {}
    for r in await conn.fetch(SQL_TERBUKA, tenant_id, list({b["purchase_order_item_id"] for b in bertaut})):
        # penjaga (MASTER 23 Okt): qty/nilai terbuka TAK PERNAH negatif -- retur/void sesudah ditagih DITOLAK (lewat Nota Debit), dan
        # baris yang terbuka <= 0 tak ikut FIFO; sisa_nilai dijepit >= 0
        if _d(r["qty_terbuka"]) > NOL:
            per_po_item.setdefault(r["purchase_order_item_id"], []).append(
                {"gr_item_id": r["gr_item_id"], "qty_terbuka": _d(r["qty_terbuka"]), "harga_satuan": _d(r["unit_cost"]),
                 "sisa_nilai": max(_d(r["sisa_nilai"]), NOL)})
    out = []
    for b in bertaut:  # dua baris tagihan atas baris PO yang sama: FIFO berlanjut (qty terbuka dikurangi alokasi baris sebelumnya)
        terbuka = per_po_item.get(b["purchase_order_item_id"], [])  # (D) kosong pun diproses -> seluruhnya tertunda
        sp = satuan_po.get(b["purchase_order_item_id"])
        if b.get("qty_dasar") is None and b.get("unit") and sp and (b.get("unit") or "").strip().lower() != (sp or "").strip().lower():
            # satuan tagihan beda dgn satuan PO/GRN tanpa konversi dari pemanggil -> BLOK, bukan tebakan (review BACKEND)
            out.append({"bill_item_id": b["id"], "po_item_id": b["purchase_order_item_id"], "blok": {"code": "SATUAN_TAK_TERKONVERSI",
                        "message": f"Satuan tagihan ({b.get('unit')}) beda dengan satuan pesanan ({sp}); kirim qty dalam satuan pesanan."},
                        "tautan": [], "qty_grn": NOL, "nilai_grni": NOL, "selisih": NOL, "qty_inbound": NOL, "perlu_inbound": False,
                        "qty_tertunda": NOL, "nilai_tertunda": NOL, "nilai_tagih_grn": NOL})
            continue
        butir = rencana_baris(b, terbuka)
        terpakai = {t["gr_item_id"]: t for t in butir["tautan"]}
        per_po_item[b["purchase_order_item_id"]] = [
            {**t, "qty_terbuka": t["qty_terbuka"] - (terpakai[t["gr_item_id"]]["qty"] if t["gr_item_id"] in terpakai else NOL),
             "sisa_nilai": t["sisa_nilai"] - (terpakai[t["gr_item_id"]]["nilai"] if t["gr_item_id"] in terpakai else NOL)} for t in terbuka]
        out.append(butir)
    return out


async def tulis_tautan(conn, rencana: list) -> int:
    """Di transaksi posting pemanggil. -> jumlah tautan ditulis (kind 'terima_dulu'). nilai_tagih per tautan dari rencana_baris (Σ ==
    nilai_tagih_grn persis) -> variance = nilai_tagih - grni_amount, Σ variance == selisih baris tanpa sisa pembulatan."""
    n = 0
    for butir in rencana:
        if butir.get("blok"):
            raise ValueError(f"tulis_tautan: butir terblok {butir['blok']['code']} (pemanggil wajib menolak dulu)")
        for t in butir["tautan"]:
            await conn.execute(
                """INSERT INTO goods_receipt_bill_links (goods_receipt_item_id, bill_item_id, quantity, grni_amount, variance_amount, status,
                                                         kind, nilai_tagih)
                   VALUES ($1, $2, $3, $4, $5, 'active', 'terima_dulu', $6)""",
                t["gr_item_id"], butir["bill_item_id"], t["qty"], t["nilai"], t["nilai_tagih"] - t["nilai"], t["nilai_tagih"])
            n += 1
    return n


async def balik_tautan(conn, bill_item_ids: list) -> int:
    """Di transaksi void tagihan pemanggil. -> jumlah tautan 'terima_dulu' dibalik (status reversed; qty terbuka GRN kembali).
    (D) tautan 'tagih_dulu'/'legacy' (penerimaan DATANG SESUDAH tagihan) = tagihan tak boleh dibatalkan (BACKEND: TAGIHAN_TERCOCOK_GRN 409,
    void simetris) -> bila masih ada, GAGAL KERAS di sini juga (bukan dibalik diam-diam: stok/GRNI penerimaan akan yatim)."""
    if not bill_item_ids:
        return 0
    if await conn.fetchval(
            """SELECT count(*) FROM goods_receipt_bill_links WHERE bill_item_id = ANY($1::uuid[]) AND status = 'active'
               AND kind IN ('tagih_dulu', 'legacy')""", list(bill_item_ids)):
        raise ValueError("balik_tautan: tagihan bertaut penerimaan yang datang sesudahnya (tagih_dulu/legacy) -- pemanggil wajib menolak dulu")
    r = await conn.execute(
        """UPDATE goods_receipt_bill_links SET status = 'reversed', reversed_at = NOW()
           WHERE bill_item_id = ANY($1::uuid[]) AND status = 'active' AND kind = 'terima_dulu'""",
        list(bill_item_ids))
    return int(r.split()[-1]) if r else 0
