"""FIFO unit 1 (25 Nov 2026; putusan pemilik T1-T8): hitungan lapisan MURNI + pembaca. BELUM dipakai jalur mana pun (flag fifo_v1 gelap).
Lapisan terurut (layer_date, seq). Lapisan yang dihabiskan menyerahkan SELURUH sisa nilainya -> Σ nilai tak pernah bocor sesen pun."""
from decimal import ROUND_HALF_UP, Decimal
from typing import Iterable, List, NamedTuple, Optional

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


# ---- Unit 2 (25 Nov 2026): sambungan ke helper kanonik. Hanya hidup bila flag fifo_v1 tenant NYALA + barang fifo + costing_mulai terisi. ----

async def fifo_aktif(conn, tid: str, product_id) -> bool:
    """SATU penentu: barang ini dihitung FIFO? Flag mati / barang WAC / belum ada costing_mulai -> False (jalur WAC lama tak tersentuh)."""
    return (await conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM tenant_features f JOIN products p ON p.tenant_id = f.tenant_id "
        "WHERE f.tenant_id = $1 AND f.feature = $2 AND f.enabled AND p.id = $3 "
        "AND p.costing_method = 'fifo' AND p.costing_mulai IS NOT NULL)", tid, FLAG, product_id)) is True   # EXISTS = bool asli; selain True -> WAC


def nilai_masuk(qty, unit_cost) -> Decimal:
    """Nilai lapisan masuk = qty x biaya satuan, 2dp HALF_UP (Law 25)."""
    return (Decimal(str(qty)) * Decimal(str(unit_cost))).quantize(SEN, rounding=ROUND_HALF_UP)


async def catat_masuk(conn, tid: str, product_id, ledger_id, tanggal, qty, nilai: Decimal, batch_id=None) -> None:
    """Satu baris ledger masuk = satu lapisan (aturan 3.1). Batch hanya keterangan (T3)."""
    await kunci(conn, tid, [product_id])
    await conn.execute(
        "INSERT INTO inventory_cost_layers (tenant_id, product_id, source_ledger_id, jenis, layer_date, batch_id, qty_awal, nilai_awal) "
        "VALUES ($1, $2, $3, 'masuk', $4, $5, $6, $7)", tid, product_id, ledger_id, tanggal, batch_id, Decimal(str(qty)), nilai)


async def ambil_keluar(conn, tid: str, product_id, qty) -> List[Ambil]:
    """Kunci lalu rencanakan (T4: kurang -> StokFifoKurang). Dipanggil SEBELUM baris ledger ditulis."""
    await kunci(conn, tid, [product_id])
    return await rencana_keluar(conn, tid, product_id, Decimal(str(qty)))


async def catat_pakai(conn, tid: str, ledger_id, ambil: List[Ambil]) -> None:
    for a in ambil:
        await conn.execute(
            "INSERT INTO inventory_layer_moves (tenant_id, layer_id, ledger_id, jenis, qty, nilai) VALUES ($1, $2::uuid, $3, 'pakai', $4, $5)",
            tid, a.layer_id, ledger_id, -a.qty, -a.nilai)


class Balik(NamedTuple):
    gerak: List[tuple]   # (layer_id, qty, nilai) yang akan ditulis sebagai moves
    jenis: str           # 'balik' (keluar asal) | 'pakai' (masuk asal dikosongkan)
    nilai: Decimal       # nilai yang dibalik = total_cost baris pembalik (positif)


def _tolak_sebelum_mulai():
    """Baris asal tercatat sebelum barang memakai FIFO (tanpa lapisan/moves): membaliknya akan memisahkan stok dari lapisan (F1)."""
    from .galat_berkode import GalatBerkode
    raise GalatBerkode(409, "FIFO_SEBELUM_MULAI", "Dokumen ini tercatat sebelum metode FIFO dipakai; gunakan penyesuaian stok.")


async def rencana_balik(conn, tid: str, product_id, ledger_asal, masuk_asal: bool) -> Balik:
    """Pembalik baris ledger_asal (dipanggil SEBELUM baris pembalik ditulis). Pemanggil hanya memanggil untuk barang FIFO aktif; baris asal tanpa jejak lapisan -> 409.
    Keluar asal -> 'balik' ke lapisan ASAL dengan qty+nilai asal (nilai pulih persis). Masuk asal -> hanya bila lapisan masih utuh (T5)."""
    await kunci(conn, tid, [product_id])
    if not masuk_asal:
        rows = await conn.fetch(
            "SELECT layer_id, qty, nilai FROM inventory_layer_moves WHERE tenant_id = $1 AND ledger_id = $2 AND jenis = 'pakai' ORDER BY seq",
            tid, ledger_asal)
        if not rows:
            _tolak_sebelum_mulai()
        g = [(r["layer_id"], -Decimal(str(r["qty"])), -Decimal(str(r["nilai"]))) for r in rows]
        return Balik(g, "balik", sum((x[2] for x in g), NOL))
    lap = await conn.fetchrow(
        "SELECT l.id, l.qty_awal, l.nilai_awal, EXISTS (SELECT 1 FROM inventory_layer_moves m WHERE m.layer_id = l.id) AS gerak "
        "FROM inventory_cost_layers l WHERE l.tenant_id = $1 AND l.source_ledger_id = $2", tid, ledger_asal)
    if lap is None:
        _tolak_sebelum_mulai()
    if lap["gerak"]:
        from .galat_berkode import GalatBerkode
        raise GalatBerkode(409, "FIFO_LAPISAN_TERPAKAI", "Stok dari dokumen ini sudah terpakai; gunakan retur atau penyesuaian.")
    n = Decimal(str(lap["nilai_awal"]))
    return Balik([(lap["id"], -Decimal(str(lap["qty_awal"])), -n)], "pakai", n)


async def catat_balik(conn, tid: str, ledger_baru, b: Balik) -> None:
    for layer_id, q, n in b.gerak:
        await conn.execute(
            "INSERT INTO inventory_layer_moves (tenant_id, layer_id, ledger_id, jenis, qty, nilai) VALUES ($1, $2, $3, $4, $5, $6)",
            tid, layer_id, ledger_baru, b.jenis, q, n)


async def rata_sisa_sesudah(conn, tid: str, product_id, ambil: List[Ambil]) -> Optional[Decimal]:
    """average_cost baris keluar FIFO = nilai sisa ÷ qty sisa SESUDAH ambil (dibaca sebelum moves ditulis). Sisa 0 -> None (pemanggil pakai cadangan)."""
    r = await conn.fetchrow(
        "SELECT COALESCE(SUM(l.qty_awal), 0) + COALESCE((SELECT SUM(m.qty) FROM inventory_layer_moves m WHERE m.tenant_id = $1 AND m.layer_id IN "
        "(SELECT id FROM inventory_cost_layers WHERE tenant_id = $1 AND product_id = $2)), 0) AS q, "
        "COALESCE(SUM(l.nilai_awal), 0) + COALESCE((SELECT SUM(m.nilai) FROM inventory_layer_moves m WHERE m.tenant_id = $1 AND m.layer_id IN "
        "(SELECT id FROM inventory_cost_layers WHERE tenant_id = $1 AND product_id = $2)), 0) AS n "
        "FROM inventory_cost_layers l WHERE l.tenant_id = $1 AND l.product_id = $2", tid, product_id)
    q = Decimal(str(r["q"])) - sum((a.qty for a in ambil), NOL)
    n = Decimal(str(r["n"])) - sum((a.nilai for a in ambil), NOL)
    return (n / q).quantize(SEN, rounding=ROUND_HALF_UP) if q > NOL else None
