"""UJI NYATA Uang Muka Pembelian seragam (22 Okt 2026, P-U3): BACA-SAJA. Mode: baru [out.json]
 A. SINTETIS (vendor_deposits nyata = 0 baris): CTE yang menutupi journal_entries/journal_lines dijalankan pada fragmen SQL YANG SAMA
    (UMD.sql_agregat + sql_aksi): sisa/terpakai/dikembalikan SQL == acuan Python; keadaan_vd/aksi_vd konsisten; jurnal DIBALIK / DRAFT / tenant lain / deposit lain / akun lain tidak ikut.
 B. NYATA (kaos+grapgrap): list/summary/detail/documents dipanggil sungguhan: total == len(?aksi) == summary, sum_*, page/offset/skip, search; SQL fungsi DB
    get_vendor_deposit_applications/refunds RUSAK (kolom b.bill_number / tipe BIGINT) -> diganti SQL langsung UMD.SQL_APLIKASI/SQL_REFUND; berjalan di skema nyata."""
import asyncio, inspect, os, sys, uuid
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "boneka")
from decimal import Decimal
import asyncpg
from fastapi.params import Query as QP
from starlette.requests import Request
from app.routers import vendor_deposits as VD
from app.services import uang_muka_vendor_daftar as UMD, po_daftar as PD

gagal = []
D = Decimal


def cek(nama, ok, rinci=""):
    print(("PASS " if ok else "FAIL ") + nama + ("" if ok else f"  {str(rinci)[:220]}"))
    if not ok: gagal.append(nama)


def U(): return str(uuid.uuid4())


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


def vals(nama, kolom, baris):
    if not baris:
        return f"{nama} AS (SELECT {', '.join(f'NULL::{t} AS {c}' for c, t in kolom)} WHERE false)"
    def lit(v, t):
        if v is None: return f"NULL::{t}"
        return f"'{v}'::{t}" if t in ("uuid", "text") else f"{v}::{t}"
    return f"{nama} AS (SELECT * FROM (VALUES {', '.join('(' + ', '.join(lit(r[c], t) for c, t in kolom) + ')' for r in baris)}) v({', '.join(c for c, _ in kolom)}))"


async def sintetis(c):
    T, T2 = "tenant-uji", "tenant-lain"
    PRE, LAIN = U(), U()
    vd, je, jl, nama = [], [], [], {}
    def dep(n, status, amount=1000):
        i = U(); nama[i] = n; vd.append(dict(id=i, tenant_id=T, status=status, amount=amount)); return i
    def jur(dep_id, tipe, d, k, tenant=T, status="POSTED", rev_by=None, rev_of=None, akun=None):
        i = U(); je.append(dict(id=i, tenant_id=tenant, source_id=dep_id, source_type=tipe, status=status, reversed_by_id=rev_by, reversal_of_id=rev_of))
        jl.append(dict(journal_id=i, account_id=akun or PRE, debit=d, credit=k))
    d1 = dep("utuh", "posted"); jur(d1, "VENDOR_DEPOSIT", 1000, 0)
    d2 = dep("sebagian", "posted"); jur(d2, "VENDOR_DEPOSIT", 1000, 0); jur(d2, "DEPOSIT_APPLICATION", 0, 300)
    d3 = dep("terpakai", "applied"); jur(d3, "VENDOR_DEPOSIT", 1000, 0); jur(d3, "DEPOSIT_APPLICATION", 0, 1000)
    d4 = dep("dikembalikan", "applied"); jur(d4, "VENDOR_DEPOSIT", 1000, 0); jur(d4, "DEPOSIT_REFUND", 0, 1000)
    d5 = dep("sebagian_dikembalikan", "posted"); jur(d5, "VENDOR_DEPOSIT", 1000, 0); jur(d5, "DEPOSIT_REFUND", 0, 400)
    d6 = dep("void (jurnal dibalik)", "void"); jur(d6, "VENDOR_DEPOSIT", 1000, 0, rev_by=U()); jur(d6, "VENDOR_DEPOSIT", 0, 1000, rev_of=U())
    d7 = dep("draf", "draft")
    d8 = dep("noise: jurnal tak ikut", "posted"); jur(d8, "VENDOR_DEPOSIT", 1000, 0)
    jur(d8, "DEPOSIT_APPLICATION", 0, 111, status="DRAFT"); jur(d8, "DEPOSIT_APPLICATION", 0, 222, tenant=T2)        # DRAFT & tenant lain
    jur(d8, "DEPOSIT_APPLICATION", 0, 333, akun=LAIN); jur(d8, "BILL", 0, 444)                                      # akun lain & source_type lain
    jur(d8, "DEPOSIT_APPLICATION", 0, 55, rev_by=U())                                                               # dibalik
    cte = "WITH " + ", ".join([
        vals("vendor_deposits", [("id", "uuid"), ("tenant_id", "text"), ("status", "text"), ("amount", "numeric")], vd),
        vals("journal_entries", [("id", "uuid"), ("tenant_id", "text"), ("source_id", "uuid"), ("source_type", "text"), ("status", "text"), ("reversed_by_id", "uuid"), ("reversal_of_id", "uuid")], je),
        vals("journal_lines", [("journal_id", "uuid"), ("account_id", "uuid"), ("debit", "numeric"), ("credit", "numeric")], jl)]) + " "
    def acuan(dep_id):
        s = t = k = D(0)
        for e in je:
            if e["tenant_id"] != T or e["source_id"] != dep_id or e["source_type"] not in ("VENDOR_DEPOSIT", "DEPOSIT_APPLICATION", "DEPOSIT_REFUND"): continue
            if e["status"] != "POSTED" or e["reversed_by_id"] or e["reversal_of_id"]: continue
            for l in jl:
                if l["journal_id"] == e["id"] and l["account_id"] == PRE:
                    net = D(l["debit"]) - D(l["credit"]); s += net
                    if e["source_type"] == "DEPOSIT_APPLICATION": t -= net
                    if e["source_type"] == "DEPOSIT_REFUND": k -= net
        return s, t, k
    rows = {str(r["id"]): r for r in await c.fetch(cte + f"SELECT vd.id, vd.status, vd.amount, ag.sisa, ag.terpakai, ag.dikembalikan FROM vendor_deposits vd {UMD.sql_agregat('$2')} WHERE vd.tenant_id = $1", T, uuid.UUID(PRE))}
    ok = True; rinci = []
    for i, r in rows.items():
        if (D(r["sisa"]), D(r["terpakai"]), D(r["dikembalikan"])) != acuan(i):
            ok = False; rinci.append((nama[i], (r["sisa"], r["terpakai"], r["dikembalikan"]), acuan(i)))
    cek("sintetis: sisa/terpakai/dikembalikan SQL == acuan Python (8 deposit, jurnal noise tak ikut)", ok, rinci)
    harap_keadaan = {d1: "utuh", d2: "sebagian", d3: "terpakai", d4: "dikembalikan", d5: "sebagian_dikembalikan", d6: "batal", d7: "draf", d8: "utuh"}
    salah = [(nama[i], UMD.keadaan_vd(r["status"], r["amount"], r["terpakai"], r["dikembalikan"], r["sisa"]), harap_keadaan[i]) for i, r in rows.items()
             if UMD.keadaan_vd(r["status"], r["amount"], r["terpakai"], r["dikembalikan"], r["sisa"]) != harap_keadaan[i]]
    cek("sintetis: keadaan_vd dari angka SQL == yang dimaksud skenario", not salah, salah)
    sql = cte + f"SELECT vd.id FROM vendor_deposits vd {UMD.sql_agregat('$2')} WHERE vd.tenant_id = $1 AND {UMD.sql_aksi('belum_diterapkan')}"
    dapat = {str(r["id"]) for r in await c.fetch(sql, T, uuid.UUID(PRE))}
    harap = {i for i, r in rows.items() if UMD.aksi_vd(r["status"], None if r["status"] in ("draft", "void") else r["sisa"])}
    cek("sintetis: ?aksi=belum_diterapkan SQL == aksi_vd Python", dapat == harap == {d1, d2, d5, d8}, (sorted(nama[x] for x in dapat ^ harap),))


async def nyata(c):
    for t in ("kaos-biru-konveksi", "grapgrap-manado"):
        uid = str(await c.fetchval("SELECT created_by FROM sales_orders WHERE tenant_id=$1 AND created_by IS NOT NULL LIMIT 1", t)); r = req(t, uid)
        sm = await VD.get_vendor_deposit_summary(**kw(VD.get_vendor_deposit_summary, r))
        semua = await VD.list_vendor_deposits(**kw(VD.list_vendor_deposits, r, per_page=100, with_totals=True))
        cek(f"{t}: list total == summary deposit_count + void_count ({semua.total})", semua.total == sm.deposit_count + sm.void_count, (semua.total, sm.deposit_count, sm.void_count))
        cek(f"{t}: sum_sisa == Σ sisa baris terbit", abs((semua.sum_sisa or 0) - sum((i.sisa or 0) for i in semua.items if i.status.value not in ("draft", "void"))) < 0.005 or semua.total > 100)
        lst = await VD.list_vendor_deposits(**kw(VD.list_vendor_deposits, r, per_page=100, aksi="belum_diterapkan"))
        cek(f"{t}: ?aksi=belum_diterapkan total == len == summary == pending_count", lst.total == len(lst.items) == sm.belum_diterapkan_count == sm.pending_count and all(i.aksi == "belum_diterapkan" for i in lst.items), (lst.total, sm.belum_diterapkan_count))
        for i in semua.items[:15]:
            dt = await VD.get_vendor_deposit(**kw(VD.get_vendor_deposit, r, deposit_id=i.id))
            beda = [x for x in ("sisa", "terpakai", "dikembalikan", "sisa_keadaan", "status_text", "position_text", "aksi", "documents", "doc_count") if getattr(dt, x) != getattr(i, x)]
            cek(f"{t}: detail == baris {i.deposit_number}", not beda, beda)
            b = await VD.get_vendor_deposit_documents(**kw(VD.get_vendor_deposit_documents, r, deposit_id=i.id))
            cek(f"{t}: documents == bundel {i.deposit_number}", i.documents == PD.documents_baris(b["groups"]) and i.doc_count == len(i.documents))
        p1 = await VD.list_vendor_deposits(**kw(VD.list_vendor_deposits, r, per_page=5, page=2))
        p2 = await VD.list_vendor_deposits(**kw(VD.list_vendor_deposits, r, per_page=5, offset=5))
        p3 = await VD.list_vendor_deposits(**kw(VD.list_vendor_deposits, r, limit=5, skip=5))
        cek(f"{t}: page2 == offset5 == skip5", [x.id for x in p1.items] == [x.id for x in p2.items] == [x.id for x in p3.items])
        for cari in ("VD", "a b"):
            lc = await VD.list_vendor_deposits(**kw(VD.list_vendor_deposits, r, per_page=1, search=cari))
            sc = await VD.get_vendor_deposit_summary(**kw(VD.get_vendor_deposit_summary, r, search=cari))
            cek(f"{t}: summary?search={cari} == daftar total", sc.deposit_count + sc.void_count == lc.total, (sc.deposit_count, sc.void_count, lc.total))
    # SQL fungsi DB detail (dulu diparkir) berjalan di skema nyata
    await c.fetch(UMD.SQL_APLIKASI, uuid.uuid4(), "kaos-biru-konveksi")
    await c.fetch(UMD.SQL_REFUND, uuid.uuid4(), "kaos-biru-konveksi")
    cek("SQL penerapan & pengembalian detail (pengganti 2 fungsi DB yang rusak) berjalan di skema nyata", True)
    print("INFO vendor_deposits nyata = %d baris (0 = pembuktian andalkan sintetis + uji tulis TES E2E)" % await c.fetchval("SELECT count(*) FROM vendor_deposits"))


async def main():
    c = await asyncpg.connect(os.environ["URL"]); await c.execute("SET default_transaction_read_only = on")
    class CM:
        async def __aenter__(s): return c
        async def __aexit__(s, *a): return False
    class P:
        def acquire(s): return CM()
    async def gp(): return P()
    VD.get_pool = gp
    await sintetis(c); await nyata(c)
    print("RINGKAS UM-VENDOR-SERAGAM gagal=%d %s" % (len(gagal), gagal[:4]))
    await c.close(); sys.exit(1 if gagal else 0)

asyncio.run(main())
