"""LAPORAN MUTASI PERSEDIAAN per periode (24 Nov 2026; setara Accurate) -- GET /api/reports/inventory-mutation.

Per barang (atau per gudang+barang): awal + masuk − keluar + pindah + revaluasi = akhir, untuk qty DAN nilai; identitas berlaku per baris dan per total.
Nilai = Σ total_cost bertanda di inventory_ledger (biaya yang dicatat bersama jurnalnya), BUKAN qty × HPP terakhir: HPP terakhir tak bisa membagi nilai per gerak
dan akan menyisakan selisih pembulatan. Laporan nilai (inventory-valuation) tetap qty × HPP, jadi keduanya boleh beda sebatas pembulatan WAC 2dp.
- pindah = pemindahan antargudang: bersih 0 per barang, tak menggembungkan masuk/keluar.  - revaluasi = pasangan PPV: qty bersih 0, hanya nilai.
- pembalik/void tampil sebagai gerak berlawanan pada tanggalnya sendiri (ledger append-only, Rule 8).
REKONSILIASI (SEMUA barang & gudang, tanpa saringan) awal/mutasi/akhir lawan GL akun peran INVENTORY_MERCHANDISE. GL memakai status POSTED, bukan
is_effective_journal: penentu itu membuang jurnal asal DAN pembaliknya sekaligus, sehingga saldo di antara kedua tanggal salah, padahal ledger memuat keduanya.
Selisih DILAPORKAN. Hanya MEMBACA."""
from datetime import date
from decimal import Decimal
from typing import Optional

from . import gudang_transit as GT
from . import teks_galat as tg
from .laporan_nilai_persediaan import BATAS_BARIS, SEN, SQL_BARANG, LaporanTerlaluBesar, _d, _r2

KOLOM = ("awal", "masuk", "keluar", "pindah", "revaluasi", "akhir")

_PINDAH = "(il.source_type LIKE 'RELOKASI%' OR il.source_type LIKE 'STOCK\\_TRANSFER%')"
_REVALUASI = "(il.source_type LIKE 'PPV\\_REVALUASI%')"
_BIASA = f"NOT {_PINDAH} AND NOT {_REVALUASI}"
_Q = "(COALESCE(il.quantity_in, 0) - COALESCE(il.quantity_out, 0))"
_N = "(CASE WHEN COALESCE(il.quantity_out, 0) > 0 THEN -il.total_cost ELSE il.total_cost END)"
_KELUAR = "COALESCE(il.quantity_out, 0) > 0"


def _kolom(nama: str, x: str) -> str:
    dalam = "il.movement_date >= $2"
    return ", ".join(f"COALESCE(SUM({e}) FILTER (WHERE {w}), 0) AS {nama}_{k}" for k, e, w in (
        ("awal", x, "il.movement_date < $2"),
        ("masuk", x, f"{dalam} AND {_BIASA} AND NOT ({_KELUAR})"),
        ("keluar", f"-{x}", f"{dalam} AND {_BIASA} AND {_KELUAR}"),
        ("pindah", x, f"{dalam} AND {_PINDAH}"),
        ("revaluasi", x, f"{dalam} AND {_REVALUASI}"),
        ("akhir", x, "TRUE")))


def _sql(gudang: bool) -> str:
    g = "il.warehouse_id" if gudang else "NULL::uuid"
    return (f"SELECT il.product_id, {g} AS warehouse_id, MAX(il.product_name) AS nama_ledger, {_kolom('q', _Q)}, {_kolom('n', _N)} "
            "FROM inventory_ledger il WHERE il.tenant_id = $1 AND il.movement_date <= $3 AND ($4::uuid IS NULL OR il.warehouse_id = $4) "
            f"GROUP BY il.product_id{', il.warehouse_id' if gudang else ''}")


SQL_BARIS_BARANG, SQL_BARIS_GUDANG = _sql(False), _sql(True)
SQL_SEMUA = (f"SELECT COALESCE(SUM({_N}) FILTER (WHERE il.movement_date < $2), 0) AS awal, COALESCE(SUM({_N}), 0) AS akhir "
             "FROM inventory_ledger il WHERE il.tenant_id = $1 AND il.movement_date <= $3")
SQL_GL = ("SELECT COALESCE(SUM(jl.debit - jl.credit) FILTER (WHERE je.journal_date < $3), 0) AS awal, COALESCE(SUM(jl.debit - jl.credit), 0) AS akhir "
          "FROM journal_lines jl JOIN journal_entries je ON je.id = jl.journal_id "
          "WHERE je.tenant_id = $1 AND jl.account_id = $2 AND je.journal_date <= $4 AND je.status = 'POSTED'")
SQL_GUDANG = "SELECT id, name, is_system FROM warehouses WHERE tenant_id = $1 AND id = ANY($2::uuid[])"


def _qty_text(q: Decimal) -> str:
    return tg.angka_laporan(q, 0) if q == q.to_integral_value() else tg.angka_laporan(q, 2 if q == q.quantize(SEN) else 4)


def rekonsiliasi(ledger, gl, presisi: int) -> dict:
    """ledger/gl = {awal, akhir}; gl None = peran INVENTORY_MERCHANDISE belum dipetakan. Toleransi 1 sen: kedua sisi dicatat 2dp dari angka yang sama."""
    def sisi(x):
        aw, ak = _r2(x["awal"]), _r2(x["akhir"])
        return {"awal": aw, "mutasi": ak - aw, "akhir": ak}
    sub = sisi(ledger)
    buku = sisi(gl) if gl is not None else None
    bagian = {}
    for k, v in sub.items():
        g = buku[k] if buku else None
        s = v - g if buku else None
        bagian[k] = {"ledger": float(v), "ledger_text": tg.angka_laporan(v, presisi), "gl": None if g is None else float(g),
                     "gl_text": None if g is None else tg.angka_laporan(g, presisi), "selisih": None if s is None else float(s),
                     "selisih_text": None if s is None else tg.angka_laporan(s, presisi)}
    if buku is None:
        status, teks = "tak_terpetakan", "Akun persediaan belum dipetakan"
    elif all(abs(sub[k] - buku[k]) <= SEN for k in sub):
        status, teks = "cocok", "Cocok dengan buku besar"
    else:
        status, teks = "selisih", f"Selisih {tg.rp(sub['akhir'] - buku['akhir'])} dengan buku besar pada saldo akhir"
        if abs(sub["akhir"] - buku["akhir"]) <= SEN:
            teks = f"Saldo akhir cocok, saldo awal selisih {tg.rp(sub['awal'] - buku['awal'])} dengan buku besar"
    return {"status": status, "status_text": teks, **bagian}


async def muat_mutasi_persediaan(conn, tid: str, dari: date, sampai: date, presisi: int, kelompok: str = "barang", gudang_id=None,
                                 kategori: Optional[str] = None, sertakan_nol: bool = False, akun_persediaan=None) -> dict:
    kat = (kategori or "").strip() or None
    ledger = await conn.fetchrow(SQL_SEMUA, tid, dari, sampai)
    gl = None if akun_persediaan is None else await conn.fetchrow(SQL_GL, tid, str(akun_persediaan), dari, sampai)
    per_gudang = kelompok == "gudang"
    rows = await conn.fetch(SQL_BARIS_GUDANG if per_gudang else SQL_BARIS_BARANG, tid, dari, sampai, None if gudang_id is None else str(gudang_id))
    if not sertakan_nol:
        rows = [r for r in rows if any(_d(r[f"{a}_{k}"]) != 0 for a in ("q", "n") for k in KOLOM)]
    info = {str(r["id"]): r for r in await conn.fetch(SQL_BARANG, tid, sorted({str(r["product_id"]) for r in rows}))} if rows else {}
    gud = {}
    if per_gudang and rows:
        gud = {str(r["id"]): (GT.LABEL if r["is_system"] else r["name"])
               for r in await conn.fetch(SQL_GUDANG, tid, sorted({str(r["warehouse_id"]) for r in rows if r["warehouse_id"]}))}
    baris, total = [], {k: Decimal(0) for k in KOLOM}
    for r in rows:
        b = info.get(str(r["product_id"]))
        if kat and (b is None or b["kategori"] != kat):
            continue
        # barang yang masternya hilang tetap tampil (nama dari ledger): nilainya masih dimuat GL
        item = {"item_id": str(r["product_id"]), "kode": b["kode"] if b else None, "nama": b["nama"] if b else (r["nama_ledger"] or "Barang terhapus"),
                "kategori": b["kategori"] if b else None, "satuan": b["satuan"] if b else None, "minus": _d(r["q_akhir"]) < 0}
        for k in KOLOM:
            q, n = _d(r[f"q_{k}"]), _r2(r[f"n_{k}"])
            item[k] = {"qty": float(q), "qty_text": _qty_text(q), "nilai": float(n), "nilai_text": tg.angka_laporan(n, presisi)}
            total[k] += n
        if per_gudang:
            g = str(r["warehouse_id"]) if r["warehouse_id"] else None
            item["gudang"] = {"id": g, "nama": gud.get(g, "Tanpa gudang")}
        baris.append(item)
    if len(baris) > BATAS_BARIS:
        raise LaporanTerlaluBesar()
    baris.sort(key=lambda x: ((x.get("gudang") or {}).get("nama") or "", x["kode"] or "~", x["nama"] or "", x["item_id"]))
    return {"dari": dari.isoformat(), "sampai": sampai.isoformat(), "periode_text": tg.rentang_tanggal(dari, sampai), "kelompok": kelompok,
            "saringan": {"gudang_id": None if gudang_id is None else str(gudang_id), "kategori": kat, "sertakan_nol": bool(sertakan_nol)},
            "satuan_text": "(Disajikan dalam Rupiah)",
            "kolom_text": {"awal": "Saldo awal", "masuk": "Masuk", "keluar": "Keluar", "pindah": "Pindah gudang", "revaluasi": "Penyesuaian nilai", "akhir": "Saldo akhir"},
            "baris": baris, "jumlah_baris": len(baris), "jumlah_minus": sum(1 for x in baris if x["minus"]),
            "total": {k: {"nilai": float(v), "nilai_text": tg.angka_laporan(v, presisi)} for k, v in total.items()},   # qty lintas satuan tak dijumlah
            "rekonsiliasi": rekonsiliasi(ledger, gl, presisi), "catatan_rekonsiliasi": "Rekonsiliasi memakai SEMUA barang dan gudang (saringan tak berlaku)."}
