"""Harness NYATA rincian JURNAL UM/NK (21 Okt 2026, MASTER). BACA saja (READ ONLY, ROLLBACK), kaos + grapgrap, SEMUA baris.
UM: remaining rincian == compute_deposit_remaining_many; masuk - diterapkan - dikembalikan == remaining; baris daftar diterapkan/dikembalikan
== rincian (draf 0). NK: total == terpakai + sisa + dikembalikan + kelebihan KRD (NK terbit); baris daftar terpakai/dikembalikan == rincian.
Selisih dengan CACHE (amount_applied/amount_refunded) DILAPORKAN, bukan disamakan (putusan MASTER)."""
import asyncio, inspect, json, os, sys
from decimal import Decimal
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "x")
import asyncpg
from fastapi.params import Query as QP
from starlette.requests import Request
from app.routers import customer_deposits as CD, credit_notes as CN
from app.services import uang_muka_daftar as U, nota_kredit_daftar as N

gagal = 0


def cek(ok, l, i=""):
    global gagal
    print(("PASS " if ok else "FAIL ") + l + (f" | {i}" if i else "")); gagal += 0 if ok else 1


def req(t, uid):
    async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
    r = Request({"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b""}, receive); r.state.user = {"tenant_id": t, "user_id": uid}; return r


def kw(fn, r, **ov):
    o = {}
    for n, p in inspect.signature(fn).parameters.items():
        if n == "request": o[n] = r
        elif n in ov: o[n] = ov[n]
        elif isinstance(p.default, QP): o[n] = p.default.default
        elif p.default is not inspect._empty: o[n] = p.default
    return o


def J(x): return json.loads(json.dumps(x if isinstance(x, dict) else x.model_dump(), default=str))


async def semua(fn, r):
    rows, skip = [], 0
    while True:
        x = J(await fn(**kw(fn, r, skip=skip, limit=100)))
        rows += x["items"]; skip += 100
        if skip >= x["total"]: break
    return rows


async def main():
    c = await asyncpg.connect(os.environ["DATABASE_URL"]); tx = c.transaction(readonly=True); await tx.start()
    class CM:
        async def __aenter__(s): return c
        async def __aexit__(s, *a): return False
    class P:
        def acquire(s): return CM()
    async def gp(): return P()
    CD.get_pool = gp; CN.get_pool = gp
    try:
        for t in ("kaos-biru-konveksi", "grapgrap-manado"):
            uid = str(await c.fetchval('SELECT id FROM "User" WHERE "tenantId"=$1 ORDER BY "createdAt" LIMIT 1', t))
            r = req(t, uid)
            # ---- UM
            um = await c.fetch("SELECT id, deposit_number, status, amount, amount_applied, amount_refunded FROM customer_deposits WHERE tenant_id=$1", t)
            ids = [x["id"] for x in um if x["status"] != "draft"]
            rin = await U.rincian_um_many(c, t, ids)
            per = await CD.compute_deposit_remaining_many(c, t, ids)
            e1 = [k for k in per if rin[k]["remaining"] != per[k]]
            e2 = [k for k, v in rin.items() if v["masuk"] - v["diterapkan"] - v["dikembalikan"] != v["remaining"]]
            cek(not e1, f"{t} UM: remaining rincian == compute_deposit_remaining_many ({len(per)})", str(e1[:3]))
            cek(not e2, f"{t} UM: masuk - diterapkan - dikembalikan == sisa jurnal", str(e2[:3]))
            rows = {x["id"]: x for x in await semua(CD.list_customer_deposits, r)}
            e3 = [(x["deposit_number"]) for x in um if rows[str(x["id"])]["diterapkan"] != float(rin.get(str(x["id"]), {}).get("diterapkan", 0))
                  or rows[str(x["id"])]["dikembalikan"] != float(rin.get(str(x["id"]), {}).get("dikembalikan", 0))]
            cek(not e3, f"{t} UM: baris daftar diterapkan/dikembalikan == rincian jurnal ({len(um)})", str(e3[:3]))
            ca = [(x["deposit_number"], x["status"], str(x["amount_applied"]), str(rin[str(x["id"])]["diterapkan"]))
                  for x in um if x["status"] not in ("draft", "void") and Decimal(str(x["amount_applied"] or 0)) != rin[str(x["id"])]["diterapkan"]]
            cr = [(x["deposit_number"], x["status"], str(x["amount_refunded"]), str(rin[str(x["id"])]["dikembalikan"]))
                  for x in um if x["status"] not in ("draft", "void") and Decimal(str(x["amount_refunded"] or 0)) != rin[str(x["id"])]["dikembalikan"]]
            print(f"  LAPOR {t} UM selisih cache: amount_applied {len(ca)} {ca[:3]} | amount_refunded {len(cr)} {cr[:3]}")
            # ---- NK
            nk = await c.fetch("""SELECT cn.id, cn.credit_note_number, cn.status, cn.total_amount, cn.amount_applied, cn.amount_refunded,
                                         COALESCE((SELECT d.amount FROM customer_deposits d WHERE d.id = cn.created_deposit_id AND d.status <> 'void'), 0) AS krd
                                  FROM credit_notes cn WHERE cn.tenant_id=$1""", t)
            rn = await N.rincian_nk_many(c, t, [x["id"] for x in nk])
            e4 = [(x["credit_note_number"], x["status"], str(x["total_amount"]), {k: str(v) for k, v in rn[str(x["id"])].items()}, str(x["krd"]))
                  for x in nk if x["status"] not in ("draft", "void")
                  and Decimal(str(x["total_amount"])) != rn[str(x["id"])]["terpakai"] + rn[str(x["id"])]["sisa"] + rn[str(x["id"])]["dikembalikan"] + Decimal(str(x["krd"]))]
            cek(not e4, f"{t} NK: total == terpakai + sisa + dikembalikan + kelebihan KRD ({sum(1 for x in nk if x['status'] not in ('draft','void'))} NK terbit)", str(e4[:2]))
            e5 = [x["credit_note_number"] for x in nk if x["status"] in ("draft", "void") and (rn[str(x["id"])]["terpakai"] != 0 or rn[str(x["id"])]["dikembalikan"] != 0)]
            cek(not e5, f"{t} NK: draf/void -> terpakai = dikembalikan = 0", str(e5[:3]))
            rows = {x["id"]: x for x in await semua(CN.list_credit_notes, r)}
            e6 = [x["credit_note_number"] for x in nk if rows[str(x["id"])]["terpakai"] != float(rn[str(x["id"])]["terpakai"])
                  or rows[str(x["id"])]["dikembalikan"] != float(rn[str(x["id"])]["dikembalikan"])]
            cek(not e6, f"{t} NK: baris daftar terpakai/dikembalikan == rincian jurnal ({len(nk)})", str(e6[:3]))
            ca = [(x["credit_note_number"], x["status"], str(x["amount_applied"]), str(rn[str(x["id"])]["terpakai"]))
                  for x in nk if x["status"] not in ("draft", "void") and Decimal(str(x["amount_applied"] or 0)) != rn[str(x["id"])]["terpakai"]]
            cr = [(x["credit_note_number"], x["status"], str(x["amount_refunded"]), str(rn[str(x["id"])]["dikembalikan"]))
                  for x in nk if x["status"] not in ("draft", "void") and Decimal(str(x["amount_refunded"] or 0)) != rn[str(x["id"])]["dikembalikan"]]
            print(f"  LAPOR {t} NK selisih cache: amount_applied {len(ca)} {ca[:3]} | amount_refunded {len(cr)} {cr[:3]}")
    finally:
        await tx.rollback(); await c.close()
    print("RINGKAS gagal=%d" % gagal)

asyncio.run(main())
