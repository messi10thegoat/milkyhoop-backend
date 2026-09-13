"""Sapuan PEMBACA untuk V247 (credit_notes.customer_id & customer_deposits.customer_id: varchar -> uuid). Baca-saja.

A. KESIAPAN FK: nilai terisi yang tak ada di customers (tenant sama) per tabel.
B. FUNGSI DB / TRIGGER / VIEW yang menyebut kolom itu (pg_proc badan + pg_trigger + pg_views) — badan plpgsql
   TIDAK tercatat di pg_depend, jadi dipindai teksnya.
C. MODEL RESPONS Pydantic yang punya medan customer_id: tipe anotasi (str -> UUID objek akan GAGAL validasi di v2).
   Uji perilaku langsung: model(customer_id=uuid.UUID(...)) valid atau tidak.
D. SITUS PEMBACA Python: di mana nilai customer_id dari kedua tabel mengalir (row["customer_id"], .get,
   dict(row)/**row ke model, str(...), json, pembanding). Jendela baris sekitar SELECT dari kedua tabel +
   berkas router kedua domain. Kontrol positif: credit_notes.py banding 1507 & sales_invoices.py:4334 str(...)
   & receive_payments.py DP sd.customer_id join HARUS muncul.
"""
import importlib
import inspect
import os
import re
import subprocess
import sys
import uuid

sys.path.insert(0, "/app/backend/api_gateway") if os.path.exists("/app/backend/api_gateway") else None


def psql(q):
    return subprocess.run(["docker", "exec", "milkyhoop-dev-postgres-1", "psql", "-U", "postgres", "-d", "milkydb", "-At", "-c", q],
                          capture_output=True, text=True, check=True).stdout.strip()


FASE = sys.argv[1] if len(sys.argv) > 1 else "host"

if FASE == "host":
    print("== A. KESIAPAN FK")
    print(psql("""SELECT 'credit_notes terisi='||count(*) FILTER (WHERE customer_id IS NOT NULL AND customer_id<>'')
                 ||' tak_ada_di_customers_tenant='||count(*) FILTER (WHERE customer_id IS NOT NULL AND customer_id<>''
                    AND NOT EXISTS (SELECT 1 FROM customers c WHERE c.id::text=lower(btrim(cn.customer_id)) AND c.tenant_id=cn.tenant_id))
               FROM credit_notes cn"""))
    print(psql("""SELECT 'customer_deposits terisi='||count(*) FILTER (WHERE customer_id IS NOT NULL AND customer_id<>'')
                 ||' tak_ada_di_customers_tenant='||count(*) FILTER (WHERE customer_id IS NOT NULL AND customer_id<>''
                    AND NOT EXISTS (SELECT 1 FROM customers c WHERE c.id::text=lower(btrim(cd.customer_id)) AND c.tenant_id=cd.tenant_id))
                 ||' string_kosong='||count(*) FILTER (WHERE customer_id='')
               FROM customer_deposits cd"""))
    print(psql("SELECT 'FK customers(id) sudah ada di tabel lain: '||count(*) FROM pg_constraint WHERE contype='f' AND confrelid='customers'::regclass"))
    print(psql("SELECT 'index pada kolom: '||string_agg(indexname||'('||tablename||')', ', ') FROM pg_indexes WHERE tablename IN ('credit_notes','customer_deposits') AND indexdef ILIKE '%customer_id%'"))

    print("\n== B. OBJEK DB yang menyebut kolom (badan fungsi, trigger, view)")
    rows = psql("""SELECT p.proname||'|'||
                   (CASE WHEN pg_get_functiondef(p.oid) ~* '(credit_notes|customer_deposits)' THEN 't' ELSE 'f' END)||'|'||
                   (SELECT string_agg(DISTINCT m[1], ',') FROM regexp_matches(pg_get_functiondef(p.oid),
                      '([a-z_]+\\.customer_id(?:::[a-z]+)?\\s*(?:=|<>|IN)\\s*[a-z_$0-9.]+(?:::[a-z]+)?)', 'gi') AS m)
                   FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                   WHERE n.nspname='public' AND p.prokind IN ('f','p')
                     AND pg_get_functiondef(p.oid) ~* '(credit_notes|customer_deposits)'
                     AND pg_get_functiondef(p.oid) ~* 'customer_id'""")
    for r in rows.split("\n"):
        if r:
            print("   fungsi:", r)
    print("   trigger pada kedua tabel:", psql("SELECT string_agg(tgname||'->'||tgfoid::regproc::text, ', ') FROM pg_trigger WHERE tgrelid IN ('credit_notes'::regclass,'customer_deposits'::regclass) AND NOT tgisinternal"))
    print("   view:", psql("SELECT coalesce(string_agg(viewname, ','), '0') FROM pg_views WHERE schemaname='public' AND definition ~* '(credit_notes|customer_deposits)' AND definition ~* 'customer_id'"))
    print("   matview:", psql("SELECT coalesce(string_agg(matviewname, ','), '0') FROM pg_matviews WHERE definition ~* '(credit_notes|customer_deposits)'"))

    print("\n== D. SITUS PEMBACA Python")
    ROOT = "/root/milkyhoop-dev/backend/api_gateway/app"
    situs = []
    for d, _, fs in os.walk(ROOT):
        if "__pycache__" in d:
            continue
        for f in fs:
            if not f.endswith(".py"):
                continue
            p = os.path.join(d, f)
            b = open(p, encoding="utf-8", errors="ignore").read().split("\n")
            rel = p.replace(ROOT + "/", "")
            for i, ln in enumerate(b):
                if re.search(r"\b(from|join)\s+(credit_notes|customer_deposits)\b", ln, re.I):
                    # 60 baris sesudah SELECT: cari pemakaian customer_id pada baris hasil
                    for j in range(i, min(i + 60, len(b))):
                        lj = b[j]
                        if re.search(r'\[\s*["\']customer_id["\']\s*\]|\.get\(\s*["\']customer_id["\']|\bdict\(\s*\w+\s*\)|\*\*\s*dict\(|\*\*\w+\b', lj):
                            situs.append((rel, j + 1, lj.strip()[:110]))
    situs = sorted(set(situs))
    for s in situs:
        print(f"   {s[0]}:{s[1]}  {s[2]}")
    print(f"   cacah situs pembaca: {len(situs)}")
    k1 = any(s[0] == "routers/credit_notes.py" and "customer_id" in s[2] and "!=" in s[2] for s in situs)
    k2 = any(s[0] == "routers/sales_invoices.py" for s in situs)
    print(f"   KONTROL K1 banding CN={k1}  K2 sales_invoices pembaca DP={k2}")
else:
    print("== C. MODEL RESPONS Pydantic dgn medan customer_id (perilaku: diberi uuid.UUID)")
    import pkgutil
    import app.schemas as S
    uji = uuid.uuid4()
    for modinfo in pkgutil.iter_modules(S.__path__):
        if not re.search(r"credit_note|customer_deposit|customer|receive|sales|dashboard|report", modinfo.name):
            continue
        m = importlib.import_module(f"app.schemas.{modinfo.name}")
        for n, c in inspect.getmembers(m, inspect.isclass):
            if getattr(c, "__module__", "") != m.__name__ or not hasattr(c, "model_fields") or "customer_id" not in c.model_fields:
                continue
            ann = c.model_fields["customer_id"].annotation
            try:
                c.model_validate({**{k: None for k in c.model_fields}, "customer_id": uji}, strict=False)
                perilaku = "terima UUID"
            except Exception as e:  # noqa: BLE001
                errs = [x for x in getattr(e, "errors", lambda: [])() if x.get("loc", [None])[0] == "customer_id"]
                perilaku = ("TOLAK UUID: " + errs[0]["type"]) if errs else "terima UUID (galat lain diabaikan)"
            print(f"   {modinfo.name}.{n}  customer_id: {str(ann)[:40]}  -> {perilaku}")
