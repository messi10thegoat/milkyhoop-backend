"""Penjaga GRNI (RANCANGAN-penerimaan-barang-grn §7a, Law 15): per tenant, saldo jurnal EFEKTIF akun peran
GOODS_RECEIVED_NOT_INVOICED (2-10150, kredit-normal) == Σ baris GRN tak-void ber-stok: (qty - qty retur) x harga terima - Σ nilai GRNI
tautan tagihan AKTIF (jenis apa pun) - Σ nilai tagihan TERTUNDA bukan-legacy (aturan D). Fail-closed: peran tak terpetakan = galat, bukan "0". Dua sisi (sehat HIJAU + injeksi MERAH) diuji harness."""
from decimal import Decimal

NOL = Decimal("0")

SQL_SALDO_GL = """
    SELECT COALESCE(SUM(jl.credit - jl.debit), 0)
    FROM journal_lines jl JOIN journal_entries je ON je.id = jl.journal_id
    WHERE je.tenant_id = $1 AND je.status = 'POSTED' AND is_effective_journal(je.id) AND jl.account_id = $2
"""
SQL_TERBUKA_DOK = """
    SELECT COALESCE(SUM(ROUND((i.quantity - i.quantity_returned) * i.unit_cost, 2)), 0)
         - COALESCE((SELECT SUM(l.grni_amount) FROM goods_receipt_bill_links l JOIN goods_receipt_items i2 ON i2.id = l.goods_receipt_item_id
                     JOIN goods_receipts g2 ON g2.id = i2.goods_receipt_id
                     WHERE g2.tenant_id = $1 AND g2.status = 'posted' AND l.status = 'active'), 0)
    FROM goods_receipt_items i JOIN goods_receipts g ON g.id = i.goods_receipt_id
    WHERE g.tenant_id = $1 AND g.status = 'posted' AND i.ber_stok
"""


async def periksa(conn, tenant_id: str) -> dict:
    """-> {saldo_gl, terbuka_dokumen, selisih, sehat}. Galat bila peran GRNI tak terpetakan (fail-closed)."""
    from .role_resolver import AccountRole, resolve_account_id_by_role
    akun = await resolve_account_id_by_role(conn, tenant_id, AccountRole.GOODS_RECEIVED_NOT_INVOICED)
    gl = Decimal(str(await conn.fetchval(SQL_SALDO_GL, tenant_id, akun)))
    dok = Decimal(str(await conn.fetchval(SQL_TERBUKA_DOK, tenant_id)))
    # (D): tagihan yang lebih dulu = Dr GRNI sebesar B bagian yang belum diterima (saldo debit sah) -> dikurangkan. SATU sumber dgn penerimaan.
    from .grn_rencana import tertunda
    tagih_dulu = sum((t["nilai_terbuka"] for daftar in (await tertunda(conn, tenant_id)).values() for t in daftar if not t["legacy"]), NOL)
    dok -= tagih_dulu
    return {"saldo_gl": gl, "terbuka_dokumen": dok, "tagihan_tertunda": tagih_dulu, "selisih": gl - dok, "sehat": gl == dok}


# ---- Penjaga #2 (MASTER 23 Okt, sesudah temuan harness e2e `merah`): sisi TAGIHAN. Penjaga #1 hanya membandingkan GL GRNI dengan dokumen GRN
# + tautan, sehingga BUTA terhadap tagihan yang SEHARUSNYA membersihkan GRNI tapi lewat jalur lama (Dr Persediaan + kartu stok BILL ->
# stok & nilai DOBEL). Dua invarian per tenant, baca-saja, fail-closed (galat SQL = galat, bukan "0"):
#   (a) ganda        = baris kartu stok BILL (tagihan tak-void) untuk produk baris tagihan yang bertaut baris PO yang PUNYA baris GRN ber-stok
#                      tak-void -> barang yang sama masuk stok DUA KALI (GRN + tagihan), urutan apa pun.
#   (b) tanpa_tautan = baris tagihan tak-void bertaut baris PO yang punya baris GRN ber-stok tak-void yang SUDAH ADA saat tagihan diposting
#                      (GRN dibuat sebelum jurnal tagihan), tetapi tanpa tautan aktif -> posting melewati penentu GRN.
SQL_GANDA = """
    SELECT count(*) FROM inventory_ledger il
    JOIN bills b ON b.id = il.source_id AND b.tenant_id = il.tenant_id
    JOIN bill_items bi ON bi.bill_id = b.id AND bi.product_id = il.product_id
    WHERE il.tenant_id = $1 AND il.source_type = 'BILL' AND COALESCE(b.status_v2, b.status) NOT IN ('void', 'draft')
      AND bi.purchase_order_item_id IS NOT NULL
      AND EXISTS (SELECT 1 FROM goods_receipt_items i JOIN goods_receipts g ON g.id = i.goods_receipt_id
                  WHERE g.tenant_id = $1 AND g.status = 'posted' AND i.ber_stok AND i.purchase_order_item_id = bi.purchase_order_item_id)
"""
SQL_TANPA_TAUTAN = """
    SELECT count(*) FROM bill_items bi
    JOIN bills b ON b.id = bi.bill_id AND b.tenant_id = $1
    JOIN journal_entries je ON je.id = b.journal_id
    WHERE COALESCE(b.status_v2, b.status) NOT IN ('void', 'draft') AND bi.purchase_order_item_id IS NOT NULL
      AND EXISTS (SELECT 1 FROM goods_receipt_items i JOIN goods_receipts g ON g.id = i.goods_receipt_id
                  WHERE g.tenant_id = $1 AND g.status = 'posted' AND i.ber_stok AND i.purchase_order_item_id = bi.purchase_order_item_id
                    AND g.created_at < je.created_at)
      AND NOT EXISTS (SELECT 1 FROM goods_receipt_bill_links l WHERE l.bill_item_id = bi.id AND l.status = 'active')
"""


# ---- Penjaga #3 (MASTER 23 Okt, sesudah kontrol merah `merah_d`): PEMASANGAN TERLEWAT. Aturan D: dokumen yang datang KEMUDIAN selalu memasang
# diri ke yang lebih dulu (tagihan -> GRN terbuka FIFO; GRN -> tagihan tertunda FIFO), jadi pada satu baris PO TAK MUNGKIN sekaligus ada
# (i) baris GRN ber-stok tak-void yang qty-nya belum habis tertaut DAN (ii) baris tagihan tertunda bukan-legacy (qty_terbuka > 0).
# Keduanya ada = salah satu jalur melewati pemasangan. Penjaga #1 tetap HIJAU dalam kasus itu (GL == dokumen: GRN & tagihan sama-sama dihitung),
# karena itu invarian terpisah. Sumber tertunda = grn_rencana.tertunda (SATU sumber dgn penerimaan & penjaga #1).
SQL_GRN_TERBUKA_PER_BARIS_PO = """
    SELECT DISTINCT i.purchase_order_item_id FROM goods_receipt_items i JOIN goods_receipts g ON g.id = i.goods_receipt_id
    WHERE g.tenant_id = $1 AND g.status = 'posted' AND i.ber_stok
      AND i.quantity - i.quantity_returned - COALESCE((SELECT SUM(l.quantity) FROM goods_receipt_bill_links l
                                                       WHERE l.goods_receipt_item_id = i.id AND l.status = 'active'), 0) > 0
"""


async def pemasangan_terlewat(conn, tenant_id: str) -> int:
    """Jumlah baris PO yang sekaligus punya GRN ber-stok terbuka DAN tagihan tertunda bukan-legacy (harus 0)."""
    from .grn_rencana import tertunda
    grn = {str(r["purchase_order_item_id"]) for r in await conn.fetch(SQL_GRN_TERBUKA_PER_BARIS_PO, tenant_id)}
    if not grn:
        return 0
    tagih = {k for k, daftar in (await tertunda(conn, tenant_id)).items() if any(not t["legacy"] for t in daftar)}
    return len(grn & tagih)


async def periksa_tagihan(conn, tenant_id: str) -> dict:
    """-> {ganda, tanpa_tautan, terlewat, sehat}. sehat = ketiganya 0. Pemeriksa saja (tak dipanggil jalur tulis)."""
    ganda = int(await conn.fetchval(SQL_GANDA, tenant_id))
    tanpa = int(await conn.fetchval(SQL_TANPA_TAUTAN, tenant_id))
    terlewat = await pemasangan_terlewat(conn, tenant_id)
    return {"ganda": ganda, "tanpa_tautan": tanpa, "terlewat": terlewat, "sehat": ganda == 0 and tanpa == 0 and terlewat == 0}
