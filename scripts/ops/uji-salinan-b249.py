"""BUG-249 uji DB di SALINAN (DRAF, belum pernah dijalankan; perbaiki di slot): handler ASLI atas klon, tenant kaos + nyawit.
A fakta: customers.name NULL, customers.nama terisi (alasan perbaikan)
B Biaya: pelanggan tertagih di SALINAN (UPDATE expenses.billed_to_customer_id) -> GET /expenses/{id} dan GET /expenses (daftar) memuat billed_to_customer_name == customers.nama
C Cek: pelanggan di SALINAN (UPDATE cheques.customer_id) -> GET /cheques/{id}.data.customer_name == customers.nama
Bila data dasar tak ada (tak ada biaya/cek di tenant) -> CATATAN (bukan FAIL) agar tenant tanpa data tak merahkan; bila UPDATE uji ditolak trigger -> FAIL (kasus tak terukur)."""
import asyncio
import inspect
import sys

from fastapi.params import Query as _Q
from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import cheques as CH  # noqa: E402
from app.routers import expenses as EX  # noqa: E402

NY, KA = "pt-kita-nyawit-bersama", "kaos-biru-konveksi"
HASIL = []


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


def req(tid, uid):
    r = Request({"type": "http", "method": "GET", "path": "/x", "headers": []})
    r.state.user = {"tenant_id": tid, "user_id": str(uid), "username": "uji-salinan", "role": "OWNER"}
    return r


async def daftar_biaya(rq, **kw):
    p = {}
    for n, v in inspect.signature(EX.list_expenses).parameters.items():
        if n != "request":
            p[n] = v.default.default if isinstance(v.default, _Q) else (None if v.default is inspect._empty else v.default)
    p.update(kw)
    return await EX.list_expenses(rq, **p)


async def main():
    pool = await EX.get_pool()
    async with pool.acquire() as c:
        for tid in (KA, NY):
            n_nama = await c.fetchval("/* tenant_id */ SELECT count(*) FROM customers WHERE tenant_id = $1 AND nama IS NOT NULL AND nama <> ''", tid)
            n_name = await c.fetchval("/* tenant_id */ SELECT count(*) FROM customers WHERE tenant_id = $1 AND name IS NOT NULL AND name <> ''", tid)
            cek(f"A {tid}: customers.nama terisi ({n_nama}), customers.name kosong ({n_name})", n_nama > 0 and n_name == 0)
            uid = await c.fetchval("/* tenant_id */ SELECT utr.user_id FROM user_tenant_roles utr JOIN roles r ON r.id = utr.role_id WHERE utr.tenant_id = $1 AND utr.status = 'ACTIVE' AND r.name ILIKE '%owner%' LIMIT 1", tid)
            cus = await c.fetchrow("/* tenant_id */ SELECT id, nama FROM customers WHERE tenant_id = $1 AND nama IS NOT NULL AND nama <> '' ORDER BY created_at LIMIT 1", tid)
            if uid is None or cus is None:
                print(f"CATATAN {tid}: tanpa pemilik/pelanggan, B dan C dilewati", flush=True)
                continue
            rq = req(tid, uid)
            eid = await c.fetchval("/* tenant_id */ SELECT id FROM expenses WHERE tenant_id = $1 ORDER BY created_at DESC LIMIT 1", tid)
            if eid is None:
                print(f"CATATAN {tid}: tak ada Biaya, B dilewati", flush=True)
            else:
                try:
                    await c.execute("/* tenant_id */ UPDATE expenses SET billed_to_customer_id = $3 WHERE id = $1 AND tenant_id = $2", eid, tid, cus["id"])
                    d = await EX.get_expense_detail(rq, eid)
                    dd = d["data"] if isinstance(d, dict) else d.data
                    nm = dd["billed_to_customer_name"] if isinstance(dd, dict) else dd.billed_to_customer_name
                    cek(f"B {tid}: detail Biaya billed_to_customer_name '{nm}' == customers.nama '{cus['nama']}'", nm == cus["nama"])
                    lst = await daftar_biaya(rq, limit=100)
                    daftar = lst["items"] if isinstance(lst, dict) else lst.items   # handler bisa mengembalikan dict atau model
                    g = lambda o, k: o[k] if isinstance(o, dict) else getattr(o, k)   # noqa: E731
                    baris = [i for i in daftar if str(g(i, "id")) == str(eid)]
                    cek(f"B {tid}: daftar Biaya (100 teratas) baris itu billed_to_customer_name == nama", (not baris) or g(baris[0], "billed_to_customer_name") == cus["nama"],
                        "baris tak di 100 teratas (dilewati)" if not baris else "")
                except Exception as e:  # noqa: BLE001
                    cek(f"B {tid}: kasus Biaya tak terukur: {type(e).__name__}", False, str(e)[:200])
            chq = await c.fetchval("/* tenant_id */ SELECT id FROM cheques WHERE tenant_id = $1 AND cheque_type = 'received' ORDER BY created_at DESC LIMIT 1", tid)
            if chq is None:
                print(f"CATATAN {tid}: tak ada Cek diterima, C dilewati", flush=True)
            else:
                try:
                    await c.execute("/* tenant_id */ UPDATE cheques SET customer_id = $3 WHERE id = $1 AND tenant_id = $2", chq, tid, cus["id"])
                    r = await CH.get_cheque(rq, chq)
                    nm = r.data.customer_name
                    cek(f"C {tid}: detail Cek customer_name '{nm}' == customers.nama '{cus['nama']}'", nm == cus["nama"])
                except Exception as e:  # noqa: BLE001
                    cek(f"C {tid}: kasus Cek tak terukur: {type(e).__name__}", False, str(e)[:200])
    print(f"HASIL: {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    sys.exit(0 if all(HASIL) else 1)


asyncio.run(main())
