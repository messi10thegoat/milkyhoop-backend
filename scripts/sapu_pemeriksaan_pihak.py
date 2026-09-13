"""S2 baca-saja: tiap penulis baris aplikasi/alokasi -> fungsi pembungkus + setiap baris yang
MEMBANDINGKAN atau MENYARING pihak (customer_id/vendor_id) antara def dan INSERT.

Kontrol positif: credit_notes.py:1547 HARUS menampilkan baris `invoice["customer_id"] != cn["customer_id"]`.
"""
import re

BASE = "/root/milkyhoop-dev/backend/api_gateway/app/"
SITUS = [
    "routers/customer_deposits.py:1630", "routers/receive_payments.py:1961",
    "routers/credit_notes.py:1547",
    "routers/vendor_deposits.py:702", "routers/vendor_credits.py:1370",
    "routers/receive_payments.py:1339", "routers/receive_payments.py:1500",
    "routers/sales_invoices.py:3325",
    "routers/bill_payments.py:1331", "services/bills_service.py:1692",
]
PIHAK = re.compile(r"(customer_id|vendor_id)")
BANDING = re.compile(r"(!=|==|<>|\bAND\b|\bWHERE\b|raise HTTPException|different|berbeda|belong)", re.I)

for s in SITUS:
    f, n = s.split(":")
    n = int(n)
    b = open(BASE + f, encoding="utf-8").read().split("\n")
    m = n - 1
    while m > 0 and not re.match(r"\s*(async\s+)?def\s", b[m]):
        m -= 1
    print(f"\n######## {s}   fungsi: {b[m].strip()[:90]}")
    for i in range(m, n):
        if PIHAK.search(b[i]) and BANDING.search(b[i]):
            print(f"  {i+1}: {b[i].strip()[:120]}")
    # konteks 2 baris sesudah tiap perbandingan != / ==
    for i in range(m, n):
        if PIHAK.search(b[i]) and re.search(r"(!=|==)", b[i]):
            for j in (i + 1, i + 2, i + 3):
                print(f"     ↳ {j+1}: {b[j].strip()[:120]}")
