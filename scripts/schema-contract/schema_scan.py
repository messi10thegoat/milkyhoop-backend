#!/usr/bin/env python3
"""One-off schema-contract scan: find ghost-column references in embedded SQL.
High-precision: only reports a column as ghost when its table is unambiguously
resolvable (explicit table for UPDATE/INSERT/RETURNING; FROM/JOIN <realtable> <alias>
for alias.col) AND the column is absent from information_schema. Under-reports on
schema-qualified names, dynamic f-string columns, and CTE/subquery aliases (skipped)
rather than emitting false positives. NO fixes — report only."""
import os, re, sys
from collections import defaultdict

COLS = defaultdict(set)
with open('/root/cols.txt') as fh:
    for ln in fh:
        ln = ln.rstrip('\n')
        if '|' not in ln:
            continue
        t, c = ln.split('|', 1)
        COLS[t].add(c)
REAL = set(COLS.keys())

DIRS = ['/root/milkyhoop-dev/backend/api_gateway/app/routers',
        '/root/milkyhoop-dev/backend/api_gateway/app/services']

SQLKW = re.compile(r'\b(SELECT|INSERT\s+INTO|UPDATE|DELETE\s+FROM|FROM|JOIN)\b', re.I)
TRIPLE = re.compile(r'(?s)("""|\'\'\')(.*?)\1')
ALIAS = re.compile(r'\b(?:FROM|JOIN)\s+("?)([a-zA-Z_]\w*)\1(?:\s+(?:AS\s+)?("?)([a-zA-Z_]\w*)\3)?', re.I)
CTE = re.compile(r'(?:\bWITH\b|,)\s*([a-zA-Z_]\w*)\s+AS\s*\(', re.I)
COLREF = re.compile(r'\b([a-zA-Z_]\w*)\.([a-zA-Z_]\w*)\b')
UPDATE = re.compile(r'\bUPDATE\s+("?)([a-zA-Z_]\w*)\1\s+SET\s+(.*?)(?:\bWHERE\b|\bRETURNING\b|\bFROM\b|$)', re.I | re.S)
INSERT = re.compile(r'\bINSERT\s+INTO\s+("?)([a-zA-Z_]\w*)\1\s*\(([^)]*)\)', re.I | re.S)

SQL_RESERVED_ALIAS = {'excluded', 'information_schema', 'pg_catalog', 'pg_temp'}
# words that appear as <x>.<y> in SQL but are not table.column (type casts, etc.)
SKIP_ALIAS = {'public'}

def line_of(src, idx):
    return src.count('\n', 0, idx) + 1

def scan_file(path):
    findings = []
    with open(path, encoding='utf-8', errors='replace') as fh:
        src = fh.read()
    for m in TRIPLE.finditer(src):
        body = m.group(2)
        if not SQLKW.search(body):
            continue
        base = m.start(2)
        # alias map (real tables only) + CTE/dynamic skip set
        amap = {}
        for a in ALIAS.finditer(body):
            tbl = a.group(2)
            al = a.group(4) or a.group(2)
            if tbl in REAL:
                amap.setdefault(al.lower(), tbl)
            # also register table name itself as alias
            if tbl in REAL:
                amap.setdefault(tbl.lower(), tbl)
        ctes = {c.group(1).lower() for c in CTE.finditer(body)}
        # 1. alias.col references (read positions: SELECT/WHERE/ORDER/GROUP/HAVING/ON)
        for c in COLREF.finditer(body):
            al, col = c.group(1).lower(), c.group(2)
            if al in ctes or al in SQL_RESERVED_ALIAS or al in SKIP_ALIAS:
                continue
            tbl = amap.get(al)
            if not tbl:
                continue
            if col not in COLS[tbl]:
                findings.append((line_of(src, base + c.start()), 'REF', tbl, col, f'{al}.{col}'))
        # 2. UPDATE <table> SET <cols>
        for u in UPDATE.finditer(body):
            tbl = u.group(2)
            if tbl not in REAL:
                continue
            setclause = u.group(3)
            # split on top-level commas
            depth = 0; cur = ''; parts = []
            for ch in setclause:
                if ch == '(':
                    depth += 1
                elif ch == ')':
                    depth -= 1
                if ch == ',' and depth == 0:
                    parts.append(cur); cur = ''
                else:
                    cur += ch
            parts.append(cur)
            for p in parts:
                if '=' not in p:
                    continue
                lhs = p.split('=', 1)[0].strip().strip('"')
                if not re.fullmatch(r'[a-zA-Z_]\w*', lhs):
                    continue  # expression / dynamic
                if lhs not in COLS[tbl]:
                    findings.append((line_of(src, base + u.start()), 'UPDATE-SET', tbl, lhs, f'UPDATE {tbl} SET {lhs}'))
        # 3. INSERT INTO <table> (cols)
        for i in INSERT.finditer(body):
            tbl = i.group(2)
            if tbl not in REAL:
                continue
            collist = i.group(3)
            if '{' in collist:  # dynamic f-string column list
                continue
            for raw in collist.split(','):
                col = raw.strip().strip('"')
                if not re.fullmatch(r'[a-zA-Z_]\w*', col):
                    continue
                if col not in COLS[tbl]:
                    findings.append((line_of(src, base + i.start()), 'INSERT', tbl, col, f'INSERT {tbl}({col})'))
    return findings

allf = {}
for d in DIRS:
    for fn in sorted(os.listdir(d)):
        if not fn.endswith('.py'):
            continue
        p = os.path.join(d, fn)
        f = scan_file(p)
        if f:
            allf[p] = f

if '--signatures' in sys.argv:
    # Stable, line-number-independent signatures for the CI ratchet:
    # one per distinct (relpath, kind, table.col). Location changes don't churn it.
    sigs = set()
    for p, items in allf.items():
        rel = p.replace('/root/milkyhoop-dev/backend/api_gateway/app/', '')
        for _line, kind, tbl, col, _ctx in items:
            sigs.add(f'{rel}|{kind}|{tbl}.{col}')
    for s in sorted(sigs):
        print(s)
    sys.exit(0)

total = sum(len(v) for v in allf.values())
print(f'GHOST-COLUMN REFERENCES: {total} across {len(allf)} files\n')
for p in sorted(allf):
    rel = p.replace('/root/milkyhoop-dev/backend/api_gateway/app/', '')
    print(f'### {rel}')
    seen = set()
    for line, kind, tbl, col, ctx in sorted(allf[p]):
        key = (kind, tbl, col)
        tag = '' if key not in seen else '  (dup)'
        seen.add(key)
        wr = 'WRITE' if kind in ('UPDATE-SET', 'INSERT') else 'read'
        print(f'  L{line:<5} [{wr:5}] {tbl}.{col}   <- {ctx}{tag}')
    print()
