"""DRAF peta tahap 2 (baca-saja): untuk tiap tulis tak terpetakan di modul pemindah uang, usul (pola, method, modul
middleware, aksi) dengan ATURAN tertulis — bukan tebakan per baris. Juga: modul middleware vs baris role_permissions."""
import asyncio
import os
import re
import sys

sys.path.insert(0, "/app/backend/api_gateway")
sys.path.insert(0, "/app")
import asyncpg  # noqa: E402
from fastapi.routing import APIRoute  # noqa: E402

from app.main import app  # noqa: E402
from app.middleware import permission_middleware as pm  # noqa: E402
import backend.api_gateway.app.services.policy_engine_client as pec  # noqa: E402

# router -> modul middleware (dinormalisasi ke salah satu 15 modul BERBARIS role_permissions bila semantik cocok)
ROUTER_MODUL = {
    "bank_reconciliation": "kas_bank", "cheques": "kas_bank", "bank_transfers": "kas_bank",
    "fixed_assets": "journal", "opening_balance": "journal", "consolidation": "journal", "intercompany": "journal",
    "budgets": "reports", "periods": "journal", "fiscal_years": "journal",
    "expense_extended": "expense", "expenses": "expense",
    "bills": "purchase_invoice", "recurring_bills": "purchase_invoice", "purchase_orders": "purchase_order",
    "production": "item", "production_costing": "journal", "stock_transfers": "item", "items": "item", "stock_adjustments": "item",
    "customers": "customer", "vendors": "supplier", "recurring_invoices": "sales_invoice", "sales_invoices": "sales_invoice",
    "customer_deposits": "customer", "sales_receipts": "sales_invoice", "tax_invoices": "tax", "nsfp": "tax", "efaktur": "tax",
    "payroll_runs": "payroll",
}
SENGAJA_KECUALI = {"unified_chat": "chat: aksi keuangan dieksekusi lewat jalur aksi internal — otorisasi di jalur itu (diukur terpisah)",
                   "document_intake": "unggah/eksekusi dokumen: sama, jalur eksekusi internal (diukur terpisah)"}
VERBA = [(r"/(void|cancel|reject|bounce)$", "V"), (r"/(approve|confirm)$", "A"),
         (r"/(post|complete|process|process-due|activate|release|start|generate|execute|reimburse|submit|send|ship|receive|clear|deposit|"
          r"issue-materials|report-output|labor|dispose|sell|to-bill|record-actual|allocate-overhead|auto-map|auto-match|match|import|"
          r"categorize|pause|resume|toggle|mark-paid|reactivate|duplicate|stock-adjustment|stock-transfer|maintenance|intercompany|"
          r"calculate-from-bom/\{[^}]+\})$", "P"),
         (r"/(export)$", "E"), (r"/(calculate|validate|preview)$", "R")]


def aksi(method, path):
    for pola_v, a in VERBA:
        if re.search(pola_v, path):
            return a
    return {"POST": "C", "PUT": "U", "PATCH": "U", "DELETE": "D"}[method]


def regex_dari(path):
    return "^" + re.sub(r"\{[^}]+\}", "[^/]+", path) + "$"


async def main():
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=1)
    db_mod = {r["module"] for r in await pool.fetch("SELECT DISTINCT module FROM role_permissions")}
    await pool.close()
    pola = [(re.compile(p), m) for p, m, *_ in pm.ROUTE_PERMISSIONS]
    skip = [re.compile(p) for p in pm.SKIP_PATTERNS]
    contoh = lambda p: re.sub(r"\{[^}]+\}", "00000000-0000-4000-8000-000000000001", p)  # noqa: E731
    baris, kecuali = [], []
    for r in app.routes:
        if not isinstance(r, APIRoute):
            continue
        rt = r.endpoint.__module__.split(".")[-1]
        for m in sorted(r.methods & {"POST", "PUT", "PATCH", "DELETE"}):
            p = contoh(r.path)
            if any(s.match(p) for s in skip) or any(m in ms and c.match(p) for c, ms in pola):
                continue
            if rt in SENGAJA_KECUALI:
                kecuali.append((m, r.path, SENGAJA_KECUALI[rt]))
            elif rt in ROUTER_MODUL:
                mod = ROUTER_MODUL[rt]
                baris.append((regex_dari(r.path), m, mod, aksi(m, r.path), pec.MODULE_NAME_MAPPING.get(mod, mod.upper())))
    print("# DRAF peta tahap 2 — tulis modul pemindah uang (dibangkitkan aturan; BELUM dipasang)\n")
    print(f"baris usulan: {len(baris)} · pengecualian dideklarasi: {len(kecuali)}\n")
    print("| pola | method | modul middleware | aksi | modul DB (role_permissions) |\n|---|---|---|---|---|")
    for b in baris:
        print(f"| `{b[0]}` | {b[1]} | {b[2]} | {b[3]} | {b[4]}{'' if b[4] in db_mod else ' ⚠️ TANPA BARIS PERAN'} |")
    print("\n## Pengecualian dideklarasi (bukan lolos diam)\n")
    for k in kecuali:
        print(f"- {k[0]} `{k[1]}` — {k[2]}")
    mw = sorted({x[2] for x in pm.ROUTE_PERMISSIONS})
    tanpa = [(m, pec.MODULE_NAME_MAPPING.get(m, m.upper())) for m in mw if pec.MODULE_NAME_MAPPING.get(m, m.upper()) not in db_mod]
    print(f"\n## Modul middleware HIDUP yang TAK punya baris role_permissions: {len(tanpa)} dari {len(mw)}\n")
    print("Akibat hari ini: semua peran NON-owner DITOLAK di rute terpetakan modul ini (owner lolos via bypass).\n")
    for m, d in tanpa:
        print(f"- {m} -> {d}")


asyncio.run(main())
