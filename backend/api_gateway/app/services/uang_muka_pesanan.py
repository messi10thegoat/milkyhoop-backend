"""Uang muka per pesanan -- SATU sumber, turunan JURNAL (KONTRAK-dp-neto-20261016; Law 1/16/29, pola NetSuite
Customer Deposit: tersedia = diterima − refund − diterapkan).

Per uang muka (bukan void; atribusi SO langsung ATAU lewat proformanya), dari baris jurnal efektif pada akun
CUSTOMER_DEPOSIT_LIABILITY yang source_id = uang muka itu:
  terima = kredit−debit jurnal CUSTOMER_DEPOSIT
  terap  = debit−kredit jurnal DEPOSIT_APPLICATION
  lain   = debit−kredit jurnal lain (terukur 16 Okt: hanya DEPOSIT_REFUND) = DIKEMBALIKAN
  neto   = terima − lain   (uang yang MENGIKAT pesanan; yang diterapkan tetap milik pesanan lewat faktur)
Draf = belum berjurnal (terima 0); nominalnya dilaporkan terpisah sebagai `draf` (janji, bukan uang).

Pemakai: ringkasan_pesanan (Sisa SO), received_total_for_order (plafon terima), deposit_totals_for_order (panel
proforma), proforma_atribusi.muat_atribusi (tagih/terbayar proforma), so_ubah_terkonfirmasi.batas_bawah_total, PDF
kwitansi. Filter tenant eksplisit (BYPASSRLS).
"""
from decimal import Decimal

NOL = Decimal("0")

SQL_PER_UANG_MUKA = """
    SELECT cd.id, cd.status, cd.amount, cd.proforma_id, cd.created_at,
           COALESCE(cd.sales_order_id, p.sales_order_id) AS so_id,
           COALESCE(SUM(jl.credit - jl.debit) FILTER (WHERE je.source_type = 'CUSTOMER_DEPOSIT'), 0) AS terima,
           COALESCE(SUM(jl.debit - jl.credit) FILTER (WHERE je.source_type = 'DEPOSIT_APPLICATION'), 0) AS terap,
           COALESCE(SUM(jl.debit - jl.credit) FILTER (
               WHERE je.source_type NOT IN ('CUSTOMER_DEPOSIT', 'DEPOSIT_APPLICATION')), 0) AS lain
    FROM customer_deposits cd
    LEFT JOIN proformas p ON p.id = cd.proforma_id AND p.tenant_id = cd.tenant_id
    LEFT JOIN journal_entries je ON je.tenant_id = cd.tenant_id AND je.source_id::uuid = cd.id
                                 AND is_effective_journal(je.id)
    LEFT JOIN journal_lines jl ON jl.journal_id = je.id AND jl.account_id = $3
    WHERE cd.tenant_id = $1 AND cd.status <> 'void'
      AND (cd.sales_order_id = ANY($2::uuid[]) OR p.sales_order_id = ANY($2::uuid[]))
    GROUP BY cd.id, cd.status, cd.amount, cd.proforma_id, cd.created_at, COALESCE(cd.sales_order_id, p.sales_order_id)
"""


def _d(v) -> Decimal:
    return Decimal(str(v)) if v is not None else NOL


async def per_uang_muka(conn, tenant_id: str, so_ids: list) -> list:
    """[{id, status, amount, proforma_id, created_at, so_id, terima, terap, lain, neto}] (Decimal)."""
    from .role_resolver import AccountRole, resolve_account_id_by_role
    so_ids = list({s for s in so_ids if s is not None})
    if not so_ids:
        return []
    akun = await resolve_account_id_by_role(conn, tenant_id, AccountRole.CUSTOMER_DEPOSIT_LIABILITY)
    out = []
    for r in await conn.fetch(SQL_PER_UANG_MUKA, tenant_id, so_ids, akun):
        d = dict(r)
        for k in ("terima", "terap", "lain"):
            d[k] = _d(d[k])
        d["neto"] = d["terima"] - d["lain"]
        out.append(d)
    return out


def jumlahkan(baris: list, so_ids: list, exclude_id=None) -> dict:
    """Murni. -> {so_id: {diterima, dikembalikan, neto, diterapkan, draf, neto_tanpa_proforma}}."""
    out = {s: {k: NOL for k in ("diterima", "dikembalikan", "neto", "diterapkan", "draf", "neto_tanpa_proforma")}
           for s in so_ids if s is not None}
    for d in baris:
        if d["so_id"] not in out or (exclude_id is not None and str(d["id"]) == str(exclude_id)):
            continue
        o = out[d["so_id"]]
        if d["status"] == "draft":
            o["draf"] += _d(d["amount"])
            continue
        o["diterima"] += d["terima"]
        o["dikembalikan"] += d["lain"]
        o["neto"] += d["neto"]
        o["diterapkan"] += d["terap"]
        if d["proforma_id"] is None:
            o["neto_tanpa_proforma"] += d["neto"]
    return out


async def uang_muka_pesanan(conn, tenant_id: str, so_ids: list, exclude_id=None) -> dict:
    return jumlahkan(await per_uang_muka(conn, tenant_id, so_ids), list(so_ids), exclude_id)
