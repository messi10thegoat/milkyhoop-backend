"""BUG-257 (16 Nov 2026; putusan MASTER): histori harga jual per PELANGGAN + barang (Accurate: tombol "Histori" di baris Pesanan Penjualan).

Sumber = baris FAKTUR PENJUALAN terposting non-void (harga yang benar-benar jadi transaksi; draf, void, dan faktur pembuka saldo awal tak ikut --
definisi sama dgn services/pelanggan_penjualan). Urut tanggal faktur turun, seri created_at turun lalu line_number turun. Hanya MEMBACA.
Harga = unit_price tercatat di baris (per SATUAN BARIS, sebelum diskon baris); `unit` diminta -> SERVER mengonversi harga + qty ke satuan itu lewat
satuan_dasar (SATU penentu satuan; faktor = satuan dasar per satuan). Baris yang satuannya tak terkonversi tampil APA ADANYA (dikonversi=false),
bukan angka karangan. FE tak menghitung."""
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from . import satuan_dasar as SD

SEN = Decimal("0.01")
SKALA_QTY = Decimal("0.0001")
BATAS_MAKS = 50

TEKS_STATUS = {"posted": "Terposting", "partial": "Dibayar sebagian", "paid": "Lunas", "overdue": "Jatuh tempo"}

_DARI_DAN_SYARAT = """
    FROM sales_invoices si
    JOIN sales_invoice_items sii ON sii.invoice_id = si.id
    WHERE si.tenant_id = $1 AND si.customer_id = $2 AND sii.item_id = $3
      AND si.status NOT IN ('draft', 'void') AND NOT si.is_opening_balance
      AND ($4::uuid IS NULL OR si.id <> $4)"""

SQL_RIWAYAT = ("SELECT si.id AS doc_id, si.invoice_number AS nomor, si.invoice_date AS tanggal, si.status,"
               " sii.quantity, sii.unit, sii.unit_price, sii.discount_percent" + _DARI_DAN_SYARAT +
               " ORDER BY si.invoice_date DESC, si.created_at DESC, sii.line_number DESC LIMIT $5")
SQL_TOTAL = "SELECT COUNT(*) AS n" + _DARI_DAN_SYARAT


def _r2(x) -> Decimal:
    return Decimal(str(x)).quantize(SEN, rounding=ROUND_HALF_UP)


def _angka_qty(q: Decimal) -> float:
    q = q.quantize(SKALA_QTY, rounding=ROUND_HALF_UP)
    return float(q.quantize(Decimal("1")) if q == q.to_integral_value() else q.normalize())


def ke_baris_respons(r: dict, ktx: dict, item_id, unit: Optional[str]) -> dict:
    """Satu baris DB -> baris respons. unit kosong = satuan asli baris. Konversi: harga_t = harga x f_t / f_baris; qty_t = qty x f_baris / f_t."""
    harga, qty, satuan, dikonversi = _r2(r["unit_price"]), Decimal(str(r["quantity"])), r["unit"], False
    if SD.normal(unit):
        hb = SD.hitung(ktx, item_id, r["unit"], SD.SATU, "konversi")
        ht = SD.hitung(ktx, item_id, unit, SD.SATU, "konversi")
        if not hb["blok"] and not ht["blok"]:
            if SD.kanon(r["unit"], ktx["alias"]) != SD.kanon(unit, ktx["alias"]):   # baris tanpa satuan = satuan dasar (faktor 1 dari hitung)
                harga = _r2(harga * ht["faktor"] / hb["faktor"])
                qty = qty * hb["faktor"] / ht["faktor"]
            satuan, dikonversi = unit, True
    return {
        "tanggal": r["tanggal"].isoformat(), "doc_type": "sales_invoice", "doc_id": str(r["doc_id"]), "nomor": r["nomor"],
        "status_text": TEKS_STATUS.get(r["status"], "Terposting"),
        "qty": _angka_qty(qty), "unit": satuan, "harga": float(harga),
        "diskon_pct": float(Decimal(str(r["discount_percent"] or 0))), "dikonversi": dikonversi,
    }


async def riwayat(conn, tid: str, customer_id, item_id, unit: Optional[str], limit: int, exclude_doc_id=None) -> dict:
    """-> {items: [...], total}. total = SEMUA baris riwayat (tanpa limit, sesudah exclude_doc_id). Satuan diminta yang tak dikenal barang -> GalatBerkode 400."""
    limit = max(1, min(int(limit), BATAS_MAKS))
    n = await conn.fetchval(SQL_TOTAL, tid, str(customer_id), str(item_id), None if exclude_doc_id is None else str(exclude_doc_id))
    if not n:
        return {"items": [], "total": 0}
    rows = await conn.fetch(SQL_RIWAYAT, tid, str(customer_id), str(item_id), None if exclude_doc_id is None else str(exclude_doc_id), limit)
    ktx = await SD.muat(conn, tid, [item_id]) if SD.normal(unit) else {"alias": {}, "barang": {}}
    if SD.normal(unit):
        SD.tolak_bila(SD.hitung(ktx, item_id, unit, SD.SATU, "konversi"))   # satuan diminta tak dikenal barang ini: 400 berkalimat
    return {"items": [ke_baris_respons(dict(r), ktx, item_id, unit) for r in rows], "total": int(n)}
