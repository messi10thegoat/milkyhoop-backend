"""GERBANG V247 L1 — 74 rute terpilih-berbasis-tabel, GET DAN tulis, LINTAS TIPE, satu transaksi ROLLBACK.

- Koneksi dibungkus PENCATAT SQL: setiap rute dicatat apakah SQL-nya BENAR-BENAR menyentuh credit_notes /
  customer_deposits (konfirmasi eksekusi atas pemilih teks).
- Body model diisi heuristik dari nama medan (id dikenal, tanggal, nominal kecil). Body yang gagal dibangun, atau
  rute yang tak mencapai tabel di fase A, dilabeli 'TIDAK TERJANGKAU ALAT' — BUKAN lulus.
- Fase A (varchar) -> badan V247 + kosongkan cache statement (meniru restart) -> Fase B (uuid).
- Hasil GET divalidasi thd response_model rute.
- Verdict per rute yang terjangkau: status & jenis galat A == B. 'Dikecualikan': merge_customers (rusak sebelum V247).
- merge dicatat apa adanya. Hasil di memori; ringkasan cacah dihitung dari hasil, bukan ketikan.
"""
import asyncio
import importlib
import inspect
import json
import os
import re
import sys
import typing
import uuid
from datetime import date, datetime
from decimal import Decimal

sys.path.insert(0, "/app/backend/api_gateway")
import asyncpg  # noqa: E402
from starlette.requests import Request  # noqa: E402
from fastapi.params import Param, Depends  # noqa: E402
from pydantic import BaseModel, TypeAdapter  # noqa: E402

T = "kaos-biru-konveksi"
TABEL = re.compile(r"\b(credit_notes|customer_deposits)\b", re.I)
rute = json.load(open("/tmp/rute_terpilih.json"))
catatan_sql = []


class Pencatat:
    def __init__(self, c):
        self._c = c

    def __getattr__(self, n):
        a = getattr(self._c, n)
        if n in ("execute", "fetch", "fetchrow", "fetchval", "executemany", "prepare", "cursor"):
            def bungkus(q, *args, **kw):
                if isinstance(q, str):
                    catatan_sql.append(q)
                return a(q, *args, **kw)
            return bungkus
        return a


def isi_medan(tipe, nama, id_dikenal):
    asal = typing.get_origin(tipe)
    args = typing.get_args(tipe)
    if asal is typing.Union:
        non = [a for a in args if a is not type(None)]
        return isi_medan(non[0], nama, id_dikenal) if non else None
    if asal in (list, typing.List):
        el = args[0] if args else str
        return [isi_medan(el, nama, id_dikenal)] if (isinstance(el, type) and issubclass(el, BaseModel)) else []
    if isinstance(tipe, type) and issubclass(tipe, BaseModel):
        return {n: isi_medan(f.annotation, n, id_dikenal) for n, f in tipe.model_fields.items() if f.is_required()}
    if nama in id_dikenal:
        v = id_dikenal[nama]
        return uuid.UUID(str(v)) if tipe is uuid.UUID else str(v)
    if tipe in (date,) or "date" in nama:
        return date.today()
    if tipe in (datetime,):
        return datetime.now()
    if tipe in (int, float, Decimal) or re.search(r"amount|quantity|price|total", nama):
        return 1000 if tipe is not float else 1.0
    if tipe is bool:
        return False
    if tipe is uuid.UUID:
        return uuid.uuid4()
    lit = typing.get_args(tipe) if asal is typing.Literal else None
    if lit:
        return lit[0]
    return "uji gerbang"


async def main():
    raw = await asyncpg.connect(os.environ["DATABASE_URL"])
    conn = Pencatat(raw)
    import app.services.db_pool as dbp

    class FakePool:
        def acquire(self, *a, **k):
            class C:
                async def __aenter__(s):
                    return conn
                async def __aexit__(s, *e):
                    return False
            return C()
        async def release(self, c):
            return None

    async def fake(*a, **k):
        return FakePool()
    dbp.get_db_pool = fake

    import importlib.util as iu
    kode_baru = {}
    for m in ("credit_notes", "customer_deposits", "customers", "sales_invoices", "receive_payments"):
        spec = iu.spec_from_file_location(f"app.routers.{m}", f"/tmp/v247/{m}.py")
        mod = iu.module_from_spec(spec)
        sys.modules[f"app.routers.{m}"] = mod
        spec.loader.exec_module(mod)
        kode_baru[m] = mod

    uid = await raw.fetchval("SELECT created_by FROM bank_transactions WHERE tenant_id=$1 AND created_by IS NOT NULL LIMIT 1", T)
    async def q(s):
        # tahan galat per subjek: run pertama gagal total karena tebakan nama tabel 'tax_invoices' yg tak ada
        try:
            return await raw.fetchval(s, T)
        except asyncpg.PostgresError as e:
            print(f"   ALAT: subjek tak dapat dicari ({e.__class__.__name__}): {s[:70]}")
            return None
    ID = {
        "credit_note_id": await q("SELECT id FROM credit_notes WHERE tenant_id=$1 AND customer_id IS NOT NULL ORDER BY status='draft' DESC, id LIMIT 1"),
        "deposit_id": await q("SELECT id FROM customer_deposits WHERE tenant_id=$1 AND customer_id IS NOT NULL ORDER BY status='draft' DESC, id LIMIT 1"),
        "customer_id": await q("SELECT customer_id FROM credit_notes WHERE tenant_id=$1 AND customer_id IS NOT NULL LIMIT 1"),
        "invoice_id": await q("SELECT id FROM sales_invoices WHERE tenant_id=$1 AND journal_id IS NOT NULL LIMIT 1"),
        "account_id": await q("SELECT coa_id FROM bank_accounts WHERE tenant_id=$1 AND is_active LIMIT 1"),
        "bank_account_id": await q("SELECT id FROM bank_accounts WHERE tenant_id=$1 AND is_active LIMIT 1"),
        "vendor_id": await q("SELECT id FROM vendors WHERE tenant_id=$1 LIMIT 1"),
        "vendor_credit_id": await q("SELECT id FROM vendor_credits WHERE tenant_id=$1 LIMIT 1"),
        "proforma_id": await q("SELECT id FROM proformas WHERE tenant_id=$1 LIMIT 1"),
        "quote_id": await q("SELECT id FROM quotes WHERE tenant_id=$1 LIMIT 1"),
        "order_id": await q("SELECT id FROM sales_orders WHERE tenant_id=$1 LIMIT 1"),
        "sales_order_id": await q("SELECT id FROM sales_orders WHERE tenant_id=$1 LIMIT 1"),
        "payment_id": await q("SELECT id FROM receive_payments WHERE tenant_id=$1 LIMIT 1"),
        "expense_id": await q("SELECT id FROM expenses WHERE tenant_id=$1 LIMIT 1"),
        "bill_id": await q("SELECT id FROM bills WHERE tenant_id=$1 AND journal_id IS NOT NULL LIMIT 1"),
        "tax_invoice_id": await q("SELECT id FROM tax_invoices WHERE tenant_id=$1 LIMIT 1"),
        "application_id": await q("SELECT id FROM customer_deposit_applications WHERE tenant_id=$1 LIMIT 1"),
        "attachment_id": uuid.uuid4(),
    }

    def req(body=None):
        isi = json.dumps(body, default=str).encode() if body is not None else b""

        async def terima():
            return {"type": "http.request", "body": isi, "more_body": False}
        return Request({"type": "http", "method": "POST", "path": "/", "query_string": b"",
                        "headers": [(b"content-type", b"application/json")],
                        "state": {"user": {"tenant_id": T, "user_id": str(uid), "role": "OWNER"}}}, terima)

    from app.main import app
    rm_by = {(getattr(r, "endpoint", None).__module__, r.endpoint.__name__): getattr(r, "response_model", None)
             for r in app.routes if getattr(r, "endpoint", None)}

    async def jalan(r):
        mod = kode_baru.get(r["modul"].split(".")[-1]) or importlib.import_module(r["modul"])
        fn = getattr(mod, r["nama"])
        kw = {}
        for n, p in inspect.signature(fn).parameters.items():
            ann = p.annotation
            if n == "request":
                kw[n] = req()
            elif isinstance(p.default, Depends):
                return "TIDAK TERJANGKAU ALAT", f"dependency {n}", False
            elif isinstance(ann, type) and issubclass(ann, BaseModel):
                try:
                    kw[n] = ann.model_validate(isi_medan(ann, n, ID))
                except Exception as e:  # noqa: BLE001
                    return "TIDAK TERJANGKAU ALAT", f"body {ann.__name__}: {str(e)[:60]}", False
            elif n in ID:
                if ID[n] is None:
                    return "TIDAK TERJANGKAU ALAT", f"tak ada subjek {n}", False
                kw[n] = uuid.UUID(str(ID[n])) if ann is uuid.UUID else str(ID[n])
            elif isinstance(p.default, Param):
                kw[n] = p.default.default
            elif p.default is not inspect._empty:
                kw[n] = p.default
            elif ann in (dict, typing.Dict) or ann is inspect._empty:
                kw[n] = {}
            else:
                return "TIDAK TERJANGKAU ALAT", f"parameter {n}:{ann}", False
        catatan_sql.clear()
        sp = raw.transaction(); await sp.start()
        try:
            out = await fn(**kw)
            rm = rm_by.get((r["modul"], r["nama"]))
            if rm is not None and not hasattr(out, "body"):
                TypeAdapter(rm).validate_python(out)
            kode, det = 200, ""
        except Exception as e:  # noqa: BLE001
            kode = getattr(e, "status_code", None) or type(e).__name__
            det = str(getattr(e, "detail", e))[:90]
        await sp.rollback()
        sentuh = any(TABEL.search(s) for s in catatan_sql)
        return kode, det, sentuh

    je0 = await raw.fetchval("SELECT count(*) FROM journal_entries")
    luar = raw.transaction(); await luar.start()
    A, B = {}, {}
    try:
        for r in rute:
            A[(r["modul"], r["nama"])] = await jalan(r)
        await raw.execute(open("/tmp/V247_badan.sql", encoding="utf-8").read())
        await raw.reload_schema_state()
        if hasattr(raw, "_drop_local_statement_cache"):
            raw._drop_local_statement_cache()
        for r in rute:
            B[(r["modul"], r["nama"])] = await jalan(r)
    finally:
        await luar.rollback()
    je1 = await raw.fetchval("SELECT count(*) FROM journal_entries")
    tipe = await raw.fetchval("SELECT string_agg(data_type, ',') FROM information_schema.columns WHERE column_name='customer_id' AND table_name IN ('credit_notes','customer_deposits')")
    await raw.close()

    def jenis(x):
        return (x[0], re.sub(r"[0-9a-f]{8}-[0-9a-f-]{27}", "<id>", x[1])[:60])

    terjangkau, beda, tak = [], [], []
    for r in rute:
        k = (r["modul"], r["nama"])
        a, b = A[k], B[k]
        if a[0] == "TIDAK TERJANGKAU ALAT" or not a[2]:
            tak.append((k, a, b))
            continue
        terjangkau.append(k)
        if jenis(a) != jenis(b) and k[1] != "merge_customers":
            beda.append((k, a, b))
    print(f"rute {len(rute)} · terjangkau & MENYENTUH tabel di A: {len(terjangkau)} · tak terjangkau alat / tak menyentuh: {len(tak)}")
    print("\n== TERJANGKAU (status A | B)")
    for k in terjangkau:
        a, b = A[k], B[k]
        tanda = "[H]" if (jenis(a) == jenis(b) or k[1] == "merge_customers") else "[X]"
        ex = "  (dikecualikan: rusak sebelum V247)" if k[1] == "merge_customers" else ""
        print(f"  {tanda} {k[0].split('.')[-1]}.{k[1]}: A={a[0]} {a[1][:50]} | B={b[0]} {b[1][:50]}{ex}")
    print("\n== TIDAK TERJANGKAU ALAT / TAK MENYENTUH TABEL DI A (bukan lulus)")
    for k, a, b in tak:
        print(f"  [-] {k[0].split('.')[-1]}.{k[1]}: {a[0]} {a[1][:70]} sentuh={a[2] if len(a) > 2 else '-'}")
    print(f"\nKONTROL nol menetap: jurnal {je0}->{je1} · tipe kembali={tipe}")
    print(f"BEDA A vs B (selain merge): {len(beda)} -> {'HIJAU' if not beda and 'uuid' not in tipe and je0 == je1 else 'MERAH'}")
    for k, a, b in beda:
        print(f"   BEDA {k}: A={a} B={b}")


asyncio.run(main())
