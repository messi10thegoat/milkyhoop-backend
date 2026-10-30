"""Harness NYATA FPB-DEBIT-SATU (kaos SAJA, penanda 'TES E2E FPB-DEBIT'): SATU transaksi luar pada satu koneksi SELALU di-ROLLBACK (pola F2 /
ND-BUAT: get_pool tertambal, transaksi dalam = savepoint). Sesudah ROLLBACK diukur di koneksi LAIN: nol sisa FPB/jurnal/stok.

Aturan (disetujui LANGSUNG pemilik 30 Okt 2026): akun debit baris FPB TANPA produk sama di kedua jalur posting = COGS_SALES.
FPB X1 dibuat LANGSUNG posted (create_bill_v2 status=posted) dan FPB X2 isi SAMA dibuat draf lalu post_bill. Baris: tanpa produk 30.021 +
barang stok 10.000. Harap: debit per akun X1 == X2; 30.021 di akun peran COGS_SALES; Persediaan hanya 10.000 (barang stok).
Gerbang menjalankan harness ini JUGA atas pohon DASAR (master) sebagai kontrol merah: di sana X1 mendebit Persediaan 40.021 -> GAGAL.
Kanal keluar di-stub: WAJIB 0. Argumen: <keluaran.json>."""
import asyncio
import json
import os
import sys
import uuid
from datetime import timedelta

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

from starlette.requests import Request  # noqa: E402

from app.routers import bills as RB  # noqa: E402
from app.schemas.bills import CreateBillRequestV2 as CB  # noqa: E402
from app.utils.tanggal_tenant import tanggal_dokumen  # noqa: E402

T = "kaos-biru-konveksi"
VENDOR, BARANG = "73a2b4bd-72d3-46cb-8adb-79f14f9e28e7", "862be8e3-b35a-48e3-b8c3-c200f75feba9"  # == harness ND-BUAT / AS-GP2
PENANDA = "TES E2E FPB-DEBIT"


def req(uid):
    async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
    r = Request({"type": "http", "method": "POST", "path": "/", "headers": [], "query_string": b""}, receive)
    r.state.user = {"tenant_id": T, "user_id": str(uid)}
    return r


async def main(keluar):
    c = await asyncpg.connect(os.environ["URL"])
    c2 = await asyncpg.connect(os.environ["URL"])
    assert await c.fetchval('SELECT 1 FROM "Tenant" WHERE id = $1', T), "tenant tak ada -- harness menolak"
    uid = await c.fetchval('SELECT id FROM "User" WHERE "tenantId" = $1 ORDER BY "createdAt" LIMIT 1', T)

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
    cek, catat = [], {}

    def harus(nama, kondisi, info=None):
        cek.append({"cek": nama, "ok": bool(kondisi), "info": info})
        print(("OK   " if kondisi else "GAGAL ") + nama + ("" if kondisi else f" -> {info}"), flush=True)

    async def saksi():
        return {"fpb": await c2.fetchval("SELECT count(*) FROM bills WHERE tenant_id = $1 AND notes = $2", T, PENANDA),
                "jurnal": await c2.fetchval("SELECT count(*) FROM journal_entries WHERE tenant_id = $1", T),
                "stok": await c2.fetchval("SELECT count(*) FROM inventory_ledger WHERE tenant_id = $1", T)}

    async def debit(bid):
        rows = await c.fetch("""SELECT ca.account_code, sum(jl.debit) d FROM bills b JOIN journal_lines jl ON jl.journal_id = b.journal_id
                                JOIN chart_of_accounts ca ON ca.id = jl.account_id WHERE b.id = $1 AND jl.debit > 0 GROUP BY 1 ORDER BY 1""", bid)
        return {r["account_code"]: str(r["d"]) for r in rows}

    sebelum = await saksi()
    luar = c.transaction()
    await luar.start()
    try:
        hari = await tanggal_dokumen(c, T)
        cogs = await c.fetchval("""SELECT ca.account_code FROM account_roles ar JOIN chart_of_accounts ca ON ca.id = ar.account_id
                                   WHERE ar.tenant_id = $1 AND ar.role_key = 'COGS_SALES'""", T)
        inv = await c.fetchval("""SELECT ca.account_code FROM account_roles ar JOIN chart_of_accounts ca ON ca.id = ar.account_id
                                  WHERE ar.tenant_id = $1 AND ar.role_key = 'INVENTORY_MERCHANDISE'""", T)
        stok = await c.fetchval("SELECT item_type = 'goods' AND track_inventory FROM products WHERE id = $1", uuid.UUID(BARANG))
        harus("prasyarat: peran COGS_SALES + INVENTORY_MERCHANDISE terpetakan, BARANG = barang stok", cogs and inv and stok, [cogs, inv, stok])
        catat["akun"] = {"COGS_SALES": cogs, "INVENTORY_MERCHANDISE": inv}
        isi = [{"product_name": f"{PENANDA} ongkos jahit", "qty": 1, "price": 30021},
               {"product_id": BARANG, "product_name": "TES E2E kain", "qty": 1, "price": 10000}]
        hasil = {}
        for jalur in ("langsung_posted", "draf_lalu_post"):
            nomor = f"TES-E2E-FPBD-{uuid.uuid4().hex[:6]}"
            sp = c.transaction()
            await sp.start()
            await RB.create_bill_v2(req(uid), CB(vendor_id=VENDOR, invoice_number=nomor, due_date=hari + timedelta(days=30), notes=PENANDA,
                                                 items=isi, status="posted" if jalur == "langsung_posted" else "draft"))
            bid = await c.fetchval("SELECT id FROM bills WHERE tenant_id = $1 AND invoice_number = $2", T, nomor)
            if jalur == "draf_lalu_post":
                await RB.post_bill(req(uid), bid)
            hasil[jalur] = await debit(bid)
            await sp.rollback()
        catat["debit"] = hasil
        a, b = hasil["langsung_posted"], hasil["draf_lalu_post"]
        harus("debit per akun: buat-langsung-posted == draf-lalu-post", a == b, hasil)
        harus("baris tanpa produk 30.021 di COGS_SALES pada KEDUA jalur", a.get(cogs) == "30021.00" and b.get(cogs) == "30021.00", hasil)
        harus("Persediaan hanya barang stok 10.000 pada KEDUA jalur", a.get(inv) == "10000.00" and b.get(inv) == "10000.00", hasil)
    finally:
        await luar.rollback()
    sesudah = await saksi()
    harus("ROLLBACK: dari koneksi LAIN nol sisa FPB TES E2E, jumlah jurnal & stok tak berubah", sesudah == sebelum, [sebelum, sesudah])
    harus("nol panggilan kanal keluar", KELUAR == [], KELUAR)
    await c.close()
    await c2.close()
    gagal = [x["cek"] for x in cek if not x["ok"]]
    json.dump({"cek": cek, "catat": catat, "gagal": gagal}, open(keluar, "w"), default=str, indent=1)
    print("HASIL", "LULUS" if not gagal else f"GAGAL {gagal}", f"({len(cek)} cek)")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
