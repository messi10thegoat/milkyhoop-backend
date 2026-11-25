"""FIFO unit 1 (25 Nov 2026; putusan pemilik T1-T8): hitungan lapisan MURNI + pembaca. BELUM dipakai jalur mana pun (flag fifo_v1 gelap).
Lapisan terurut (layer_date, seq). Lapisan yang dihabiskan menyerahkan SELURUH sisa nilainya -> Σ nilai tak pernah bocor sesen pun."""
from decimal import ROUND_HALF_UP, Decimal
from typing import Iterable, List, NamedTuple

FLAG = "fifo_v1"
NOL = Decimal("0")
SEN = Decimal("0.01")


class Lapisan(NamedTuple):
    id: str
    qty: Decimal      # sisa
    nilai: Decimal    # sisa


class Ambil(NamedTuple):
    layer_id: str
    qty: Decimal
    nilai: Decimal


class StokFifoKurang(Exception):
    """T4: barang FIFO tak boleh keluar melebihi lapisan (= stok)."""

    def __init__(self, diminta: Decimal, tersedia: Decimal):
        self.diminta, self.tersedia = diminta, tersedia
        super().__init__(f"diminta {diminta}, tersedia {tersedia}")


def alokasi(lapisan: Iterable[Lapisan], qty: Decimal) -> List[Ambil]:
    """Makan lapisan dari yang terdepan. qty wajib > 0."""
    if qty <= NOL:
        raise ValueError("qty keluar FIFO wajib > 0")
    isi = [x for x in lapisan if x.qty > NOL]
    tersedia = sum((x.qty for x in isi), NOL)
    if qty > tersedia:
        raise StokFifoKurang(qty, tersedia)
    sisa, hasil = qty, []
    for x in isi:
        if sisa == NOL:
            break
        q = min(sisa, x.qty)
        nilai = x.nilai if q == x.qty else min(x.nilai, (x.nilai * q / x.qty).quantize(SEN, rounding=ROUND_HALF_UP))
        hasil.append(Ambil(x.id, q, nilai))
        sisa -= q
    return hasil


SQL_SISA = ("SELECT l.id::text AS id, l.qty_awal + COALESCE(SUM(m.qty), 0) AS qty, l.nilai_awal + COALESCE(SUM(m.nilai), 0) AS nilai "
            "FROM inventory_cost_layers l LEFT JOIN inventory_layer_moves m ON m.layer_id = l.id AND m.tenant_id = l.tenant_id "
            "WHERE l.tenant_id = $1 AND l.product_id = $2 GROUP BY l.id HAVING l.qty_awal + COALESCE(SUM(m.qty), 0) > 0 ORDER BY l.layer_date, l.seq")


async def kunci(conn, tid: str, product_ids) -> None:
    """Law 13: satu kunci per barang, urut id supaya dokumen multi-barang tak saling mengunci."""
    for p in sorted({str(x) for x in product_ids}):
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"STOCK_LAYER:{tid}:{p}")


async def rencana_keluar(conn, tid: str, product_id, qty: Decimal) -> List[Ambil]:
    """SATU penentu untuk pratinjau dan tulis (penulis memanggil kunci() dulu)."""
    rows = await conn.fetch(SQL_SISA, tid, product_id)
    return alokasi([Lapisan(r["id"], Decimal(str(r["qty"])), Decimal(str(r["nilai"]))) for r in rows], Decimal(str(qty)))
