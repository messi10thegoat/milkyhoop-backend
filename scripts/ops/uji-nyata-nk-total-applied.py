"""UJI NYATA Nota Kredit / summary.total_applied = Σ `terpakai` jurnal (21 Okt 2026): BACA-SAJA. Mode: baru [out.json]
  summary.total_applied == Σ terpakai baris daftar (non-void) -- tanpa dan dengan ?search=; == Σ independen dari jurnal (kredit Piutang
  CREDIT_NOTE efektif - debit Piutang CREDIT_NOTE_REFUND efektif - sisa untuk bertaut: sisa 0 => kredit - refund); bukan kolom cache."""
import asyncio, inspect, os, sys
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "boneka")
from decimal import Decimal
import asyncpg
from fastapi.params import Query as QP
from starlette.requests import Request
from app.routers import credit_notes as CN

TEN = ["kaos-biru-konveksi", "grapgrap-manado"]
gagal = []


def cek(nama, ok, rinci=""):
    print(("PASS " if ok else "FAIL ") + nama + ("" if ok else f"  {str(rinci)[:200]}"))
    if not ok: gagal.append(nama)


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


async def semua(r, **ov):
    out, skip = [], 0
    while True:
        d = await CN.list_credit_notes(**kw(CN.list_credit_notes, r, limit=100, skip=skip, **ov))
        out += d["items"]
        if not d["has_more"]: return out
        skip += 100


async def main():
    c = await asyncpg.connect(os.environ["URL"]); await c.execute("SET default_transaction_read_only = on")
    class CM:
        async def __aenter__(s): return c
        async def __aexit__(s, *a): return False
    class P:
        def acquire(s): return CM()
    async def gp(): return P()
    CN.get_pool = gp
    for t in TEN:
        uid = str(await c.fetchval("SELECT created_by FROM sales_orders WHERE tenant_id=$1 AND created_by IS NOT NULL LIMIT 1", t)); r = req(t, uid)
        for cari in (None, "CN", "a"):
            items = [i for i in await semua(r, **({"search": cari} if cari else {})) if i["status"] != "void"]
            jml = sum((Decimal(str(i["terpakai"])) for i in items), Decimal(0))
            sm = (await CN.get_credit_notes_summary(**kw(CN.get_credit_notes_summary, r, **({"search": cari} if cari else {}))))["data"]
            cek(f"{t} search={cari}: summary.total_applied == Σ terpakai baris ({len(items)} NK)", Decimal(str(sm["total_applied"])) == jml, (sm["total_applied"], jml))
        # independen dari jurnal (tanpa search)
        ind = Decimal(0)
        for n in await c.fetch("SELECT id, status, original_invoice_id FROM credit_notes WHERE tenant_id=$1 AND status<>'void'", t):
            kp = await c.fetchval("""SELECT COALESCE(SUM(jl.credit),0) FROM journal_entries je JOIN journal_lines jl ON jl.journal_id=je.id
                 JOIN chart_of_accounts coa ON coa.id=jl.account_id AND coa.account_type='RECEIVABLE'
                 WHERE je.tenant_id=$1 AND je.source_id=$2 AND je.source_type='CREDIT_NOTE' AND is_effective_journal(je.id)""", t, n["id"])
            rf = await c.fetchval("""SELECT COALESCE(SUM(jl.debit),0) FROM journal_entries je JOIN journal_lines jl ON jl.journal_id=je.id
                 JOIN chart_of_accounts coa ON coa.id=jl.account_id AND coa.account_type='RECEIVABLE'
                 WHERE je.tenant_id=$1 AND je.source_id=$2 AND je.source_type='CREDIT_NOTE_REFUND' AND is_effective_journal(je.id)""", t, n["id"])
            if n["original_invoice_id"] is not None and n["status"] != "draft":
                ind += Decimal(str(kp)) - Decimal(str(rf))          # bertaut: sisa 0 => seluruh sisa kredit sudah memotong faktur
        # NK bebas: terpakai = kredit - refund - sisa; sisa dari rumus tunggal (total - applied - refunded) hanya untuk bebas; dibanding lewat baris di atas
        bebas = sum((Decimal(str(i["terpakai"])) for i in await semua(r) if i["status"] != "void" and i.get("original_invoice_id") is None), Decimal(0))
        sm = (await CN.get_credit_notes_summary(**kw(CN.get_credit_notes_summary, r)))["data"]
        cek(f"{t} independen jurnal: total_applied == Σ(bertaut: kredit-refund) + Σ(bebas via baris)", Decimal(str(sm["total_applied"])) == ind + bebas, (sm["total_applied"], ind, bebas))
        print(f"INFO {t}: total_applied={sm['total_applied']}")
    print("RINGKAS NK-TOTAL-APPLIED gagal=%d %s" % (len(gagal), gagal[:4]))
    await c.close()
    sys.exit(1 if gagal else 0)

asyncio.run(main())
