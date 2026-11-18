"""FORM BARANG CW -- STOK AWAL PER GUDANG (putusan pemilik 14 Nov 2026; MASTER 18 Nov: ikut Accurate 'Saldo Awal' + QBO 'Inventory Starting Value').

SATU penentu (rencana) untuk pratinjau Buat Barang, penulis POST /items, dan impor: tiap baris = gudang + jumlah + satuan + harga per satuan itu; server mengonversi ke
SATUAN DASAR (konversi yang dikirim bersama barang). Jurnal: SATU jurnal per barang (Dr Persediaan / Cr Ekuitas Saldo Awal = Σ nilai gudang) + SATU baris kartu stok
OPENING_BALANCE per gudang ("Saldo awal") lewat saldo_awal_stok.catat_saldo_awal_per_gudang -- BUKAN dokumen Penyesuaian. Aturan tetap: usaha yang sudah punya transaksi tak bisa
menambah saldo awal (trigger guard_opening_balance V114) -> blok berkode, stok dibuat lewat Penyesuaian setelah barang dibuat; barang ber-batch/kedaluwarsa ditunda ke penerimaan;
harga 0 ditolak (stok harus bernilai di pembukuan); periode tertutup ditolak."""
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional
from uuid import UUID

from fastapi import HTTPException

from . import saldo_awal_stok as SAS
from . import teks_galat as tg
from . import teks_laporan_persediaan as TLP
from ..utils.tanggal_tenant import tanggal_dokumen

NOL = Decimal("0")
BATAS_BARIS = 50


def _blok(code: str, message: str, field: Optional[str] = None) -> dict:
    return {"code": code, "message": message, "field": field}


def _norm(u: Optional[str]) -> str:
    return " ".join(str(u or "").split()).lower()


def faktor_satuan(body, unit: Optional[str]) -> Optional[Decimal]:
    """Faktor ke satuan dasar dari konversi yang dikirim bersama barang; None = satuan tak dikenal. Kosong / sama dgn satuan dasar = 1."""
    if not _norm(unit) or _norm(unit) == _norm(body.base_unit):
        return Decimal("1")
    for c in body.conversions or []:
        if _norm(c.conversion_unit) == _norm(unit):
            return Decimal(str(c.conversion_factor))
    return None


async def tanggal_mulai_data(conn, tid: str) -> date:
    """Tanggal bawaan stok awal: tanggal saldo awal tertua yang sudah ada di pembukuan usaha; belum ada = tanggal bisnis hari ini (zona tenant)."""
    d = await conn.fetchval("SELECT MIN(journal_date) FROM journal_entries WHERE tenant_id = $1 AND status = 'POSTED' AND is_opening_balance", tid)
    return d or await tanggal_dokumen(conn, tid)


async def rencana(conn, tid: str, body, inventory_account_id=None) -> Optional[dict]:
    """None = permintaan tak membawa stok_awal (jalur opening_stock lama). Selain itu -> {blocks[{code,message,field}], baris[...], total_nilai, tanggal, efek[]}. Hanya MEMBACA."""
    if not body.stok_awal:
        return None
    blocks: list = []
    hasil = {"blocks": blocks, "baris": [], "total_nilai": NOL, "tanggal": None, "efek": []}
    if body.item_type != "goods" or not body.track_inventory:
        blocks.append(_blok("STOK_AWAL_BUKAN_BARANG_STOK", "Stok awal hanya untuk barang yang melacak stok; jasa dan barang non-stok tidak punya stok.", "stok_awal"))
        return hasil
    if body.track_batches or body.track_expiry:
        blocks.append(_blok("STOK_AWAL_BARANG_BATCH", "Barang ber-batch atau kedaluwarsa: stok awalnya dicatat saat Penerimaan Barang atau Faktur Pembelian, supaya batch dan tanggal kedaluwarsanya terisi.", "stok_awal"))
        return hasil
    if len(body.stok_awal) > BATAS_BARIS:
        blocks.append(_blok("STOK_AWAL_TERLALU_BANYAK", f"Stok awal maksimal {BATAS_BARIS} gudang per barang.", "stok_awal"))
        return hasil
    try:
        tgl = date.fromisoformat(str(body.opening_stock_date)) if body.opening_stock_date else await tanggal_mulai_data(conn, tid)
    except (ValueError, TypeError):
        blocks.append(_blok("STOK_AWAL_TANGGAL_TAK_SAH", "Tanggal stok awal harus berbentuk TTTT-BB-HH.", "opening_stock_date"))
        return hasil
    hasil["tanggal"] = tgl
    rate_cadangan = Decimal(str(body.opening_stock_rate if body.opening_stock_rate else (body.purchase_price or 0)))
    terlihat, total = set(), NOL
    for i, b in enumerate(body.stok_awal):
        f = f"stok_awal[{i}]"
        try:
            wid = UUID(str(b.warehouse_id))
        except (ValueError, TypeError):
            blocks.append(_blok("STOK_AWAL_GUDANG_TAK_ADA", f"Gudang baris {i + 1} tidak ditemukan di usaha ini.", f"{f}.warehouse_id"))
            continue
        g = await conn.fetchrow("SELECT id, name, is_active, is_system FROM warehouses WHERE tenant_id = $1 AND id = $2", tid, wid)
        if not g or g["is_system"]:
            blocks.append(_blok("STOK_AWAL_GUDANG_TAK_ADA", f"Gudang baris {i + 1} tidak ditemukan di usaha ini.", f"{f}.warehouse_id"))
            continue
        if not g["is_active"]:
            blocks.append(_blok("STOK_AWAL_GUDANG_NONAKTIF", f"Gudang {g['name']} tidak aktif; pilih gudang aktif.", f"{f}.warehouse_id"))
            continue
        if wid in terlihat:
            blocks.append(_blok("STOK_AWAL_GUDANG_GANDA", f"Gudang {g['name']} muncul dua kali; gabungkan jadi satu baris.", f"{f}.warehouse_id"))
            continue
        terlihat.add(wid)
        fk = faktor_satuan(body, b.unit)
        if fk is None:
            blocks.append(_blok("SATUAN_TAK_TERKONVERSI", tg.satuan_tak_terkonversi(b.unit, body.name, body.base_unit), f"{f}.unit"))
            continue
        qty_dasar = Decimal(str(b.quantity)) * fk
        harga_satuan = Decimal(str(b.unit_cost)) if b.unit_cost is not None else None
        rate_dasar = (harga_satuan / fk).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP) if harga_satuan is not None else rate_cadangan
        try:
            nilai = SAS.periksa_nilai(qty_dasar, rate_dasar)
        except HTTPException as e:
            blocks.append(_blok("STOK_AWAL_HARGA_NOL", str(e.detail), f"{f}.unit_cost"))
            continue
        total += nilai
        masuk = _norm(b.unit) and _norm(b.unit) != _norm(body.base_unit)
        hasil["baris"].append({"warehouse_id": wid, "gudang": g["name"], "qty_dasar": qty_dasar, "rate_dasar": rate_dasar, "nilai": nilai,
                               "unit_masuk": b.unit if masuk else None, "qty_masuk": Decimal(str(b.quantity)) if masuk else None})
        hasil["efek"].append({
            "jenis": "saldo_awal_stok", "warehouse_id": str(wid), "gudang": g["name"], "qty": str(qty_dasar), "harga": str(rate_dasar), "nilai": f"{nilai:.2f}",
            "unit": body.base_unit, "unit_input": b.unit if masuk else None, "quantity_input": str(b.quantity) if masuk else None,
            "qty_text": (f"{tg.qty(b.quantity)} {b.unit} = {tg.qty(qty_dasar)} {body.base_unit}" if masuk else f"{tg.qty(qty_dasar)} {body.base_unit}"),
            "harga_text": TLP.harga(rate_dasar), "nilai_text": TLP.uang(nilai, 2)})
    if blocks:
        return hasil
    hasil["total_nilai"] = total
    if await conn.fetchval(SAS.SQL_SUDAH_BERJALAN, tid):
        blocks.append(_blok("STOK_AWAL_USAHA_BERJALAN", "Usaha ini sudah punya transaksi, jadi stok awal tidak bisa diisi saat membuat barang. "
                            "Simpan barang dulu, lalu tambahkan stoknya lewat Penyesuaian stok.", "stok_awal"))
        return hasil
    periode = await conn.fetchrow("SELECT status FROM fiscal_periods WHERE tenant_id = $1 AND start_date <= $2 AND end_date >= $2", tid, tgl)
    if periode and periode["status"] != "OPEN":
        blocks.append(_blok("STOK_AWAL_PERIODE_TERTUTUP", tg.periode_tertutup(None, periode["status"]), "opening_stock_date"))
        return hasil
    try:
        await SAS._akun(conn, tid, inventory_account_id, SAS.AccountRole.INVENTORY_MERCHANDISE, "Persediaan")
        await SAS._akun(conn, tid, None, SAS.AccountRole.EQUITY_OPENING_BALANCE, "Modal Saldo Awal")
    except HTTPException as e:
        blocks.append(_blok("STOK_AWAL_AKUN_BELUM_DIATUR", str(e.detail), "stok_awal"))
    return hasil


def galat_pertama(r: dict) -> HTTPException:
    """Penulis: blok pertama -> galat berkode (kode + kalimat + field SAMA dgn pratinjau)."""
    from .galat_berkode import GalatBerkode
    b = r["blocks"][0]
    status = 409 if b["code"] in ("STOK_AWAL_GUDANG_GANDA", "STOK_AWAL_AKUN_BELUM_DIATUR", "STOK_AWAL_PERIODE_TERTUTUP") else 422
    return GalatBerkode(status, b["code"], b["message"], b.get("field"))
