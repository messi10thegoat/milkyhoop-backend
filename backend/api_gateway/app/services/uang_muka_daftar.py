"""Data daftar/detail UANG MUKA seragam dengan Pesanan/Faktur (S2-UM, 20 Okt 2026, MASTER). Server menghitung, FE memformat.

  sisa              = jurnal (routers/customer_deposits.compute_deposit_remaining_many / SQL_SISA_UANG_MUKA), BUKAN cache.
                      Untuk filter & ringkasan (semua uang muka tenant sekaligus) teks SQL DITURUNKAN dari konstanta yang sama
                      (sql_sisa_semua) -- digerbang per uang muka == compute_deposit_remaining_many.
  aksi              = 'belum_diterapkan' : terbit (bukan draf/void) DAN sisa jurnal > 0 (pill + ?aksi= + summary: predikat SAMA)
  sisa_keadaan      = draf | utuh | sebagian | terpakai | dikembalikan | sebagian_dikembalikan | batal
                      (dari status_detail yang sudah ada; konsistensinya dengan sisa jurnal digerbang harness)
  status_text       = label layar teks_galat.status_id("dp", status_detail) == peta FE r288 (statusUangMuka.ts)
`remaining_state` lama ("posted" | "draft_belum_diposting") TIDAK diubah -- FE live membacanya (sisaUangMuka.ts).
"""
from decimal import Decimal
from typing import Optional

AKSI_BELUM_DITERAPKAN = "belum_diterapkan"
AKSI_UM = (AKSI_BELUM_DITERAPKAN,)
KEADAAN_UM = ("draf", "utuh", "sebagian", "terpakai", "dikembalikan", "sebagian_dikembalikan", "batal")
_KEADAAN_DARI_DETAIL = {"draft": "draf", "posted": "utuh", "partial": "sebagian", "applied": "terpakai",
                        "refunded": "dikembalikan", "partially_refunded": "sebagian_dikembalikan", "void": "batal"}
CTE_SISA = "sisa_um"


def sql_sisa_semua(p_akun: str) -> str:
    """Sisa jurnal SEMUA uang muka bukan-draf tenant $1 -> (id, remaining). Diturunkan TEKS dari SQL_SISA_UANG_MUKA (satu sumber):
    daftar id `unnest($2)` diganti uang muka tenant, `$3` (akun peran CUSTOMER_DEPOSIT_LIABILITY) diganti `p_akun`."""
    from ..routers.customer_deposits import SQL_SISA_UANG_MUKA as S
    asal = "unnest($2::uuid[]) AS u(x)"
    assert S.count(asal) == 1 and S.count("ANY($2::uuid[])") == 3 and S.count("$3") == 1
    s = S.replace(asal, "(SELECT cd0.id FROM customer_deposits cd0 WHERE cd0.tenant_id = $1 AND cd0.status <> 'draft') AS u(x)")
    s = s.replace("ANY($2::uuid[])", "ANY(ARRAY(SELECT id FROM d))")
    return s.replace("$3", p_akun)


def sql_sisa_baris(alias: str = "customer_deposits") -> str:
    """Sisa jurnal satu baris daftar (CTE sisa_um harus ada di kueri). Draf = NULL."""
    return f"(SELECT s.remaining FROM {CTE_SISA} s WHERE s.id = {alias}.id)"


def sql_aksi(aksi: str, alias: str = "customer_deposits") -> str:
    """Predikat SQL kunci aksi; kembaran Python: aksi_um()."""
    if aksi != AKSI_BELUM_DITERAPKAN:
        raise ValueError(aksi)
    return f"({alias}.status NOT IN ('draft', 'void') AND COALESCE({sql_sisa_baris(alias)}, 0) > 0)"


def aksi_um(status: str, sisa) -> Optional[str]:
    if status in ("draft", "void") or sisa is None:
        return None
    return AKSI_BELUM_DITERAPKAN if Decimal(str(sisa)) > 0 else None


def keadaan_um(status_detail: str) -> str:
    return _KEADAAN_DARI_DETAIL.get(status_detail, status_detail)


def status_text_um(status_detail: str) -> str:
    from . import teks_galat as tg
    return tg.status_id("dp", status_detail)


def keadaan_cocok_sisa(keadaan: str, amount, sisa) -> bool:
    """Gerbang (harness): keadaan dari status_detail konsisten dengan sisa JURNAL."""
    if keadaan in ("draf",):
        return sisa is None
    s, a = Decimal(str(sisa or 0)), Decimal(str(amount or 0))
    if keadaan == "batal":
        return s == 0
    if keadaan == "utuh":
        return s == a and a > 0
    if keadaan in ("terpakai", "dikembalikan"):
        return s == 0
    return 0 < s < a  # sebagian / sebagian_dikembalikan


def _urut(ds: list) -> list:
    return sorted(ds, key=lambda d: (d["date"] or "", d["number"] or ""))  # = ORDER BY tanggal, nomor pembentuk


async def kelompok_untuk(conn, tid: str, uang_muka: list) -> dict:
    """{deposit_id: [kelompok]} bundel UANG MUKA (S2-UM b, 20 Okt 2026), urutan = bundel SO/faktur: rekap · pnw · pro · kw · ki · nk.
    rekap/pnw/pro = SO induk (sales_order_id atau lewat proforma) + proforma ASAL uang muka ini; kw = kwitansi uang muka ITU SENDIRI
    (apa pun statusnya); ki = surat jalan + faktur tempat diterapkan (aplikasi aktif); nk = nota kredit asal (KRD). Pembentuk dokumen
    HANYA dari services/dokumen_bundel; di sini hanya PEMILIHAN id. Dipakai bundel /customer-deposits/{id}/documents DAN documents[] daftar."""
    from . import dokumen_bundel as DB
    ids = [u["id"] for u in uang_muka]
    if not ids:
        return {}
    rows = {r["id"]: r for r in await conn.fetch(
        """SELECT cd.id, cd.proforma_id, COALESCE(cd.sales_order_id, p.sales_order_id) AS so_id FROM customer_deposits cd
           LEFT JOIN proformas p ON p.id = cd.proforma_id AND p.tenant_id = cd.tenant_id
           WHERE cd.tenant_id = $1 AND cd.id = ANY($2::uuid[])""", tid, ids)}
    so_ids = sorted({r["so_id"] for r in rows.values() if r["so_id"]})
    so = {r["id"]: r for r in await conn.fetch(
        "SELECT id, status, quote_id FROM sales_orders WHERE tenant_id = $1 AND id = ANY($2::uuid[])", tid, so_ids)} if so_ids else {}
    apl = {}
    for r in await conn.fetch(
            """SELECT deposit_id, invoice_id FROM customer_deposit_applications
               WHERE tenant_id = $1 AND deposit_id = ANY($2::uuid[]) AND status = 'active'""", tid, ids):
        if r["invoice_id"] not in apl.setdefault(r["deposit_id"], []):
            apl[r["deposit_id"]].append(r["invoice_id"])
    inv = sorted({i for v in apl.values() for i in v})
    fak = {d["id"]: d for d in await DB.faktur(conn, tid, inv)}
    sj_per_inv = {}
    if inv:
        for r in await conn.fetch("SELECT id, invoice_id FROM invoice_fulfillments WHERE tenant_id = $1 AND invoice_id = ANY($2::uuid[])",
                                  tid, inv):
            sj_per_inv.setdefault(r["invoice_id"], []).append(r["id"])
    sj = {d["id"]: d for d in await DB.surat_jalan(conn, tid, [i for v in sj_per_inv.values() for i in v])}
    nk_asal = {r["created_deposit_id"]: r["id"] for r in await conn.fetch(
        """SELECT id, created_deposit_id FROM credit_notes WHERE tenant_id = $1 AND created_deposit_id = ANY($2::uuid[])
           AND status NOT IN ('draft', 'void')""", tid, ids)}
    nk = {d["id"]: d for d in await DB.nota_kredit(conn, tid, list(nk_asal.values()))}
    kw = await DB.kwitansi_uang_muka(conn, tid, ids, sendiri=True)
    pnw_cache, hasil = {}, {}
    for did in ids:
        r, kel = rows.get(did), []
        s = so.get(r["so_id"]) if r else None
        if s:
            kel.append({"key": "rekap", "label": "Rekap", "docs": [DB.rekap(s["id"])]})
            if s["quote_id"]:
                if s["quote_id"] not in pnw_cache:
                    pnw_cache[s["quote_id"]] = await DB.penawaran(conn, tid, s["quote_id"])
                if pnw_cache[s["quote_id"]]:
                    kel.append({"key": "pnw", "label": "Penawaran", "docs": [dict(pnw_cache[s["quote_id"]])]})
            if r["proforma_id"]:
                pro = await DB.proforma_so(conn, tid, s["id"], s["status"] == "cancelled", {str(r["proforma_id"])})
                if pro:
                    kel.append({"key": "pro", "label": "Tagihan", "docs": pro})
        if did in kw:
            kel.append({"key": "kw", "label": "Pembayaran", "planned": None, "docs": [kw[did]]})
        invs = apl.get(did, [])
        ki = _urut([sj[str(x)] for i in invs for x in sj_per_inv.get(i, []) if str(x) in sj])
        ki += _urut([fak[str(i)] for i in invs if str(i) in fak])
        if ki:
            kel.append({"key": "ki", "label": "Kirim & Faktur", "docs": ki})
        if did in nk_asal and str(nk_asal[did]) in nk:
            kel.append({"key": "nk", "label": "Nota Kredit", "docs": [nk[str(nk_asal[did])]]})
        hasil[did] = kel
    return hasil


def documents_baris(kelompok: list) -> list:
    """documents[{type}] baris daftar = dokumen bundel TANPA rekap -- SATU definisi (penerimaan_daftar.documents_baris)."""
    from .penerimaan_daftar import documents_baris as _db
    return _db(kelompok)


def sql_rincian_um() -> str:
    """Rincian jurnal per uang muka (21 Okt 2026, MASTER): pasangan jurnal & keefektifan = TEKS SQL_SISA_UANG_MUKA (bagian sebelum
    `baris AS (`), lalu baris akun CUSTOMER_DEPOSIT_LIABILITY dipecah per source_type. $1 tenant, $2 uuid[], $3 akun.
    -> id, remaining (== compute_deposit_remaining_many), diterapkan (D-K DEPOSIT_APPLICATION), dikembalikan (D-K DEPOSIT_REFUND),
    masuk (K-D jurnal lain: CUSTOMER_DEPOSIT, lepas bayar, kelebihan penerimaan/NK). Identitas: masuk - diterapkan - dikembalikan = remaining."""
    from ..routers.customer_deposits import SQL_SISA_UANG_MUKA as S
    assert S.count("baris AS (") == 1
    return S[:S.index("baris AS (")] + """baris AS (
    SELECT jl.journal_id, je.source_type, SUM(jl.credit) AS kr, SUM(jl.debit) AS db
    FROM journal_lines jl JOIN efektif e ON e.journal_id = jl.journal_id JOIN journal_entries je ON je.id = jl.journal_id
    WHERE jl.account_id = $3
    GROUP BY jl.journal_id, je.source_type
)
SELECT d.id, COALESCE(SUM(b.kr) - SUM(b.db), 0) AS remaining,
       COALESCE(SUM(b.db - b.kr) FILTER (WHERE b.source_type = 'DEPOSIT_APPLICATION'), 0) AS diterapkan,
       COALESCE(SUM(b.db - b.kr) FILTER (WHERE b.source_type = 'DEPOSIT_REFUND'), 0) AS dikembalikan,
       COALESCE(SUM(b.kr - b.db) FILTER (WHERE b.source_type NOT IN ('DEPOSIT_APPLICATION', 'DEPOSIT_REFUND')), 0) AS masuk
FROM d
LEFT JOIN pasangan p ON p.deposit_id = d.id
LEFT JOIN baris b ON b.journal_id = p.journal_id
GROUP BY d.id"""


async def rincian_um_many(conn, tenant_id: str, ids) -> dict:
    """{str(id): {remaining, diterapkan, dikembalikan, masuk}} (Decimal) -- turunan JURNAL, bukan cache amount_applied/refunded."""
    from .role_resolver import AccountRole, resolve_account_id_by_role
    ids = [str(i) for i in ids]
    if not ids:
        return {}
    akun = await resolve_account_id_by_role(conn, tenant_id, AccountRole.CUSTOMER_DEPOSIT_LIABILITY)
    return {str(r["id"]): {k: Decimal(str(r[k] or 0)) for k in ("remaining", "diterapkan", "dikembalikan", "masuk")}
            for r in await conn.fetch(sql_rincian_um(), tenant_id, ids, akun)}


def medan_rincian(r) -> dict:
    """Medan daftar/detail UM (float JSON): diterapkan, dikembalikan. Tanpa jurnal (draf) = 0."""
    r = r or {}
    return {"diterapkan": float(r.get("diterapkan", 0)), "dikembalikan": float(r.get("dikembalikan", 0))}
