"""Daftar + ringkasan KONTAK (Pelanggan / Vendor) -- SATU penentu dua modul (B1, 4 Nov 2026; kontrak CONVERSATIONAL, desain disetujui
MASTER). Pola po_daftar: fakta per baris + predikat aksi SQL yang SAMA dipakai ?aksi=, ringkasan *_count/_value, dan aksi baris.

Angka dari JURNAL (Law 1/16/29; arap Rule 5): saldo = Σ outstanding compute_ar_outstanding / compute_ap_outstanding per pihak (termasuk
baris pihak tanpa dokumen: kredit NK tanpa faktur / saldo awal vendor, V314/V457); lewat tempo = baris ber-dokumen bersisa > 0 dengan
jatuh tempo < tanggal bisnis tenant. Decimal (Law 9), tanpa int(). Kredit vendor TIDAK dikirim sampai ada penentu jurnalnya.
"""
from decimal import Decimal
from typing import Optional

NOL = Decimal("0")

JENIS = {
    "customer": {"tabel": "customers", "fungsi": "compute_ar_outstanding", "pihak": "customer_id", "dok": "invoice_id",
                 "aksi": ("lewat_jatuh_tempo", "punya_piutang"), "nama": "nama", "telepon": "telepon", "kota": "city",
                 "kode": "nomor_member"},
    "vendor": {"tabel": "vendors", "fungsi": "compute_ap_outstanding", "pihak": "vendor_id", "dok": "bill_id",
               "aksi": ("lewat_jatuh_tempo", "punya_hutang"), "nama": "name", "telepon": ("mobile_phone", "phone"), "kota": "city",  # temuan A 9 Nov: No. HP form vendor = mobile_phone
               "kode": "code"},
}


def cte_fakta(jenis: str, p_hari: str) -> str:
    """CTE `x(pihak, saldo, lewat_nilai, lewat_jumlah)` per pihak dari fungsi jurnal ($1 = tenant, p_hari = tanggal bisnis)."""
    j = JENIS[jenis]
    lewat = f"{j['dok']} IS NOT NULL AND outstanding > 0 AND due_date < {p_hari}::date"
    return f"""x AS (SELECT {j['pihak']}::text AS pihak, SUM(outstanding) AS saldo,
                     COALESCE(SUM(outstanding) FILTER (WHERE {lewat}), 0) AS lewat_nilai,
                     COUNT(*) FILTER (WHERE {lewat}) AS lewat_jumlah
              FROM {j['fungsi']}($1) GROUP BY 1)"""


def sql_aksi(jenis: str, aksi: str) -> str:
    """Predikat SQL atas x (LEFT JOIN) -- SATU definisi untuk ?aksi=, ringkasan, dan aksi baris. Kunci asing -> ValueError."""
    if aksi not in JENIS[jenis]["aksi"]:
        raise ValueError(aksi)
    return "COALESCE(x.lewat_jumlah, 0) > 0" if aksi == "lewat_jatuh_tempo" else "COALESCE(x.saldo, 0) > 0"


def aksi_baris(jenis: str, f: dict) -> list:
    """Aksi baris dari fakta (aturan == sql_aksi), urut kemendesakan."""
    out = []
    for k in JENIS[jenis]["aksi"]:
        if (f["lewat_jumlah"] > 0) if k == "lewat_jatuh_tempo" else (f["saldo"] > 0):
            out.append(k)
    return out


async def fakta_kontak(conn, tid: str, jenis: str, ids: list, hari_ini) -> dict:
    """{id_teks: {saldo, lewat_tempo_nilai, lewat_tempo_jumlah}} untuk satu halaman (satu kueri)."""
    if not ids:
        return {}
    rows = await conn.fetch(f"WITH {cte_fakta(jenis, '$3')} SELECT * FROM x WHERE pihak = ANY($2::text[])",
                            tid, [str(i) for i in ids], hari_ini)
    out = {}
    for r in rows:
        out[r["pihak"]] = {"saldo": Decimal(str(r["saldo"] or 0)), "lewat_tempo_nilai": Decimal(str(r["lewat_nilai"] or 0)),
                           "lewat_tempo_jumlah": int(r["lewat_jumlah"] or 0)}
    return out


def fakta_kosong() -> dict:
    return {"saldo": NOL, "lewat_tempo_nilai": NOL, "lewat_tempo_jumlah": 0}


def telepon_teks(v: Optional[str]) -> Optional[str]:
    """0812 4306 5632 (kelompok 4 dari depan); bukan angka -> apa adanya."""
    if not v:
        return None
    d = "".join(ch for ch in str(v) if ch.isdigit() or ch == "+")
    if len(d) < 8 or not d.lstrip("+").isdigit():
        return str(v).strip() or None
    return " ".join(d[i:i + 4] for i in range(0, len(d), 4))


def kontak_teks(telepon, kota) -> Optional[str]:
    bagian = [b for b in (telepon_teks(telepon), (kota or "").strip() or None) if b]
    return " · ".join(bagian) or None


def telepon_baris(row, kolom) -> Optional[str]:
    """Kolom telepon kontak: nama kolom atau tuple prioritas (vendor: mobile_phone ?? phone) -- nilai tak kosong PERTAMA."""
    for k in (kolom if isinstance(kolom, tuple) else (kolom,)):
        v = row.get(k)
        if v is not None and str(v).strip():
            return v
    return None


def identitas_text(jenis: str, row) -> Optional[str]:
    """Satu baris identitas detail kontak (MASTER 9 Nov, pemilik setuju): "<kode> · <HP> · <kota> · <termin>", bagian kosong dilewati.
    HP = penentu SAMA dengan kontak_teks (telepon_baris; vendor mobile_phone ?? phone); termin = kontak_detail.termin_text ("Tunai" / "Termin n hari")."""
    from .kontak_detail import termin_text
    j = JENIS[jenis]
    t = termin_text(row.get("payment_terms_days"))
    bagian = [(str(row.get(j["kode"]) or "").strip() or None), telepon_teks(telepon_baris(row, j["telepon"])),
              (row.get(j["kota"]) or "").strip() or None, t if t == "Tunai" else f"Termin {t}"]
    return " · ".join(b for b in bagian if b) or None


def status_text(is_active) -> str:
    return "Aktif" if is_active else "Nonaktif"


def medan_baris(jenis: str, row, fakta: dict) -> dict:
    """Medan BARU per baris (aditif; medan lama router tetap)."""
    from ..schemas.uang_json import _uang_json as uang
    j = JENIS[jenis]
    f = fakta.get(str(row["id"]), fakta_kosong())
    berlaku = aksi_baris(jenis, {"saldo": f["saldo"], "lewat_jumlah": f["lewat_tempo_jumlah"]})
    # kontrak AKSI-SEMUA (pembaca FE pillAksi.medanAksiBaris): aksi = kunci PERTAMA (urut desak) atau null; aksi_semua = yang BERLAKU
    return {"saldo": uang(f["saldo"]), "lewat_tempo_nilai": uang(f["lewat_tempo_nilai"]), "lewat_tempo_jumlah": f["lewat_tempo_jumlah"],
            "kontak_teks": kontak_teks(telepon_baris(row, j["telepon"]), row.get(j["kota"])), "status_text": status_text(row["is_active"]),
            "aksi": berlaku[0] if berlaku else None, "aksi_semua": berlaku}


async def halaman(conn, tid: str, jenis: str, tabel_alias: str, where: str, params: list, idx: int, hari_ini, *, skip: int,
                  limit: int, aksi: Optional[str], with_totals: bool, urut: Optional[str] = None) -> dict:
    """Mode daftar BARU (?aksi= / with_totals): filter aksi + urut nama, id + total + sum_sisa dari CTE yang SAMA.
    where/params = syarat router (pencarian, is_active, dst.) dengan parameter $1..$(idx-1). -> {ids, total, sum_sisa?}."""
    j = JENIS[jenis]
    p_hari = f"${idx}"
    syarat = where + (f" AND {sql_aksi(jenis, aksi)}" if aksi else "")
    dasar = f"WITH {cte_fakta(jenis, p_hari)} SELECT {{}} FROM {j['tabel']} {tabel_alias} LEFT JOIN x ON x.pihak = {tabel_alias}.id::text WHERE {syarat}"
    arg = [*params, hari_ini]
    total = await conn.fetchval(dasar.format("COUNT(*)"), *arg)
    rows = await conn.fetch(dasar.format(f"{tabel_alias}.id") + f" ORDER BY {urut or tabel_alias + '.' + j['nama']}, {tabel_alias}.id"
                            f" LIMIT ${idx + 1} OFFSET ${idx + 2}", *arg, limit, skip)
    out = {"ids": [r["id"] for r in rows], "total": total}
    if with_totals:
        out["sum_sisa"] = Decimal(str(await conn.fetchval(dasar.format("COALESCE(SUM(x.saldo), 0)"), *arg) or 0))
    return out


async def ringkasan(conn, tid: str, jenis: str, tabel_alias: str, where: str, params: list, idx: int, hari_ini) -> dict:
    """GET /{jenis}/summary: predikat SAMA dengan daftar. -> {total, aktif, nonaktif, total_value, <aksi>_count, <aksi>_value}."""
    from ..schemas.uang_json import _uang_json as uang
    j = JENIS[jenis]
    kol = [f"COUNT(*) AS total", f"COUNT(*) FILTER (WHERE {tabel_alias}.is_active) AS aktif",
           f"COUNT(*) FILTER (WHERE NOT {tabel_alias}.is_active) AS nonaktif", "COALESCE(SUM(x.saldo), 0) AS total_value"]
    for k in j["aksi"]:
        nilai = "x.lewat_nilai" if k == "lewat_jatuh_tempo" else "x.saldo"
        kol += [f"COUNT(*) FILTER (WHERE {sql_aksi(jenis, k)}) AS {k}_count",
                f"COALESCE(SUM({nilai}) FILTER (WHERE {sql_aksi(jenis, k)}), 0) AS {k}_value"]
    r = await conn.fetchrow(f"WITH {cte_fakta(jenis, f'${idx}')} SELECT {', '.join(kol)} FROM {j['tabel']} {tabel_alias} "
                            f"LEFT JOIN x ON x.pihak = {tabel_alias}.id::text WHERE {where}", *params, hari_ini)
    return {k: (uang(v) if isinstance(v, Decimal) else int(v or 0)) for k, v in dict(r).items()}


def uang_json(v):
    """Decimal -> JSON number (bulat int, bersen float) -- schemas/uang_json, satu aturan."""
    from ..schemas.uang_json import _uang_json
    return _uang_json(v)
