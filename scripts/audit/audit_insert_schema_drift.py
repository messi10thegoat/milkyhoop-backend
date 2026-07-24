import re, os, subprocess, json

ROOT = "/root/milkyhoop-dev/backend/api_gateway/app"
# 1) kumpulkan kolom aktual dari DB
q = ("SELECT table_name||'|'||string_agg(column_name, ',' ORDER BY column_name) "
     "FROM information_schema.columns WHERE table_schema='public' GROUP BY table_name;")
out = subprocess.run(["docker","exec","milkyhoop-dev-postgres-1","psql","-U","postgres",
                      "-d","milkydb","-tAc",q], capture_output=True, text=True).stdout
db = {}
for line in out.strip().split("\n"):
    if "|" in line:
        t, cols = line.split("|", 1)
        db[t.strip()] = set(c.strip() for c in cols.split(","))

# 2) ekstrak INSERT INTO <tabel> ( ... ) VALUES dari kode
# ketat: daftar kolom TIDAK BOLEH memuat kurung/titik-koma, dan harus langsung
# diikuti VALUES atau SELECT -> mematikan artefak INSERT..SELECT lintas-statement
pat = re.compile(r'INSERT\s+INTO\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(([^();]*)\)\s*(?:VALUES|SELECT)\b', re.I | re.S)
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
                findings.setdefault("__TABEL_HILANG__", set()).add(tbl)
                continue
            raw = m.group(2)
            # buang komentar SQL dan whitespace
            raw = re.sub(r'--[^\n]*', '', raw)
            cols = [c.strip().strip('"') for c in raw.split(",")]
            cols = [c for c in cols if re.fullmatch(r'[a-zA-Z_][a-zA-Z0-9_]*', c or "")]
            if not cols:
                continue
            missing = [c for c in cols if c not in db[tbl]]
            if missing:
                line_no = src[:m.start()].count("\n") + 1
                key = tbl
                findings.setdefault(key, set()).update(
                    (c, f"{os.path.relpath(p, ROOT)}:{line_no}") for c in missing)

print(f"file .py dipindai: {scanned}   tabel di DB: {len(db)}")
print("=" * 68)
if not findings:
    print("NOL drift: semua kolom INSERT ada di skema.")
for tbl in sorted(findings):
    if tbl == "__TABEL_HILANG__":
        print(f"\n### TABEL TIDAK ADA DI DB: {sorted(findings[tbl])}")
        continue
    items = sorted(findings[tbl])
    cols = sorted(set(c for c, _ in items))
    print(f"\n### {tbl}  -> kolom hilang: {', '.join(cols)}")
    for c, loc in items:
        print(f"      {c:<24} {loc}")
