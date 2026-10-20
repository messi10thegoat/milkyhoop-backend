"""Harness NYATA S2-UM uang muka seragam (20 Okt 2026). BACA saja (transaksi READ ONLY, ROLLBACK), grapgrap + kaos, SEMUA uang muka.
 1. sql_sisa_semua (teks turunan) == compute_deposit_remaining_many per uang muka bukan-draf
 2. tiap baris daftar: aksi == aksi_um(sisa jurnal); sisa_keadaan konsisten dgn sisa jurnal; status_text == peta FE r288
 3. ?aksi=belum_diterapkan == himpunan baris ber-aksi; summary {aksi}_count/_value == jumlah/Σ sisa himpunan itu
 4. with_totals (tanpa filter, ?aksi=, ?search=) == Σ dari semua baris hasil filter
 5. summary ?search= : total == baris daftar bukan-void dgn ?search= yang sama
 6. detail == baris daftar (aksi, sisa_keadaan, status_text, remaining_amount, remaining_state)"""
import asyncio, inspect, json, os, sys
from decimal import Decimal
sys.path.insert(0, "/app"); os.environ.setdefault("OPENAI_API_KEY", "x")
from types import SimpleNamespace
import asyncpg
from uuid import UUID
from backend.api_gateway.app.routers import customer_deposits as CD
from backend.api_gateway.app.services import uang_muka_daftar as U
from backend.api_gateway.app.services.role_resolver import AccountRole, resolve_account_id_by_role

# peta FE r288 (frontend recipes/statusUangMuka.ts LABEL_STATUS_UANG_MUKA), kunci = status_detail
FE_R288 = {"draft": "Draf", "posted": "Diterima", "partial": "Sebagian terpakai", "applied": "Terpakai", "void": "Batal",
           "refunded": "Dikembalikan", "partially_refunded": "Sebagian dikembalikan"}
gagal = 0


def cek(ok, l, i=""):
    global gagal
    print(("PASS " if ok else "FAIL ") + l + (f" | {i}" if i else "")); gagal += 0 if ok else 1


def kw(fn, **o):
    d = {n: getattr(p.default, "default", p.default) for n, p in inspect.signature(fn).parameters.items()
         if n != "request" and p.default is not inspect.Parameter.empty}
    d.update(o); return d


async def semua(req, **o):
    out, skip, r = [], 0, None
    while True:
        r = await CD.list_customer_deposits(req, **kw(CD.list_customer_deposits, skip=skip, limit=100, **o))
        r = r if isinstance(r, dict) else r.model_dump()
        out += r["items"]; skip += 100
        if skip >= r["total"]: break
    return out, r


async def main():
    c = await asyncpg.connect(os.environ["DATABASE_URL"])
    tx = c.transaction(readonly=True); await tx.start()
    class Pool:
        def acquire(self_):
            class A:
                async def __aenter__(s): return c
                async def __aexit__(s, *a): return False
            return A()
    async def gp(): return Pool()
    CD.get_pool = gp
    # SET LOCAL di jalur baca: aman dalam transaksi read-only
    try:
        for t in ("grapgrap-manado", "kaos-biru-konveksi"):
            uid = await c.fetchval('SELECT id FROM "User" WHERE "tenantId"=$1 ORDER BY "createdAt" LIMIT 1', t)
            req = SimpleNamespace(state=SimpleNamespace(user={"tenant_id": t, "user_id": str(uid)}), headers={})
            akun = await resolve_account_id_by_role(c, t, AccountRole.CUSTOMER_DEPOSIT_LIABILITY)
            # 1
            sem = {str(r["id"]): r["remaining"] for r in await c.fetch(U.sql_sisa_semua("$2"), t, akun)}
            ids = [r["id"] for r in await c.fetch("SELECT id FROM customer_deposits WHERE tenant_id=$1 AND status <> 'draft'", t)]
            per = await CD.compute_deposit_remaining_many(c, t, ids)
            b1 = [k for k in per if Decimal(str(sem.get(k))) != per[k]]
            cek(not b1 and set(sem) == set(per), f"{t}: sisa semua (teks turunan) == compute_deposit_remaining_many ({len(per)} UM)", str(b1[:3]))
            # 2
            rows, r0 = await semua(req)
            n_db = await c.fetchval("SELECT count(*) FROM customer_deposits WHERE tenant_id=$1", t)
            cek(len(rows) == n_db, f"{t}: daftar memuat semua UM", f"{len(rows)} vs {n_db}")
            e_aksi, e_kd, e_tx = [], [], []
            for x in rows:
                sisa = None if x["status"] == "draft" else per[x["id"]]
                if x["aksi"] != U.aksi_um(x["status"], sisa): e_aksi.append((x["deposit_number"], x["aksi"], str(sisa)))
                if not U.keadaan_cocok_sisa(x["sisa_keadaan"], x["amount"], sisa): e_kd.append((x["deposit_number"], x["sisa_keadaan"], x["amount"], str(sisa)))
                if x["status_text"] != FE_R288.get(x["status_detail"]): e_tx.append((x["deposit_number"], x["status_detail"], x["status_text"]))
            cek(not e_aksi, f"{t}: aksi baris == aksi_um(sisa jurnal)", str(e_aksi[:3]))
            cek(not e_kd, f"{t}: sisa_keadaan konsisten dengan sisa jurnal", str(e_kd[:3]))
            cek(not e_tx, f"{t}: status_text == peta FE r288", str(e_tx[:3]))
            hit = {}
            for x in rows: hit[x["sisa_keadaan"]] = hit.get(x["sisa_keadaan"], 0) + 1
            print("  keadaan", t, json.dumps(hit, sort_keys=True))
            # 3
            harap = {x["id"] for x in rows if x["aksi"] == U.AKSI_BELUM_DITERAPKAN}
            ra, _ = await semua(req, aksi=U.AKSI_BELUM_DITERAPKAN)
            cek({x["id"] for x in ra} == harap, f"{t}: ?aksi=belum_diterapkan == baris ber-aksi ({len(harap)})")
            sm = (await CD.get_customer_deposits_summary(req, **kw(CD.get_customer_deposits_summary)))
            sm = sm if isinstance(sm, dict) else sm.model_dump()
            d = sm["data"]
            nilai = sum(per[i] for i in harap)
            cek(d["belum_diterapkan_count"] == len(harap) and Decimal(str(d["belum_diterapkan_value"])) == nilai,
                f"{t}: summary belum_diterapkan == daftar", f"{d['belum_diterapkan_count']}/{d['belum_diterapkan_value']} vs {len(harap)}/{nilai}")
            # 4
            kata = next((x["customer_name"].split()[0] for x in rows if x["customer_name"]), "a")
            for o in ({}, {"aksi": U.AKSI_BELUM_DITERAPKAN}, {"search": kata}):
                rr, rt = await semua(req, with_totals=True, **o)
                st = sum(Decimal(str(x["amount"])) for x in rr if x["status"] != "void")
                ss = sum(per[x["id"]] for x in rr if x["status"] not in ("draft", "void"))
                cek(Decimal(str(rt["sum_total"])) == st and Decimal(str(rt["sum_sisa"])) == ss,
                    f"{t}: with_totals {o or 'tanpa filter'} == Σ baris", f"{rt['sum_total']}/{rt['sum_sisa']} vs {st}/{ss}")
            # 5
            rs, _ = await semua(req, search=kata)
            ss = (await CD.get_customer_deposits_summary(req, **kw(CD.get_customer_deposits_summary, search=kata)))
            ss = ss if isinstance(ss, dict) else ss.model_dump()
            cek(ss["data"]["total"] == sum(1 for x in rs if x["status"] != "void")
                and ss["data"]["belum_diterapkan_count"] == sum(1 for x in rs if x["aksi"] == U.AKSI_BELUM_DITERAPKAN),
                f"{t}: summary ?search={kata!r} == daftar ?search= ({len(rs)} baris)")
            # 6
            e_det = []
            for x in rows:
                det = await CD.get_customer_deposit(req, UUID(x["id"]))
                det = (det if isinstance(det, dict) else det.model_dump())["data"]
                for f in ("aksi", "sisa_keadaan", "status_text", "remaining_amount", "remaining_state"):
                    if det.get(f) != x.get(f): e_det.append((x["deposit_number"], f, det.get(f), x.get(f)))
            cek(not e_det, f"{t}: detail == baris daftar ({len(rows)} UM)", str(e_det[:3]))
    finally:
        await tx.rollback(); await c.close()
    print("RINGKAS gagal=%d" % gagal)

asyncio.run(main())
