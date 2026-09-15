"""FIX_R9_REFUND: cermin bank refund diturunkan dari CoA KREDIT jurnal (body.account_id) via
reverse-lookup bank_accounts, BUKAN body.bank_account_id. Refund yang mengkredit CoA bank-terhubung
SELALU bercermin (deposit bank_account_id NULL: overpayment / lama). CoA non-bank -> tak bercermin.
account_id = sumber kebenaran (body.bank_account_id diabaikan) -> tepat 1 cermin di akun yang dikredit."""
import io, sys
P = "/root/mh-law2/backend/api_gateway/app/routers/customer_deposits.py"
t = io.open(P, encoding="utf-8").read()
OLD = '''                if body.bank_account_id:
                    await create_bank_transaction_for_journal(
                        conn,
                        tenant_id=ctx["tenant_id"],
                        bank_account_id=UUID(body.bank_account_id),
                        journal_id=journal_id,'''
NEW = '''                # FIX_R9_REFUND (2026-09-14): cermin diturunkan dari CoA KREDIT jurnal
                # (body.account_id) via reverse-lookup, BUKAN body.bank_account_id. Sebelumnya
                # refund deposit dg bank_account_id NULL (overpayment / deposit lama) mengkredit
                # CoA bank TANPA cermin -> celah R9. account_id = sumber kebenaran (body.bank_account_id
                # diabaikan: bila menunjuk bank lain, yang benar tetap akun yang DIKREDIT jurnal).
                _refund_ba_id = await conn.fetchval(
                    "SELECT id FROM bank_accounts WHERE tenant_id = $1 AND coa_id = $2 AND is_active = true",
                    ctx["tenant_id"],
                    UUID(body.account_id),
                )
                if _refund_ba_id:
                    await create_bank_transaction_for_journal(
                        conn,
                        tenant_id=ctx["tenant_id"],
                        bank_account_id=_refund_ba_id,
                        journal_id=journal_id,'''
if t.count(OLD) != 1:
    print("GAGAL anchor", t.count(OLD)); sys.exit(1)
io.open(P, "w", encoding="utf-8").write(t.replace(OLD, NEW))
print("OK FIX_R9_REFUND: cermin via reverse-lookup CoA kredit")
