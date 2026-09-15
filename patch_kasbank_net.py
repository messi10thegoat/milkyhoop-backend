"""#3 refinement: exclude internal transfer via ATURAN UMUM (neto bank per jurnal), bukan source_type.
Jurnal yang Σ(debit bank)==Σ(kredit bank) -> neto 0 -> internal -> dikecualikan (mencakup BANK_TRANSFER
DAN jurnal manual dua-kaki). Jurnal campuran -> hanya selisih neto bank yang dihitung."""
import io, sys
P = "/root/mh-law2/backend/api_gateway/app/routers/kasbank.py"
t = io.open(P, encoding="utf-8").read()


def old_q(agg, sign):  # versi source_type (deploy 495d7589)
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


def new_q(direction):  # 'in' -> net>0 ; 'out' -> net<0 (dinegasikan). Neto bank PER JURNAL.
    picker = "j.net > 0 THEN j.net" if direction == "in" else "j.net < 0 THEN -j.net"
    return (
        f'                SELECT COALESCE(SUM(CASE WHEN {picker} ELSE 0 END), 0)\n'
        f'                FROM (\n'
        f'                    SELECT SUM(jl.debit) - SUM(jl.credit) AS net\n'
        f'                    FROM journal_lines jl\n'
        f'                    JOIN journal_entries je ON je.id = jl.journal_id\n'
        f'                    WHERE je.tenant_id = $1 AND je.status = \'POSTED\'\n'
        f'                        AND je.reversed_by_id IS NULL\n'
        f'                        AND je.reversal_of_id IS NULL\n'
        f'                        AND je.journal_date = (now() AT TIME ZONE COALESCE(\n'
        f'                            (SELECT timezone FROM "Tenant" WHERE id = $1), \'Asia/Jakarta\'))::date\n'
        f'                        AND jl.account_id IN (\n'
        f'                            SELECT coa_id FROM bank_accounts\n'
        f'                            WHERE tenant_id = $1 AND is_active = true AND coa_id IS NOT NULL\n'
        f'                        )\n'
        f'                    GROUP BY je.id\n'
        f'                ) j\n'
    )


for old, new, tag in [(old_q("debit", "debit"), new_q("in"), "today_in"),
                      (old_q("credit", "credit"), new_q("out"), "today_out")]:
    if t.count(old) != 1:
        print(f"GAGAL {tag}: cacah {t.count(old)}"); sys.exit(1)
    t = t.replace(old, new)
io.open(P, "w", encoding="utf-8").write(t)
print("OK today_in/out -> aturan neto-bank per jurnal (internal transfer umum dikecualikan)")
