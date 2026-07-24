#!/usr/bin/env python3
"""
audit_update_schema_drift.py — pelengkap audit_insert_schema_drift.py.

Memindai `UPDATE <tabel> SET col = ...` di seluruh .py backend dan men-diff
kolom target terhadap information_schema. Ada karena drift V206
(receive_payments.bank_transaction_id) lolos audit INSERT-only.

Pakai: python3 scripts/audit/audit_update_schema_drift.py
TRIASE WAJIB — banyak temuan = kode mati (lihat README).
"""
import re, os, subprocess

ROOT = "/root/milkyhoop-dev/backend/api_gateway/app"
q = ("SELECT table_name||'|'||string_agg(column_name, ',' ORDER BY column_name) "
     "FROM information_schema.columns WHERE table_schema='public' GROUP BY table_name;")
out = subprocess.run(["docker","exec","milkyhoop-dev-postgres-1","psql","-U","postgres",
                      "-d","milkydb","-tAc",q], capture_output=True, text=True).stdout
db = {}
for line in out.strip().split("\n"):
    if "|" in line:
        t, cols = line.split("|", 1)
        db[t.strip()] = set(c.strip() for c in cols.split(","))

# UPDATE <tbl> SET <assignments up to WHERE/RETURNING/end-of-string-literal>
pat = re.compile(r'UPDATE\s+([a-zA-Z_][a-zA-Z0-9_]*)\s+SET\s+(.*?)(?:\bWHERE\b|\bRETURNING\b|"""|\'\'\')', re.I | re.S)
# target kolom = identifier di kiri '=' pada tiap assignment (pisah koma di top-level)
assign_col = re.compile(r'(?:^|,)\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*=', re.M)
findings = {}
scanned = 0
for dirpath, _, files in os.walk(ROOT):
    for fn in files:
        if not fn.endswith(".py") or ".bak" in fn:
            continue
        p = os.path.join(dirpath, fn)
        try: src = open(p, encoding="utf-8", errors="ignore").read()
        except Exception: continue
        scanned += 1
        for m in pat.finditer(src):
            tbl = m.group(1)
            if tbl not in db:
                continue  # tabel absen dilaporkan oleh audit INSERT
            body = re.sub(r'--[^\n]*', '', m.group(2))
            for cm in assign_col.finditer(body):
                col = cm.group(1)
                # buang alias tabel yg keliru tertangkap (mis. je.status -> status ok,
                # tapi 'x.y' tidak akan cocok karena assign_col butuh awal/koma)
                if col not in db[tbl]:
                    line_no = src[:m.start()].count("\n") + 1
                    findings.setdefault(tbl, set()).add((col, f"{os.path.relpath(p, ROOT)}:{line_no}"))

print(f"file .py dipindai: {scanned}   tabel di DB: {len(db)}")
print("=" * 60)
if not findings:
    print("NOL UPDATE-drift.")
for tbl in sorted(findings):
    cols = sorted(set(c for c,_ in findings[tbl]))
    print(f"\n### {tbl} -> {', '.join(cols)}")
    for c, loc in sorted(findings[tbl]):
        print(f"      {c:<22} {loc}")
