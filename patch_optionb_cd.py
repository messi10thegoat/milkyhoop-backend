"""Option B — customer_deposits.py: C2 saldo hitung baris deposit di jurnal RP via created_deposit_id (x3),
C3 refund journal-derived, C4 larang void deposit-overpay."""
import io, sys
P = "/root/mh-law2/backend/api_gateway/app/routers/customer_deposits.py"
t = io.open(P, encoding="utf-8").read()


def rep(old, new, tag):
    global t
    if t.count(old) != 1:
        print(f"GAGAL {tag}: cacah {t.count(old)}"); sys.exit(1)
    t = t.replace(old, new)


# C2a compute_deposit_remaining (per-deposit; $2=deposit_id)
rep(
'''        WHERE je.tenant_id = $1
          AND je.source_id = $2
          AND jl.account_id = $3
          AND is_effective_journal(je.id)''',
'''        WHERE je.tenant_id = $1
          AND jl.account_id = $3
          AND is_effective_journal(je.id)
          AND (
              je.source_id = $2
              OR je.id IN (
                  SELECT journal_id FROM receive_payments
                  WHERE tenant_id = $1 AND created_deposit_id = $2 AND journal_id IS NOT NULL
              )
          )''', "C2a remaining")

# C2b compute_deposit_balance (customer-level; $2=customer_id)
rep(
'''        SELECT COALESCE(SUM(jl.credit) - SUM(jl.debit), 0)
        FROM journal_lines jl
        JOIN journal_entries je ON je.id = jl.journal_id
        JOIN customer_deposits cd ON cd.id = je.source_id
        WHERE je.tenant_id = $1
          AND cd.tenant_id = $1
          AND cd.customer_id = $2
          AND jl.account_id = $3
          AND is_effective_journal(je.id)''',
'''        SELECT COALESCE(SUM(jl.credit) - SUM(jl.debit), 0)
        FROM journal_lines jl
        JOIN journal_entries je ON je.id = jl.journal_id
        WHERE je.tenant_id = $1
          AND jl.account_id = $3
          AND is_effective_journal(je.id)
          AND (
              je.source_id IN (
                  SELECT id FROM customer_deposits WHERE tenant_id = $1 AND customer_id = $2
              )
              OR je.id IN (
                  SELECT journal_id FROM receive_payments
                  WHERE tenant_id = $1 AND journal_id IS NOT NULL
                    AND created_deposit_id IN (
                        SELECT id FROM customer_deposits WHERE tenant_id = $1 AND customer_id = $2
                    )
              )
          )''', "C2b balance")

# C2c available_balance (tenant-level; $2=deposit_coa)
rep(
'''                SELECT COALESCE(SUM(jl.credit) - SUM(jl.debit), 0)
                FROM journal_lines jl
                JOIN journal_entries je ON je.id = jl.journal_id
                JOIN customer_deposits cd ON cd.id = je.source_id
                WHERE je.tenant_id = $1
                  AND cd.tenant_id = $1
                  AND jl.account_id = $2
                  AND is_effective_journal(je.id)''',
'''                SELECT COALESCE(SUM(jl.credit) - SUM(jl.debit), 0)
                FROM journal_lines jl
                JOIN journal_entries je ON je.id = jl.journal_id
                WHERE je.tenant_id = $1
                  AND jl.account_id = $2
                  AND is_effective_journal(je.id)
                  AND (
                      je.source_id IN (SELECT id FROM customer_deposits WHERE tenant_id = $1)
                      OR je.id IN (
                          SELECT journal_id FROM receive_payments
                          WHERE tenant_id = $1 AND journal_id IS NOT NULL
                            AND created_deposit_id IS NOT NULL
                      )
                  )''', "C2c available_balance")

# C3 refund remaining -> journal-derived
rep(
'''                # Check remaining
                remaining = (
                    dep["amount"]
                    - (dep["amount_applied"] or 0)
                    - (dep["amount_refunded"] or 0)
                )

                if body.amount > remaining:''',
'''                # Check remaining (Option B: journal-derived, SATU SUMBER dgn apply; bukan cache)
                remaining = await compute_deposit_remaining(
                    conn, ctx["tenant_id"], deposit_id
                )

                if body.amount > remaining:''', "C3 refund journal-derived")

# C4 larang void deposit-overpay
rep(
'''                if (dep["amount_refunded"] or 0) > 0:
                    raise HTTPException(
                        status_code=400,
                        detail="Cannot void deposit with refunds. Reverse refunds first.",
                    )

                    # Law 5: Period lock check''',
'''                if (dep["amount_refunded"] or 0) > 0:
                    raise HTTPException(
                        status_code=400,
                        detail="Cannot void deposit with refunds. Reverse refunds first.",
                    )

                # Option B: uang muka dari kelebihan bayar (created via RP) -> liabilitasnya di jurnal
                # RP; void deposit tak boleh membalik jurnal RP. Larang; arahkan ke refund / batalkan RP.
                _rp_ovp = await conn.fetchrow(
                    "SELECT payment_number FROM receive_payments "
                    "WHERE tenant_id = $1 AND created_deposit_id = $2",
                    ctx["tenant_id"],
                    deposit_id,
                )
                if _rp_ovp:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"Uang muka ini berasal dari kelebihan bayar pembayaran "
                            f"{_rp_ovp['payment_number']}. Gunakan pengembalian dana, atau batalkan "
                            "pembayarannya."
                        ),
                    )

                    # Law 5: Period lock check''', "C4 larang void overpay")

io.open(P, "w", encoding="utf-8").write(t)
print("OK customer_deposits: C2 saldo x3 + C3 refund journal-derived + C4 larang void overpay")
