"""B1/B2 stok baris (BUG-049/075, 6 Nov 2026; putusan MASTER): SATU penentu stok untuk pemilih barang dan Tinjau penjualan.

  stok_tanggal = SUM(inventory_ledger in - out) dengan movement_date <= tanggal   (isyarat transaksi bertanggal mundur)
  stok_kini    = SUM(inventory_ledger in - out) semua tanggal                     (Law 1/16 versi kuantitas: ledger, bukan cache)
  dipesan      = SUM per barang (qty baris SO - terkirim) atas SO terbuka (bukan draft/cancelled/completed), satuan dasar
  bisa_dijual  = stok_kini - dipesan   (pola NetSuite/Accurate "available" = sekarang)

warehouse_stock.reserved_quantity TIDAK dipakai: tak satu pun penulis mengisinya (diukur 6 Nov, 28/28 baris = 0). warehouse_id:
stok hanya baris ledger gudang itu (14 baris ledger tanpa gudang ikut di "semua gudang" saja); dipesan hanya SO bergudang sama --
SO tanpa gudang dihitung di tampilan semua gudang saja (catatan). Kirim tanpa tautan baris tak bisa diatribusikan ke barang
(catatan). Satuan = satuan_dasar (penentu yang sama dengan ROW 109). Hanya MEMBACA: aman di transaksi READ ONLY dan di pratinjau
tulis-lalu-rollback (kecuali_sumber membuang gerak dokumen yang sedang dipratinjau; kecuali_so membuang pesanannya sendiri).
"""
from datetime import date
from decimal import Decimal
from typing import Iterable, Optional

from . import satuan_dasar as SD
from . import teks_galat as tg
from .so_kirim import terkirim_per_baris

NOL = Decimal("0")

SQL_STOK = """
SELECT il.product_id,
       COALESCE(SUM(COALESCE(il.quantity_in, 0) - COALESCE(il.quantity_out, 0)) FILTER (WHERE il.movement_date <= $3), 0) AS stok_tanggal,
       COALESCE(SUM(COALESCE(il.quantity_in, 0) - COALESCE(il.quantity_out, 0)), 0) AS stok_kini
FROM inventory_ledger il
WHERE il.tenant_id = $1 AND il.product_id = ANY($2::uuid[])
  AND ($4::uuid IS NULL OR il.warehouse_id = $4)
  AND (il.source_id IS NULL OR il.source_id <> ALL($5::uuid[]))
GROUP BY il.product_id"""

SQL_BARANG = """SELECT id, nama_produk, COALESCE(track_inventory, false) AS lacak FROM products WHERE tenant_id = $1 AND id = ANY($2::uuid[])"""

SQL_SO_TERBUKA = """
SELECT so.id AS so_id, so.warehouse_id, soi.id AS soi_id, soi.item_id, soi.quantity, soi.unit
FROM sales_orders so JOIN sales_order_items soi ON soi.sales_order_id = so.id
WHERE so.tenant_id = $1 AND soi.item_id = ANY($2::uuid[]) AND so.status <> ALL($3::text[])
  AND COALESCE(soi.perlu_kirim, true) AND ($4::uuid IS NULL OR so.id <> $4)"""


def angka(x: Optional[Decimal]) -> Optional[str]:
    """Decimal -> string kontrak (4 desimal, tanpa nol buntut); None tetap None."""
    if x is None:
        return None
    q = Decimal(x).quantize(SD.SKALA_QTY)
    return format(q.quantize(SD.SATU) if q == q.to_integral_value() else q.normalize(), "f")


async def hitung(conn, tid: str, item_ids: Iterable, tanggal: date, warehouse_id=None, kecuali_so=None,
                 kecuali_sumber: Iterable = ()) -> dict:
    """-> {"barang": {str(id): {nama, lacak_stok, satuan_dasar, stok_tanggal, stok_kini, dipesan, bisa_dijual}}, "catatan": [..], "ktx"}.
    Barang tanpa lacak stok: angka None (bukan 0). Barang tak ada di tenant: tak muncul di "barang"."""
    ids = sorted({str(i) for i in item_ids if i})
    ktx = await SD.muat(conn, tid, ids)
    hasil = {"barang": {}, "catatan": [], "ktx": ktx}
    if not ids:
        return hasil
    stok = {str(r["product_id"]): r for r in await conn.fetch(SQL_STOK, tid, ids, tanggal, warehouse_id, list(kecuali_sumber))}
    from .sj_so import STATUS_SO_TERTUTUP  # lambat: sj_so mengimpor jalur tulis (hindari impor melingkar dari router)
    dipesan = {i: NOL for i in ids}
    so_rows = await conn.fetch(SQL_SO_TERBUKA, tid, ids, list(STATUS_SO_TERTUTUP), kecuali_so)
    tanpa_gudang = {r["so_id"] for r in so_rows if r["warehouse_id"] is None}
    if warehouse_id is not None:
        so_rows = [r for r in so_rows if r["warehouse_id"] is not None and str(r["warehouse_id"]) == str(warehouse_id)]
        if tanpa_gudang:
            hasil["catatan"].append(tg.stok_so_tanpa_gudang(len(tanpa_gudang)))
    per_baris, tanpa_tautan = await terkirim_per_baris(conn, tid, sorted({r["so_id"] for r in so_rows}))
    if any(v > 0 for v in tanpa_tautan.values()):
        hasil["catatan"].append(tg.stok_kirim_tanpa_tautan())
    for r in so_rows:
        sisa = Decimal(str(r["quantity"])) - per_baris.get(r["soi_id"], NOL)
        if sisa <= 0:
            continue
        h = SD.hitung(ktx, r["item_id"], r["unit"], sisa, "konversi")
        if h["blok"]:  # satuan SO tak terkonversi: tak bisa dijumlahkan dalam satuan dasar -> catatan (sekali), bukan angka karangan
            if h["blok"]["message"] not in hasil["catatan"]:
                hasil["catatan"].append(h["blok"]["message"])
            continue
        dipesan[str(r["item_id"])] += h["qty_dasar"]
    for r in await conn.fetch(SQL_BARANG, tid, ids):
        i = str(r["id"])
        b = ktx["barang"].get(i) or {}
        x = {"nama": r["nama_produk"], "lacak_stok": r["lacak"], "satuan_dasar": b.get("dasar"),
             "stok_tanggal": None, "stok_kini": None, "dipesan": None, "bisa_dijual": None}
        if r["lacak"]:
            s = stok.get(i)
            x["stok_tanggal"] = Decimal(str(s["stok_tanggal"])) if s else NOL
            x["stok_kini"] = Decimal(str(s["stok_kini"])) if s else NOL
            x["dipesan"] = dipesan[i]
            x["bisa_dijual"] = x["stok_kini"] - dipesan[i]
        hasil["barang"][i] = x
    return hasil


def baris(h: dict, item_id, unit: Optional[str]) -> dict:
    """B1: satu baris respons /api/inventory/stok-baris; angka dalam `unit` bila terkonversi (dibagi faktor), selain itu blok."""
    b = h["barang"].get(str(item_id))
    if b is None:
        return {"item_id": str(item_id), "lacak_stok": False, "satuan_dasar": None, "satuan": unit, "faktor": None,
                "stok_tanggal": None, "stok_kini": None, "dipesan": None, "bisa_dijual": None,
                "blok": {"code": "BARANG_TIDAK_DITEMUKAN", "message": tg.barang_tidak_ditemukan()}}
    f = SD.hitung(h["ktx"], item_id, unit, SD.SATU, "konversi")
    out = {"item_id": str(item_id), "lacak_stok": b["lacak_stok"], "satuan_dasar": b["satuan_dasar"],
           "satuan": unit if SD.normal(unit) else b["satuan_dasar"], "faktor": angka(f["faktor"]), "blok": None}
    if f["blok"]:
        out.update(faktor=None, blok={"code": f["blok"]["code"], "message": f["blok"]["message"]})
    for k in ("stok_tanggal", "stok_kini", "dipesan", "bisa_dijual"):
        out[k] = None if (b[k] is None or f["blok"]) else angka(b[k] / f["faktor"])
    return out


async def isyarat(conn, tid: str, tanggal: date, hari_ini: date, lines: Iterable[dict], warehouse_id=None, kecuali_so=None,
                  kecuali_sumber: Iterable = ()) -> list:
    """B2: [{item_id, kalimat}] -- ISYARAT, bukan blok. Satu kalimat per barang ber-lacak-stok (baris ganda dijumlah dalam satuan
    dasar). Tanggal mundur dan qty > stok per tanggal -> kalimat tanggal; selain itu qty > bisa dijual -> kalimat bisa dijual.
    lines = [{item_id, quantity, unit}]; baris tanpa barang / satuan tak terkonversi dilewati (penjaga satuan dokumen yang menolak)."""
    lines = [x for x in lines if x.get("item_id")]
    if not lines:
        return []
    h = await hitung(conn, tid, [x["item_id"] for x in lines], tanggal, warehouse_id, kecuali_so, kecuali_sumber)
    minta, satuan = {}, {}
    for x in lines:
        i = str(x["item_id"])
        b = h["barang"].get(i)
        q = SD.hitung(h["ktx"], i, x.get("unit"), x.get("quantity"), "konversi")
        if b is None or not b["lacak_stok"] or q["blok"]:
            continue
        minta[i] = minta.get(i, NOL) + q["qty_dasar"]
        # dikunci per satuan KANON ("dus" == "Dus" == singkatan kamus); nama tampil = ketikan pertama
        satuan.setdefault(i, {}).setdefault(SD.kanon(x.get("unit") or b["satuan_dasar"], h["ktx"]["alias"]),
                                            (q["faktor"], x.get("unit") or b["satuan_dasar"]))
    out = []
    for i, q in minta.items():
        b = h["barang"][i]
        # satu satuan di semua baris barang ini -> kalimat dalam satuan itu; campuran -> satuan dasar
        f, nama_satuan = next(iter(satuan[i].values())) if len(satuan[i]) == 1 else (SD.SATU, b["satuan_dasar"])
        if tanggal < hari_ini and q > b["stok_tanggal"]:
            k = tg.stok_per_tanggal(b["nama"], tanggal, b["stok_tanggal"] / f, b["stok_kini"] / f, nama_satuan)
        elif q > b["bisa_dijual"]:
            k = tg.stok_bisa_dijual(b["nama"], b["bisa_dijual"] / f, b["dipesan"] / f, q / f, nama_satuan)
        else:
            continue
        out.append({"item_id": i, "kalimat": k})
    return out
