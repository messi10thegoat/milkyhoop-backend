"""Revaluasi selisih harga tagihan vs penerimaan ke PERSEDIAAN untuk stok yang masih ada (ppv-revaluasi-wac, 6 Nov 2026; putusan LANGSUNG
pemilik via sesi BACKEND2: Q1 flag `ppv_revaluasi_v1`, Q2 porsi yang sudah keluar -> 5-10400, Q3 dua arah). Pola SAP moving average / Odoo
AVCO / Accurate: selisih atas barang yang MASIH di gudang menaikkan/menurunkan nilai persediaan (WAC); hanya porsi yang sudah keluar ke
Selisih Harga Pembelian (HPP lama tak disentuh, Law 2). Flag mati = aturan lama grn_tagih P3 (semua selisih ke 5-10400) persis.

Nilai persediaan = Σ qty_masuk x unit_cost - Σ qty_keluar x unit_cost per baris kartu stok (inventory_helpers.record_inventory_inbound), jadi
baris "nilai saja" (qty 0) tak mengubah nilai -> revaluasi = PASANGAN baris qty sama di gudang penerimaan: MASUK Q @ WAC_baru LALU KELUAR
Q @ WAC_lama (cache gudang tak pernah negatif; qty bersih 0). Jurnal pemanggil: Dr/Cr Persediaan = Δ kartu stok (2dp), sisanya ke PPV ->
GL == kartu stok (health check 15). Void = pasangan dibalik urutan terbalik (balik), jurnal dibalik pemanggil.
  lipat(nilai_stok, selisih, dasar)  murni: arah tagih_dulu (GRN sesudah tagihan) -> biaya masuk + sisa sen
  bagi(V, Q, basis)                 murni: porsi selisih ke stok = r2(V x min(1, Q / basis)); Q <= 0 -> 0
  rencana(conn, tid, product_id, V, basis, gr_item_ids) -> butir | None   (baca saja; pratinjau == posting)
  tulis(conn, ctx, butir, source_id, source_number, journal_id, tanggal) (di transaksi pemanggil)
  balik(conn, ctx, source_id, journal_id_balik, tanggal)               (di transaksi void pemanggil)"""
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID, uuid4

FLAG = "ppv_revaluasi_v1"
SUMBER = "PPV_REVALUASI"
NOL = Decimal("0")
SEN = Decimal("0.01")


def r2(v: Decimal) -> Decimal:
    return v.quantize(SEN, rounding=ROUND_HALF_UP)


async def aktif(conn, tenant_id: str) -> bool:
    return bool(await conn.fetchval(
        "SELECT 1 FROM tenant_features WHERE tenant_id = $1 AND feature = $2 AND enabled", tenant_id, FLAG))


def bagi(v: Decimal, q: Decimal, basis: Decimal) -> Decimal:
    """Porsi selisih V untuk barang yang MASIH ada: Q on-hand >= basis -> seluruhnya; sebagian -> proporsional; habis -> 0."""
    if v == NOL or q <= NOL or basis <= NOL:
        return NOL
    return r2(v * min(Decimal("1"), q / basis))


def lipat(nilai_stok: Decimal, selisih: Decimal, dasar: Decimal) -> tuple:
    """Arah tagih_dulu (GRN SESUDAH tagihan): barang yang baru diterima seluruhnya masih ada -> porsi stok = bagi(selisih, dasar, dasar)
    dilipat ke harga masuk per satuan dasar. -> (biaya_dasar 2dp, persediaan = Δ kartu stok 2dp, sisa ke PPV = sen pembulatan)."""
    target = nilai_stok + bagi(selisih, dasar, dasar)
    biaya = max(NOL, r2(target / dasar))
    persediaan = r2(dasar * biaya)
    return biaya, persediaan, nilai_stok + selisih - persediaan


async def rencana(conn, tid: str, product_id, v: Decimal, basis: Decimal, gr_item_ids: list) -> dict | None:
    """-> {product_id, q, wac_lama, wac_baru, rata, persediaan (Δ kartu stok 2dp), gudang, kode, nama} atau None (tak ada revaluasi)."""
    if product_id is None or v == NOL:
        return None
    s = await conn.fetchrow(
        """SELECT COALESCE(SUM(quantity_in) - SUM(quantity_out), 0) AS q,
                  COALESCE(SUM(quantity_in * unit_cost) - SUM(quantity_out * unit_cost), 0) AS nilai
           FROM inventory_ledger WHERE tenant_id = $1 AND product_id = $2""", tid, product_id)
    q, nilai = Decimal(str(s["q"])), Decimal(str(s["nilai"]))
    target = bagi(v, q, basis)
    if target == NOL:
        return None
    wac_lama = Decimal(str(await conn.fetchval("SELECT get_weighted_average_cost($1, $2)", tid, product_id) or 0))
    wac_baru = max(NOL, r2(wac_lama + target / q))
    delta = r2(q * wac_baru - q * wac_lama)
    if delta == NOL:
        return None
    g = await conn.fetchrow(
        """SELECT gr.warehouse_id, p.item_code, p.nama_produk FROM goods_receipt_items i
           JOIN goods_receipts gr ON gr.id = i.goods_receipt_id JOIN products p ON p.id = i.item_id
           WHERE i.id = ANY($1::uuid[]) AND gr.tenant_id = $2 AND gr.warehouse_id IS NOT NULL ORDER BY gr.receipt_date LIMIT 1""",
        [UUID(str(x)) for x in gr_item_ids], tid) if gr_item_ids else None
    if g is None:  # tanpa gudang penerimaan -> tak bisa dipasangkan; seluruh selisih tetap ke PPV (aturan lama)
        return None
    return {"product_id": product_id, "q": q, "wac_lama": wac_lama, "wac_baru": wac_baru, "rata": r2((nilai + q * (wac_baru - wac_lama)) / q),
            "persediaan": delta, "gudang": g["warehouse_id"], "kode": g["item_code"], "nama": g["nama_produk"]}


async def _baris(conn, tid, uid, b, masuk, qty, biaya, rata, source_type, source_id, nomor, jid, tanggal, catatan):
    saldo = await conn.fetchval(
        "SELECT COALESCE(SUM(quantity_in) - SUM(quantity_out), 0) FROM inventory_ledger WHERE tenant_id = $1 AND product_id = $2",
        tid, b["product_id"])
    await conn.execute(
        """INSERT INTO inventory_ledger (id, tenant_id, product_id, product_code, product_name, movement_type, movement_date, source_type,
               source_id, source_number, quantity_in, quantity_out, quantity_balance, unit_cost, total_cost, average_cost, warehouse_id,
               journal_id, created_by, notes, created_at)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20, clock_timestamp())""",
        uuid4(), tid, b["product_id"], b["kode"], b["nama"], "REVALUASI_MASUK" if masuk else "REVALUASI_KELUAR", tanggal, source_type,
        source_id, nomor, qty if masuk else NOL, NOL if masuk else qty, Decimal(str(saldo)) + (qty if masuk else -qty), biaya,
        r2(qty * biaya), rata, b["gudang"], jid, uid, catatan)


async def tulis(conn, ctx: dict, b: dict, source_id, nomor: str, jid, tanggal) -> None:
    """Pasangan revaluasi (MASUK dulu, lalu KELUAR) di transaksi pemanggil; Δ nilai == b['persediaan'] (jurnal pemanggil)."""
    if not conn.is_in_transaction():
        raise RuntimeError("revaluasi_ppv.tulis wajib di dalam transaksi pemanggil (Law 23)")
    c = f"Revaluasi selisih harga {nomor}"
    await _baris(conn, ctx["tenant_id"], ctx.get("user_id"), b, True, b["q"], b["wac_baru"], b["rata"], SUMBER, source_id, nomor, jid, tanggal, c)
    await _baris(conn, ctx["tenant_id"], ctx.get("user_id"), b, False, b["q"], b["wac_lama"], b["rata"], SUMBER, source_id, nomor, jid, tanggal, c)


async def balik(conn, ctx: dict, source_id, jid_balik, tanggal) -> int:
    """Void: cermin setiap pasangan dengan urutan TERBALIK (cermin KELUAR = MASUK dulu) -> cache gudang tak negatif walau stok sudah pindah
    gudang; nilai kembali persis. rata sesudah = nilai/qty sesudah pembalik. -> jumlah baris dibalik."""
    tid = ctx["tenant_id"]
    rows = await conn.fetch(
        """SELECT * FROM inventory_ledger WHERE tenant_id = $1 AND source_type = $2 AND source_id = $3 ORDER BY created_at DESC, id DESC""",
        tid, SUMBER, source_id)
    for r in rows:
        s = await conn.fetchrow(
            """SELECT COALESCE(SUM(quantity_in) - SUM(quantity_out), 0) AS q,
                      COALESCE(SUM(quantity_in * unit_cost) - SUM(quantity_out * unit_cost), 0) AS nilai
               FROM inventory_ledger WHERE tenant_id = $1 AND product_id = $2""", tid, r["product_id"])
        masuk = Decimal(str(r["quantity_out"])) > NOL  # cermin baris KELUAR = MASUK
        qty, biaya = Decimal(str(r["quantity_out"] if masuk else r["quantity_in"])), Decimal(str(r["unit_cost"]))
        q2 = Decimal(str(s["q"])) + (qty if masuk else -qty)
        n2 = Decimal(str(s["nilai"])) + (qty * biaya if masuk else -qty * biaya)
        rata = r2(n2 / q2) if q2 > NOL else biaya
        b = {"product_id": r["product_id"], "kode": r["product_code"], "nama": r["product_name"], "gudang": r["warehouse_id"]}
        await _baris(conn, tid, ctx.get("user_id"), b, masuk, qty, biaya, rata, f"{SUMBER}_VOID", source_id, r["source_number"], jid_balik,
                     tanggal, f"VOID revaluasi {r['source_number']}")
    return len(rows)
