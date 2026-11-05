"""Harness NYATA faktor konversi DESIMAL (kaos SAJA, penanda 'TES E2E FAKTOR-DESIMAL'): SATU transaksi luar SELALU di-ROLLBACK
(pool tertambal). Fixture DI DALAM rollback lewat RUTE layar satuan (validator nyata): barang stok kaos + konversi Sak = 2,5 x satuan
dasar; faktor 10001 / 7 desimal DITOLAK rute. Tagihan langsung terbit 3 Sak @ Rp 100.000 tanpa pajak -> kartu stok +7,5 dasar,
total_cost 300.000, unit_cost 40.000, transaction_quantity 3, conversion_factor 2,5. Sesudah ROLLBACK (koneksi LAIN): nol sisa.
Pohon DASAR (faktor int) WAJIB GAGAL (rute menolak 2,5 / skema int). Argumen: <keluaran.json>."""
import asyncio
import json
import os
import sys
import uuid
from datetime import timedelta
from decimal import Decimal

import asyncpg

sys.path.insert(0, os.getcwd())
sys.path.insert(0, "/app")
os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-harness")

KELUAR = []


def _pasang_pagar_keluar():
    import smtplib

    def catat(nama):
        def f(*a, **k):
            KELUAR.append(nama)
            raise RuntimeError(f"harness: kanal keluar {nama} diblokir")
        return f

    async def acatat(*a, **k):
        KELUAR.append("async-http")
        raise RuntimeError("harness: kanal keluar async-http diblokir")
    smtplib.SMTP.__init__ = catat("smtp")
    smtplib.SMTP_SSL.__init__ = catat("smtp_ssl")
    for modul, kelas, attr, f in (("httpx", "Client", "send", catat("httpx")), ("httpx", "AsyncClient", "send", acatat),
                                  ("requests", "Session", "send", catat("requests")), ("aiohttp", "ClientSession", "_request", acatat)):
        try:
            setattr(getattr(__import__(modul), kelas), attr, f)
        except ImportError:
            pass


_pasang_pagar_keluar()

from fastapi import HTTPException  # noqa: E402
from starlette.requests import Request  # noqa: E402

from app.routers import bills as RB  # noqa: E402
from app.routers import units as RU  # noqa: E402
from app.schemas.bills import CreateBillRequestV2 as CB  # noqa: E402
from app.services import db_pool  # noqa: E402
from app.services import policy_engine_client as PEC  # noqa: E402
from app.utils.tanggal_tenant import tanggal_dokumen  # noqa: E402

T = "kaos-biru-konveksi"
VENDOR, STOK = "73a2b4bd-72d3-46cb-8adb-79f14f9e28e7", "862be8e3-b35a-48e3-b8c3-c200f75feba9"  # == harness c-2
PENANDA = "TES E2E FAKTOR-DESIMAL"
D = Decimal


def req(uid, badan=None, role="OWNER"):
    data = json.dumps(badan or {}).encode()

    async def receive(): return {"type": "http.request", "body": data, "more_body": False}
    r = Request({"type": "http", "method": "POST", "path": "/", "headers": [(b"content-type", b"application/json")], "query_string": b""},
                receive)
    r.state.user = {"tenant_id": T, "user_id": str(uid), "role": role}
    return r


async def main(keluar):
    c = await asyncpg.connect(os.environ["URL"])
    c2 = await asyncpg.connect(os.environ["URL"])
    assert await c.fetchval('SELECT 1 FROM "Tenant" WHERE id = $1', T), "tenant tak ada -- harness menolak"
    uid = await c.fetchval("""SELECT utr.user_id FROM user_tenant_roles utr JOIN roles r ON r.id = utr.role_id
                              WHERE utr.tenant_id = $1 AND r.code = 'OWNER' LIMIT 1""", T)

    class Kon:
        def __getattr__(s, a): return getattr(c, a)
        def transaction(s, **kw): return c.transaction()

    class CM:
        async def __aenter__(s): return Kon()
        async def __aexit__(s, *a): return False

    class Pool:
        def acquire(s): return CM()

    async def gp(): return Pool()
    RB.get_pool = gp
    RU.get_pool = gp
    db_pool.get_db_pool = gp
    PEC.init_policy_engine(Pool())
    cek, catat = [], {}

    def harus(nama, kondisi, info=None):
        cek.append({"cek": nama, "ok": bool(kondisi), "info": info})
        print(("OK   " if kondisi else "GAGAL ") + nama + ("" if kondisi else f" -> {info}"), flush=True)

    async def saksi():
        return {"tagihan": await c2.fetchval("SELECT count(*) FROM bills WHERE tenant_id = $1 AND notes = $2", T, PENANDA),
                "konversi": await c2.fetchval("SELECT count(*) FROM unit_conversions WHERE tenant_id = $1", T),
                "jurnal": await c2.fetchval("SELECT count(*) FROM journal_entries WHERE tenant_id = $1", T),
                "stok": await c2.fetchval("SELECT count(*) FROM inventory_ledger WHERE tenant_id = $1", T)}

    sebelum = await saksi()
    luar = c.transaction()
    await luar.start()
    try:
        dasar = await c.fetchval("SELECT COALESCE(NULLIF(base_unit, ''), satuan) FROM products WHERE id = $1 AND tenant_id = $2", STOK, T)
        catat["dasar"] = dasar
        harus("prasyarat: barang stok kaos punya satuan dasar", bool(dasar), dasar)
        for salah in (10001, 1.1234567, 0):
            try:
                await RU.set_conversion(req(uid, {"from_unit": dasar, "to_unit": "sak", "factor": salah}), STOK)
                harus(f"rute satuan menolak faktor {salah}", False, "diterima")
            except HTTPException as e:
                harus(f"rute satuan menolak faktor {salah}", e.status_code == 400, e.detail)
        await RU.set_conversion(req(uid, {"from_unit": dasar, "to_unit": "sak", "factor": 2.5}), STOK)
        f = await c.fetchval("SELECT conversion_factor FROM unit_conversions WHERE tenant_id = $1 AND product_id = $2 AND lower(conversion_unit) = 'sak' AND is_active",
                             T, STOK)
        harus("rute satuan menyimpan faktor 2,5", f == D("2.5"), f)
        g = await RU.get_conversions(req(uid), STOK)
        fs = [x.get("factor") for x in g.get("conversions", [])] if isinstance(g, dict) else []
        catat["baca"] = fs
        harus("baca konversi: faktor 2.5 angka (float)", 2.5 in fs and all(isinstance(x, float) for x in fs), fs)
        hari = await tanggal_dokumen(c, T)
        nomor = f"TES-E2E-FD-{uuid.uuid4().hex[:6]}"
        await RB.create_bill_v2(req(uid), CB(vendor_id=VENDOR, invoice_number=nomor, due_date=hari + timedelta(days=30), notes=PENANDA,
                                             tax_rate=0, status="posted",
                                             items=[{"product_id": STOK, "product_name": "TES E2E SAK", "qty": 3, "price": 100000, "unit": "sak"}]))
        b = await c.fetchrow("SELECT id, grand_total FROM bills WHERE tenant_id = $1 AND invoice_number = $2", T, nomor)
        k = await c.fetchrow("""SELECT quantity_in, total_cost, unit_cost, transaction_quantity, conversion_factor, transaction_unit
                                FROM inventory_ledger WHERE tenant_id = $1 AND source_id = $2 AND movement_type = 'PURCHASE'""", T, b["id"])
        catat["kartu"] = {x: str(k[x]) for x in k.keys()} if k else None
        harus("tagihan 3 Sak @ 100.000 = 300.000", b["grand_total"] == D("300000"), b["grand_total"])
        harus("kartu: +7,5 dasar, total 300.000, @40.000, transaksi 3 Sak, faktor 2,5",
              k is not None and k["quantity_in"] == D("7.5") and k["total_cost"] == D("300000") and D(str(k["unit_cost"])) == D("40000")
              and k["transaction_quantity"] == D("3") and k["conversion_factor"] == D("2.5"), catat["kartu"])
    finally:
        await luar.rollback()
    sesudah = await saksi()
    harus("ROLLBACK: dari koneksi LAIN nol sisa tagihan/konversi/jurnal/stok", sesudah == sebelum, [sebelum, sesudah])
    harus("nol panggilan kanal keluar", KELUAR == [], KELUAR)
    await c.close()
    await c2.close()
    gagal = [x["cek"] for x in cek if not x["ok"]]
    json.dump({"cek": cek, "catat": catat, "gagal": gagal}, open(keluar, "w"), default=str, indent=1)
    print("HASIL", "LULUS" if not gagal else f"GAGAL {gagal}", f"({len(cek)} cek)")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
