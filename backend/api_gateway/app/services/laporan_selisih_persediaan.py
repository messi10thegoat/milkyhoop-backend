"""LAPORAN SELISIH SALDO AKUN PERSEDIAAN vs NILAI BARANG per periode (28 Nov 2026; ikut Accurate; GO MASTER) -- GET /api/reports/inventory-gl-difference.

Per DOKUMEN dalam periode: nilai barang = Σ total_cost bertanda inventory_ledger (kartu stok) vs nilai akun = Σ(debit − credit) jurnal POSTED pada akun peran
INVENTORY_MERCHANDISE. Pemasangan: kartu stok ber-journal_id -> jurnal itu; kartu tanpa journal_id -> jurnal dengan source_id sama (bila ada);
sisanya berdiri sendiri ("Tanpa jurnal" / "Tanpa kartu stok"). Yang tampil hanya dokumen yang selisihnya > 1 sen. Ringkasan = saldo awal/mutasi/akhir
(rekonsiliasi SAMA dengan Mutasi Persediaan). Hanya MEMBACA: penjelas selisih, tidak memperbaiki apa pun."""
from datetime import date
from decimal import Decimal
from typing import Optional

from . import teks_galat as tg
from .laporan_mutasi_persediaan import SQL_GL as SQL_GL_SALDO, SQL_SEMUA, _N, rekonsiliasi
from .laporan_nilai_persediaan import BATAS_BARIS, SEN, LaporanTerlaluBesar, _d, _r2

SQL_KARTU = (f"SELECT il.journal_id, il.source_type, il.source_id::text AS source_id, MAX(il.source_number) AS nomor, MIN(il.movement_date) AS tanggal, "
             f"SUM({_N}) AS nilai FROM inventory_ledger il WHERE il.tenant_id = $1 AND il.movement_date BETWEEN $2 AND $3 "
             "GROUP BY il.journal_id, il.source_type, il.source_id")
SQL_JURNAL = ("SELECT je.id, je.journal_number, je.journal_date, je.source_type, je.source_id::text AS source_id, je.description, "
              "SUM(jl.debit - jl.credit) AS nilai FROM journal_lines jl JOIN journal_entries je ON je.id = jl.journal_id "
              "WHERE je.tenant_id = $1 AND jl.account_id = $2 AND je.journal_date BETWEEN $3 AND $4 AND je.status = 'POSTED' "
              "GROUP BY je.id, je.journal_number, je.journal_date, je.source_type, je.source_id, je.description")

JENIS_TEKS = {"tanpa_jurnal": "Tanpa jurnal persediaan", "tanpa_kartu": "Tanpa kartu stok", "beda_nilai": "Nilai berbeda"}


def pasangkan(kartu: list, jurnal: list) -> list:
    """Murni. kartu: [{journal_id, source_type, source_id, nomor, tanggal, nilai}]; jurnal: [{id, journal_number, journal_date, source_type, source_id, nilai}].
    -> grup [{kartu: [..], jurnal: [..]}]."""
    jur = {str(j["id"]): j for j in jurnal}
    per_sumber: dict = {}
    for j in jurnal:
        if j.get("source_id"):
            per_sumber.setdefault(str(j["source_id"]), []).append(str(j["id"]))
    grup: dict = {}
    dipakai: set = set()
    for k in kartu:
        jid = str(k["journal_id"]) if k.get("journal_id") else None
        if jid is None or jid not in jur:
            calon = per_sumber.get(str(k.get("source_id") or ""), [])
            jid = calon[0] if len(calon) == 1 else jid
        kunci = f"J:{jid}" if jid else f"S:{k.get('source_type')}:{k.get('source_id')}"
        g = grup.setdefault(kunci, {"kartu": [], "jurnal": []})
        g["kartu"].append(k)
        if jid and jid in jur and jid not in dipakai:
            g["jurnal"].append(jur[jid])
            dipakai.add(jid)
    for jid, j in jur.items():
        if jid not in dipakai:
            grup.setdefault(f"J:{jid}", {"kartu": [], "jurnal": []})["jurnal"].append(j)
    return list(grup.values())


def baris_selisih(grup: list, presisi: int) -> list:
    out = []
    for g in grup:
        nb = _r2(sum((_d(k["nilai"]) for k in g["kartu"]), Decimal(0)))
        na = _r2(sum((_d(j["nilai"]) for j in g["jurnal"]), Decimal(0)))
        sel = nb - na
        if abs(sel) <= SEN:
            continue
        jenis = "tanpa_jurnal" if not g["jurnal"] else "tanpa_kartu" if not g["kartu"] else "beda_nilai"
        k0, j0 = (g["kartu"] or [None])[0], (g["jurnal"] or [None])[0]
        tgl = (k0 or {}).get("tanggal") or (j0 or {}).get("journal_date")
        out.append({"tanggal": tgl.isoformat() if hasattr(tgl, "isoformat") else tgl,
                    "dokumen": (k0 or {}).get("nomor") or (j0 or {}).get("journal_number"),
                    "sumber": (k0 or j0 or {}).get("source_type"), "source_id": (k0 or j0 or {}).get("source_id"),
                    "jurnal": [{"id": str(j["id"]), "nomor": j["journal_number"]} for j in g["jurnal"]],
                    "nilai_barang": float(nb), "nilai_barang_text": tg.angka_laporan(nb, presisi),
                    "nilai_akun": float(na), "nilai_akun_text": tg.angka_laporan(na, presisi),
                    "selisih": float(sel), "selisih_text": tg.angka_laporan(sel, presisi),
                    "jenis": jenis, "jenis_text": JENIS_TEKS[jenis]})
    out.sort(key=lambda x: (x["tanggal"] or "", x["dokumen"] or ""))
    return out


async def muat_selisih_persediaan(conn, tid: str, dari: date, sampai: date, presisi: int, akun_persediaan=None) -> dict:
    led = await conn.fetchrow(SQL_SEMUA, tid, dari, sampai)
    if akun_persediaan is None:
        return {"periode": {"dari": dari.isoformat(), "sampai": sampai.isoformat()}, "ringkasan": rekonsiliasi(led, None, presisi),
                "baris": [], "jumlah_baris": 0, "total_selisih": None, "total_selisih_text": None,
                "catatan": "Akun persediaan belum dipetakan; selisih tidak bisa dihitung."}
    gl = await conn.fetchrow(SQL_GL_SALDO, tid, str(akun_persediaan), dari, sampai)
    kartu = [dict(r) for r in await conn.fetch(SQL_KARTU, tid, dari, sampai)]
    jurnal = [dict(r) for r in await conn.fetch(SQL_JURNAL, tid, str(akun_persediaan), dari, sampai)]
    baris = baris_selisih(pasangkan(kartu, jurnal), presisi)
    if len(baris) > BATAS_BARIS:
        raise LaporanTerlaluBesar()
    total = _r2(sum((_d(b["selisih"]) for b in baris), Decimal(0)))
    return {"periode": {"dari": dari.isoformat(), "sampai": sampai.isoformat()}, "ringkasan": rekonsiliasi(led, gl, presisi),
            "baris": baris, "jumlah_baris": len(baris), "total_selisih": float(total), "total_selisih_text": tg.angka_laporan(total, presisi),
            "satuan_text": "(Disajikan dalam Rupiah)",
            "catatan": "Nilai barang = kartu stok; nilai akun = jurnal akun persediaan. Hanya dokumen yang selisih yang ditampilkan."}
