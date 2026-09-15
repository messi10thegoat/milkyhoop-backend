"""#3 today_in/out — perbaiki himpunan:
- akun: tautan bank_accounts aktif (bukan coa.account_type IN ('BANK','CASH') yg TAK PERNAH cocok).
- EFEKTIF: reversed_by_id IS NULL AND reversal_of_id IS NULL (void hari ini tak menambah in+out).
- tanggal: hari ini di ZONA WAKTU tenant (Tenant.timezone), bukan CURRENT_DATE server.
- kecualikan BANK_TRANSFER (pindah antar-rekening sendiri = internal, bukan arus).
"""
import io, sys
P = "/root/mh-law2/backend/api_gateway/app/routers/kasbank.py"
t = io.open(P, encoding="utf-8").read()


def q(agg, sign):  # agg: 'debit'/'credit'; sign: 'debit'/'credit' for >0 filter
    return (
        f'                SELECT COALESCE(SUM(jl.{agg}), 0)\n'
        f'                FROM journal_lines jl\n'
        f'                JOIN journal_entries je ON je.id = jl.journal_id\n'
        f'                WHERE je.tenant_id = $1 AND je.status = \'POSTED\'\n'
        f'                    AND je.reversed_by_id IS NULL\n'
        f'                    AND je.reversal_of_id IS NULL\n'
        f'                    AND je.source_type <> \'BANK_TRANSFER\'\n'
        f'                    AND je.journal_date = (now() AT TIME ZONE COALESCE(\n'
        f'                        (SELECT timezone FROM "Tenant" WHERE id = $1), \'Asia/Jakarta\'))::date\n'
        f'                    AND jl.account_id IN (\n'
        f'                        SELECT coa_id FROM bank_accounts\n'
        f'                        WHERE tenant_id = $1 AND is_active = true AND coa_id IS NOT NULL\n'
        f'                    )\n'
        f'                    AND jl.{sign} > 0\n'
    )


OLD_IN = (
    '                SELECT COALESCE(SUM(jl.debit), 0)\n'
    '                FROM journal_lines jl\n'
    '                JOIN journal_entries je ON je.id = jl.journal_id\n'
    '                JOIN chart_of_accounts coa ON coa.id = jl.account_id\n'
    "                WHERE je.tenant_id = $1 AND je.status = 'POSTED'\n"
    '                    AND je.journal_date = CURRENT_DATE\n'
    '                    AND je.reversed_by_id IS NULL\n'
    "                    AND coa.account_type IN ('BANK', 'CASH')\n"
    '                    AND jl.debit > 0\n'
)
OLD_OUT = OLD_IN.replace("SUM(jl.debit)", "SUM(jl.credit)").replace("AND jl.debit > 0", "AND jl.credit > 0")

for old, new, tag in [(OLD_IN, q("debit", "debit"), "today_in"),
                      (OLD_OUT, q("credit", "credit"), "today_out")]:
    if t.count(old) != 1:
        print(f"GAGAL {tag}: cacah {t.count(old)}"); sys.exit(1)
    t = t.replace(old, new)
io.open(P, "w", encoding="utf-8").write(t)
print("OK today_in + today_out: bank-linkage + efektif + tz-tenant + exclude BANK_TRANSFER")
