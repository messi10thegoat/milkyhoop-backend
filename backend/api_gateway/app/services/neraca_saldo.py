"""Neraca Saldo (trial balance) dari JURNAL POSTED -- gelombang 4 L3 (10 Nov 2026; kontrak KONTRAK-laporan-teks-server-rupiah-20261110.md §8).

Dulu GET /api/reports/trial-balance memanggil fungsi DB get_trial_balance(): saringan journal_entries (POSTED, tanggal <= as_of) ada di
ON LEFT JOIN sementara journal_lines di-join tanpa syarat -> baris jurnal VOID/DRAFT/masa depan ikut dijumlah (kaos: +Rp 65.218 dari
32 baris VOID; debit & kredit sama-sama lebih -> tetap "seimbang"), as_of tak berefek, akun nonaktif bersaldo dibuang (BL-07).
Sekarang SATU kueri: INNER JOIN jurnal POSTED <= as_of, tenant; akun aktif ATAU nonaktif bersaldo; sama dengan /trial-balance/full dan
/api/ledger (POSTED saja; pasangan pembalik bersih nol per akun). Decimal penuh (Law 9/25); medan lama float tetap; teks tampil server.
"""
from decimal import Decimal

from . import teks_galat as tg
from .report_engine import baris as B

NOL = Decimal("0")

KUERI = """
    SELECT coa.id AS account_id, coa.account_code, coa.name AS account_name, coa.account_type, coa.normal_balance,
           SUM(x.debit) AS total_debit, SUM(x.credit) AS total_credit
    FROM chart_of_accounts coa
    JOIN (SELECT jl.account_id, jl.debit, jl.credit
          FROM journal_lines jl JOIN journal_entries je ON je.id = jl.journal_id
          WHERE je.tenant_id = $1 AND je.status = 'POSTED' AND je.journal_date <= $2) x ON x.account_id = coa.id
    WHERE coa.tenant_id = $1
    GROUP BY coa.id, coa.account_code, coa.name, coa.account_type, coa.normal_balance
    HAVING SUM(x.debit) <> 0 OR SUM(x.credit) <> 0
    ORDER BY coa.account_code
"""


def kolom_saldo(debit, kredit) -> tuple:
    """Kolom sajian neraca saldo: saldo bersih di kolom debit bila debit > kredit, selain itu di kolom kredit (nol -> keduanya nol)."""
    neto = Decimal(debit) - Decimal(kredit)
    return (neto, NOL) if neto > 0 else (NOL, -neto)


def tampil(x, presisi: int) -> str:
    return "" if Decimal(x) == 0 else tg.angka_laporan(x, presisi)


async def neraca_saldo(conn, tenant_id: str, per, presisi: int) -> dict:
    rows = await conn.fetch(KUERI, tenant_id, per)
    akun, td, tk, tds, tks = [], NOL, NOL, NOL, NOL
    for r in rows:
        d, k = Decimal(r["total_debit"] or 0), Decimal(r["total_credit"] or 0)
        ds, ks = kolom_saldo(d, k)
        akun.append({
            "account_id": str(r["account_id"]), "account_code": r["account_code"], "account_name": r["account_name"],
            "account_type": r["account_type"], "normal_balance": r["normal_balance"],
            "total_debit": float(d), "total_credit": float(k), "balance": float(d - k if r["normal_balance"] == "DEBIT" else k - d),
            "debit_saldo": B.nilai(ds), "kredit_saldo": B.nilai(ks), "debit_tampil": tampil(ds, presisi), "kredit_tampil": tampil(ks, presisi),
        })
        td, tk, tds, tks = td + d, tk + k, tds + ds, tks + ks
    selisih = tds - tks
    return {
        "tenant_id": tenant_id, "as_of_date": per.isoformat(), "period_id": None,
        "total_debit": float(td), "total_credit": float(tk), "is_balanced": abs(td - tk) < Decimal("0.01"),
        "account_count": len(akun), "accounts": akun,
        "judul": "NERACA SALDO", "per_tanggal": per.isoformat(), "per_text": B.per_tanggal(per),
        "total_debit_saldo": B.nilai(tds), "total_kredit_saldo": B.nilai(tks),
        "total_debit_tampil": tg.angka_laporan(tds, presisi), "total_kredit_tampil": tg.angka_laporan(tks, presisi),
        "selisih": B.nilai(selisih),
        "status_text": "Seimbang" if selisih.quantize(B.SEN) == 0 else f"Tidak seimbang — selisih {tg.angka_laporan(abs(selisih), presisi)}",
    }
