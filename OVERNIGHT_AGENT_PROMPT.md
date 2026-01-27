# OVERNIGHT AUTONOMOUS AGENT: Accounting Endpoint Audit & Fix

## Context
Kamu adalah backend developer untuk Milkyhoop accounting software.
Malam ini kamu bekerja TANPA supervisi sampai semua endpoint accounting PASS.
Jika butuh info, cari di web. Jika ragu soal accounting logic, refer ke QuickBooks/Xero pattern.

## Working Directory
```
/root/milkyhoop-dev/backend/api_gateway/app/
```

## TASK 1: Register Expenses Router (5 menit)

File: `/root/milkyhoop-dev/backend/api_gateway/app/main.py`

Tambahkan:
```python
from .routers import expenses

# Di bagian include_router, tambahkan:
app.include_router(expenses.router, prefix="/api/expenses", tags=["expenses"])
```

Commit: `git commit -m "feat: register expenses router"`

## TASK 2: Restart API Gateway

```bash
cd /root/milkyhoop-dev
docker-compose restart api-gateway
# atau
docker-compose up -d api-gateway
```

Wait 30 detik untuk service ready.

## TASK 3: Run Audit Script

```bash
cd /root/milkyhoop-dev
python3 scripts/overnight_audit.py
```

Review output. Catat endpoint yang FAIL.

## TASK 4: Fix Failing Endpoints

Untuk setiap endpoint yang FAIL:

1. Identify root cause (check logs: `docker-compose logs api-gateway --tail=100`)
2. Fix the issue in router/schema/service
3. Restart api-gateway
4. Re-test endpoint spesifik itu

### Common Issues & Fixes:

**Issue: 404 Not Found**
- Router belum di-register di main.py
- Endpoint path typo

**Issue: 500 Internal Server Error**
- Check logs untuk stack trace
- Biasanya: missing field, wrong type, DB query error

**Issue: 401 Unauthorized**
- Token expired atau invalid
- Refresh token dan retry

**Issue: 422 Validation Error**
- Request body tidak sesuai schema
- Check Pydantic schema

## TASK 5: Verify All Endpoints

Re-run audit sampai SEMUA endpoint PASS:

```bash
python3 scripts/overnight_audit.py
```

Target: 100% pass rate untuk semua endpoint berikut:

### Critical Endpoints Checklist

#### Jurnal Umum
- [ ] GET /api/journals
- [ ] GET /api/journals/{id}
- [ ] POST /api/journals
- [ ] POST /api/journals/{id}/post
- [ ] POST /api/journals/{id}/reverse

#### Buku Besar
- [ ] GET /api/ledger
- [ ] GET /api/ledger/summary
- [ ] GET /api/ledger/{account_id}

#### Tutup Buku
- [ ] GET /api/periods
- [ ] GET /api/periods/current
- [ ] POST /api/periods/{id}/close
- [ ] POST /api/periods/{id}/reopen
- [ ] GET /api/fiscal-years
- [ ] POST /api/fiscal-years

#### Laporan
- [ ] GET /api/reports/trial-balance
- [ ] GET /api/reports/laba-rugi/{periode}
- [ ] GET /api/reports/neraca/{periode}
- [ ] GET /api/reports/arus-kas/{periode}
- [ ] GET /api/reports/ar-aging
- [ ] GET /api/reports/ap-aging

#### Penerimaan Pembayaran
- [ ] GET /api/receive-payments
- [ ] GET /api/receive-payments/summary
- [ ] GET /api/receive-payments/{id}
- [ ] POST /api/receive-payments
- [ ] POST /api/receive-payments/{id}/post
- [ ] POST /api/receive-payments/{id}/void

#### Biaya & Pengeluaran
- [ ] GET /api/expenses
- [ ] GET /api/expenses/summary
- [ ] GET /api/expenses/{id}
- [ ] POST /api/expenses
- [ ] DELETE /api/expenses/{id}
- [ ] POST /api/expenses/{id}/void

#### Kas & Bank
- [ ] GET /api/bank-accounts
- [ ] GET /api/bank-reconciliation/sessions

#### Master Data
- [ ] GET /api/customers
- [ ] GET /api/vendors
- [ ] GET /api/items
- [ ] GET /api/accounts/tree

#### Invoices & Bills
- [ ] GET /api/sales-invoices
- [ ] GET /api/bills
- [ ] POST /api/bills/{id}/payments (Pay Bills)

## TASK 6: Generate Final Report

Setelah semua PASS, buat report:

```bash
cat > /root/milkyhoop-dev/docs/OVERNIGHT_AUDIT_REPORT.md << EOF
# Overnight Audit Report
Date: Tue Jan 27 21:29:39 WIB 2026

## Summary
- Total Endpoints Tested: XX
- Passed: XX
- Failed: XX
- Fixed: XX

## Endpoints Status

### Jurnal Umum ✅
- GET /api/journals - PASS
- ...

### (repeat for all modules)

## Issues Fixed
1. [Issue description] - [Fix applied]
2. ...

## Recommendations
1. ...

## Next Steps for Frontend
All endpoints ready. Frontend dapat langsung integrate dengan:
- Base URL: http://api.milkyhoop.com/api
- Auth: Bearer token

EOF
```

Commit report: `git add docs/ && git commit -m "docs: overnight audit report"`

## Rules
1. JANGAN tanya user - cari solusi sendiri
2. Jika stuck > 15 menit pada 1 issue, skip dan lanjut ke issue lain, catat untuk review
3. Commit setiap fix yang berhasil
4. Selalu restart api-gateway setelah edit code
5. Gunakan web search jika perlu referensi

## Success Criteria
- ✅ Expenses router registered
- ✅ All audit tests PASS (atau documented why not)
- ✅ Final report generated
- ✅ All commits pushed

START NOW. Work until complete or until 06:00 WIB.
