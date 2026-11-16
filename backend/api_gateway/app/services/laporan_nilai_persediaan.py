"""BUG-272 (A) (16 Nov 2026; putusan MASTER): LAPORAN NILAI PERSEDIAAN per tanggal (Accurate: Kuantitas/Nilai Barang per Gudang) -- GET /api/reports/inventory-valuation.

Per barang (kelompok=barang) atau per gudang+barang (kelompok=gudang): qty = Σ(masuk − keluar) inventory_ledger sampai per_tanggal (gudang bila disaring);
HPP/satuan = average_cost gerakan TERAKHIR barang itu sampai per_tanggal (urutan aplikasi created_at,id -- sama dgn verify_inventory_wac_reconciliation_all, V181;
WAC milik BARANG, bukan gudang); nilai = qty × HPP (2dp HALF_UP). REKONSILIASI: Σ(qty × HPP) SEMUA barang (tanpa saringan) dibandingkan dgn saldo GL akun peran
INVENTORY_MERCHANDISE per tanggal (jurnal efektif, Law 1/16); toleransi = Σ(|qty| × 0,005) + 0,01 (batas pembulatan WAC 2dp, sama dgn V181); selisih DILAPORKAN, bukan disembunyikan.
Barang nonaktif/terhapus yang masih bernilai tetap ikut (GL masih memuat nilainya). Hanya MEMBACA."""
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from . import teks_galat as tg

SEN = Decimal("0.01")
BATAS_BARIS = 5000

_GERAKAN = ("FROM inventory_ledger il WHERE il.tenant_id = $1 AND il.movement_date <= $2")

SQL_WAC = ("SELECT DISTINCT ON (il.product_id) il.product_id, il.average_cost " + _GERAKAN +
           " ORDER BY il.product_id, il.created_at DESC, il.id DESC")

# qty per (barang[, gudang]) -- gudang HANYA menyaring qty; HPP tetap milik barang (SQL_WAC tanpa saringan gudang)
SQL_QTY_BARANG = ("SELECT il.product_id, NULL::uuid AS warehouse_id, COALESCE(SUM(COALESCE(il.quantity_in, 0) - COALESCE(il.quantity_out, 0)), 0) AS qty " + _GERAKAN +
                  " AND ($3::uuid IS NULL OR il.warehouse_id = $3) GROUP BY il.product_id")
SQL_QTY_GUDANG = ("SELECT il.product_id, il.warehouse_id, COALESCE(SUM(COALESCE(il.quantity_in, 0) - COALESCE(il.quantity_out, 0)), 0) AS qty " + _GERAKAN +
                  " AND ($3::uuid IS NULL OR il.warehouse_id = $3) GROUP BY il.product_id, il.warehouse_id")
SQL_QTY_SEMUA = ("SELECT il.product_id, COALESCE(SUM(COALESCE(il.quantity_in, 0) - COALESCE(il.quantity_out, 0)), 0) AS qty " + _GERAKAN + " GROUP BY il.product_id")

SQL_BARANG = ("SELECT p.id, p.item_code AS kode, p.nama_produk AS nama, p.kategori, COALESCE(NULLIF(p.base_unit, ''), p.satuan) AS satuan "
              "FROM products p WHERE p.tenant_id = $1 AND p.id = ANY($2::uuid[])")
SQL_GUDANG = "SELECT id, name FROM warehouses WHERE tenant_id = $1 AND id = ANY($2::uuid[])"
SQL_GL = ("SELECT COALESCE(SUM(jl.debit - jl.credit), 0) AS gl FROM journal_lines jl JOIN journal_entries je ON je.id = jl.journal_id "
          "WHERE je.tenant_id = $1 AND jl.account_id = $2 AND je.journal_date <= $3 AND is_effective_journal(je.id)")


def _d(x) -> Decimal:
    return Decimal(str(x if x is not None else 0))


def _r2(x) -> Decimal:
    return _d(x).quantize(SEN, rounding=ROUND_HALF_UP)


class LaporanTerlaluBesar(Exception):
    pass


def rekonsiliasi(qty_hpp, gl: Optional[Decimal], presisi: int) -> dict:
    """qty_hpp = [(qty, hpp)] SEMUA barang. gl None = peran INVENTORY_MERCHANDISE belum dipetakan. -> status cocok | selisih | tak_terpetakan."""
    sub = _r2(sum((_d(q) * _d(h) for q, h in qty_hpp), Decimal(0)))
    tol = _r2(sum((abs(_d(q)) * Decimal("0.005") for q, _ in qty_hpp), Decimal(0)) + SEN)
    if gl is None:
        return {"status": "tak_terpetakan", "status_text": "Akun persediaan belum dipetakan", "sub_ledger": float(sub), "gl": None, "selisih": None, "toleransi": float(tol),
                "sub_ledger_text": tg.angka_laporan(sub, presisi), "gl_text": None, "selisih_text": None}
    gl = _r2(gl)
    sel = _r2(sub - gl)
    ok = abs(sel) <= tol
    return {"status": "cocok" if ok else "selisih",
            "status_text": "Cocok dengan buku besar" if ok else f"Selisih {tg.rp(sel)} dengan buku besar",
            "sub_ledger": float(sub), "gl": float(gl), "selisih": float(sel), "toleransi": float(tol),
            "sub_ledger_text": tg.angka_laporan(sub, presisi), "gl_text": tg.angka_laporan(gl, presisi), "selisih_text": tg.angka_laporan(sel, presisi)}


async def muat_nilai_persediaan(conn, tid: str, per_tanggal: date, presisi: int, kelompok: str = "barang", gudang_id=None, kategori: Optional[str] = None,
                                sertakan_nol: bool = False, akun_persediaan=None) -> dict:
    kat = (kategori or "").strip() or None
    wac = {str(r["product_id"]): _d(r["average_cost"]) for r in await conn.fetch(SQL_WAC, tid, per_tanggal)}
    semua = await conn.fetch(SQL_QTY_SEMUA, tid, per_tanggal)
    gl = None
    if akun_persediaan is not None:
        gl = _d(await conn.fetchval(SQL_GL, tid, str(akun_persediaan), per_tanggal))
    rekon = rekonsiliasi([(r["qty"], wac.get(str(r["product_id"]), Decimal(0))) for r in semua], gl, presisi)
    rows = await conn.fetch(SQL_QTY_GUDANG if kelompok == "gudang" else SQL_QTY_BARANG, tid, per_tanggal, None if gudang_id is None else str(gudang_id))
    rows = [r for r in rows if sertakan_nol or _d(r["qty"]) != 0]
    info = {str(r["id"]): r for r in await conn.fetch(SQL_BARANG, tid, sorted({str(r["product_id"]) for r in rows}))} if rows else {}
    nama_gudang = {}
    if kelompok == "gudang" and rows:
        nama_gudang = {str(r["id"]): r["name"] for r in await conn.fetch(SQL_GUDANG, tid, sorted({str(r["warehouse_id"]) for r in rows if r["warehouse_id"]}))}
    baris = []
    for r in rows:
        b = info.get(str(r["product_id"]))
        if b is None or (kat and b["kategori"] != kat):
            continue
        qty, hpp = _d(r["qty"]), wac.get(str(r["product_id"]), Decimal(0))
        nilai = _r2(qty * hpp)
        item = {"item_id": str(r["product_id"]), "kode": b["kode"], "nama": b["nama"], "kategori": b["kategori"], "satuan": b["satuan"],
                "qty": float(qty), "qty_text": tg.angka_laporan(qty, 0) if qty == qty.to_integral_value() else tg.angka_laporan(qty, 2),
                "hpp_satuan": float(_r2(hpp)), "hpp_satuan_text": tg.angka_laporan(_r2(hpp), presisi),
                "nilai": float(nilai), "nilai_text": tg.angka_laporan(nilai, presisi), "minus": qty < 0}
        if kelompok == "gudang":
            item["gudang"] = {"id": str(r["warehouse_id"]) if r["warehouse_id"] else None, "nama": nama_gudang.get(str(r["warehouse_id"]), "Tanpa gudang")}
        baris.append(item)
    baris.sort(key=lambda x: ((x.get("gudang") or {}).get("nama") or "", x["kode"] or "~", x["nama"] or ""))
    if len(baris) > BATAS_BARIS:
        raise LaporanTerlaluBesar()
    total = sum((_d(x["nilai"]) for x in baris), Decimal(0))
    return {"per_tanggal": per_tanggal.isoformat(), "kelompok": kelompok,
            "saringan": {"gudang_id": None if gudang_id is None else str(gudang_id), "kategori": kat, "sertakan_nol": bool(sertakan_nol)},
            "satuan_text": "(Disajikan dalam Rupiah)", "baris": baris, "jumlah_baris": len(baris), "jumlah_minus": sum(1 for x in baris if x["minus"]),
            "total_nilai": float(_r2(total)), "total_nilai_text": tg.angka_laporan(total, presisi),
            "rekonsiliasi": rekon, "catatan_rekonsiliasi": "Rekonsiliasi memakai SEMUA barang dan gudang (saringan tak berlaku)."}
