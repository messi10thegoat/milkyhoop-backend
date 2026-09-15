"""L1 receive_payments router: hapus pembulatan int() pada nominal (display/response) -> float.
Nilai tersimpan/hitung sudah Decimal (body Decimal + kolom numeric)."""
import io, sys
P = "/root/mh-law2/backend/api_gateway/app/routers/receive_payments.py"
t = io.open(P, encoding="utf-8").read()
def rep(o, n, tag):
    global t
    if t.count(o) != 1:
        print(f"GAGAL {tag}: {t.count(o)}"); sys.exit(1)
    t = t.replace(o, n)

rep('        invoice_id,\n    )\n    return int(result or 0)',
    '        invoice_id,\n    )\n    return float(result or 0)', "176 helper")
rep('                    "total_settled_noncash": int(noncash or 0),',
    '                    "total_settled_noncash": float(noncash or 0),', "noncash")
rep('''                    "total_received": int(row["total_received"] or 0),
                    "total_allocated": int(row["total_allocated"] or 0),
                    "total_unapplied": int(row["total_unapplied"] or 0),''',
    '''                    "total_received": float(row["total_received"] or 0),
                    "total_allocated": float(row["total_allocated"] or 0),
                    "total_unapplied": float(row["total_unapplied"] or 0),''', "summary totals")
rep('''                        "debit": int(line["debit"] or 0),
                        "credit": int(line["credit"] or 0),''',
    '''                        "debit": float(line["debit"] or 0),
                        "credit": float(line["credit"] or 0),''', "journal line disp")
rep('''                journal_debit = int(journal["total_debit"] or 0)
                journal_credit = int(journal["total_credit"] or 0)''',
    '''                journal_debit = float(journal["total_debit"] or 0)
                journal_credit = float(journal["total_credit"] or 0)''', "journal totals disp")
rep('                    remaining = int(allocs[0]["remaining_after"])',
    '                    remaining = float(allocs[0]["remaining_after"])', "remaining disp")
rep('            _amt = int(pay["total_amount"] or 0)',
    '            _amt = float(pay["total_amount"] or 0)', "amt disp")

io.open(P, "w", encoding="utf-8").write(t)
print("OK receive_payments router: int()->float pada nominal")
