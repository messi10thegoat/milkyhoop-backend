"""Harness NYATA F2 (kaos SAJA, penanda 'TES E2E F2'): SATU transaksi luar pada satu koneksi SELALU di-ROLLBACK (pola ND-BUAT / B1b-2: pool
tertambal, transaksi dalam = savepoint). Sesudah ROLLBACK diukur di koneksi LAIN: nol sisa.

Aturan F2: faktur pembelian TAK BOLEH dibatalkan selama punya ND tertaut original_bill_id yang bukan draf/void (blok ADA_NOTA_DEBIT).
Per status ND (draft/posted/partial/applied/void) di savepoint sendiri, atas SATU FPB posted kaos yang dasarnya tak terblok:
  penentu (TA.blok_void) + pratinjau (TA.rencana_void) + TULIS (BillsService.void_bill) harus sepakat: blok untuk posted/partial/applied,
  lolos untuk draft/void (void sungguhan terjadi di savepoint, lalu di-rollback).
Kontrol: (a) kueri LAMA (aplikasi saja) atas ND posted tertaut = 0 baris (celah nyata, penjaga bisa merah); (b) ND posted tertaut ke FPB LAIN
tak memblok FPB ini. Semua kanal keluar di-stub: WAJIB 0 panggilan. Argumen: <keluaran.json>."""
import asyncio
import json
import os
import sys
import uuid

import asyncpg

sys.path.insert(0, os.getcwd())
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

from app.services import bills_service as BS  # noqa: E402
from app.services import tagihan_aksi as TA  # noqa: E402

T = "kaos-biru-konveksi"
PENANDA = "TES E2E F2"
KUERI_LAMA = """SELECT vc.credit_number FROM vendor_credit_applications vca JOIN vendor_credits vc ON vc.id = vca.vendor_credit_id AND vc.tenant_id = $2
           WHERE vca.tenant_id = $2 AND vca.bill_id = $1 AND vca.status = 'active' ORDER BY vc.credit_number"""
HARAP_BLOK = {"draft": False, "posted": True, "partial": True, "applied": True, "void": False}


async def main(keluar):
    c = await asyncpg.connect(os.environ["URL"])
    c2 = await asyncpg.connect(os.environ["URL"])  # koneksi LAIN: saksi sisa
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

    svc = BS.BillsService(Pool())
    cek, catat = [], {}

    def harus(nama, kondisi, info=None):
        cek.append({"cek": nama, "ok": bool(kondisi), "info": info})
        print(("OK   " if kondisi else "GAGAL ") + nama + ("" if kondisi else f" -> {info}"), flush=True)

    async def saksi():
        return {"nd_tes": await c2.fetchval("SELECT count(*) FROM vendor_credits WHERE tenant_id = $1 AND notes = $2", T, PENANDA),
                "jurnal": await c2.fetchval("SELECT count(*) FROM journal_entries WHERE tenant_id = $1", T),
                "bill_void": await c2.fetchval("SELECT count(*) FROM bills WHERE tenant_id = $1 AND status_v2 = 'void'", T)}

    sebelum = await saksi()
    catat["sebelum"] = sebelum
    luar = c.transaction()
    await luar.start()
    try:
        # FPB posted kaos yang dasarnya tak terblok apa pun (blok lain akan menyamarkan hasil)
        fpb = None
        for b in await c.fetch("""SELECT id, invoice_number, status, status_v2, vendor_id, vendor_name FROM bills
                                  WHERE tenant_id = $1 AND status_v2 = 'posted' ORDER BY created_at DESC LIMIT 30""", T):
            if await TA.blok_void(c, T, b) == []:
                fpb = b
                break
        harus("ada FPB posted kaos yang dasarnya tak terblok", fpb is not None)
        lain = await c.fetchrow("""SELECT id, invoice_number FROM bills WHERE tenant_id = $1 AND status_v2 = 'posted' AND id <> $2
                                   ORDER BY created_at DESC LIMIT 1""", T, fpb["id"])
        catat["fpb"], catat["fpb_lain"] = fpb["invoice_number"], lain["invoice_number"]

        async def sisip(status, bill_id, nomor):
            await c.execute("""INSERT INTO vendor_credits (tenant_id, credit_number, vendor_id, vendor_name, original_bill_id, subtotal,
                                   total_amount, credit_date, reason, status, notes, created_by)
                               VALUES ($1, $2, $3, $4, $5, 1000, 1000, CURRENT_DATE, 'return', $6, $7, $8)""",
                            T, nomor, fpb["vendor_id"], fpb["vendor_name"] or "-", bill_id, status, PENANDA, uid)

        # kontrol (b): ND posted tertaut ke FPB LAIN tak memblok FPB ini
        sp = c.transaction()
        await sp.start()
        await sisip("posted", lain["id"], "TES-F2-LAIN")
        b = await TA.blok_void(c, T, fpb)
        harus("kontrol: ND posted tertaut FPB LAIN tak memblok FPB ini", b == [], b)
        await sp.rollback()

        for st, blokir in HARAP_BLOK.items():
            sp = c.transaction()
            await sp.start()
            nomor = f"TES-F2-{st.upper()}"
            await sisip(st, fpb["id"], nomor)
            if st == "posted":  # kontrol (a): celah nyata -- kueri lama buta terhadap ND tertaut
                lama = await c.fetch(KUERI_LAMA, fpb["id"], T)
                harus("kontrol: kueri LAMA (aplikasi saja) atas ND posted tertaut = 0 baris (celah nyata)", lama == [], [dict(r) for r in lama])
            b = await TA.blok_void(c, T, fpb)
            kode = [x["code"] for x in b]
            pv = await TA.rencana_void(c, T, fpb["id"])
            kode_pv = [x["code"] for x in pv["blocks"]]
            r = await svc.void_bill(tenant_id=T, bill_id=fpb["id"], request={"reason": PENANDA}, user_id=uid)
            st_bill = await c.fetchval("SELECT status_v2 FROM bills WHERE id = $1", fpb["id"])
            catat[st] = {"blok": kode, "pratinjau": kode_pv, "tulis": {"success": r.get("success"), "message": r.get("message")},
                         "status_bill": st_bill}
            if blokir:
                pesan = b[0]["message"] if b else ""
                harus(f"ND {st} tertaut: penentu ADA_NOTA_DEBIT sebut {nomor} + 'Batalkan nota debit itu dulu'",
                      kode == ["ADA_NOTA_DEBIT"] and nomor in pesan and "Batalkan nota debit itu dulu" in pesan, b)
                harus(f"ND {st} tertaut: pratinjau == penentu", kode_pv == kode, kode_pv)
                harus(f"ND {st} tertaut: TULIS ditolak dgn pesan yang sama, FPB tetap posted",
                      r.get("success") is False and r.get("message") == pesan and st_bill == "posted", catat[st])
            else:
                harus(f"ND {st} tertaut: tak memblok (penentu + pratinjau kosong)", kode == [] and kode_pv == [], [kode, kode_pv])
                harus(f"ND {st} tertaut: TULIS void lolos (di savepoint, lalu rollback)", r.get("success") is True and st_bill == "void",
                      catat[st])
            await sp.rollback()
    finally:
        await luar.rollback()
    sesudah = await saksi()
    catat["sesudah"] = sesudah
    harus("ROLLBACK: dari koneksi LAIN nol sisa ND TES E2E F2, jumlah jurnal & FPB void tak berubah", sesudah == sebelum, [sebelum, sesudah])
    harus("nol panggilan kanal keluar (surel/WA/HTTP)", KELUAR == [], KELUAR)
    await c.close()
    await c2.close()
    gagal = [x["cek"] for x in cek if not x["ok"]]
    json.dump({"cek": cek, "catat": catat, "gagal": gagal}, open(keluar, "w"), default=str, indent=1)
    print("HASIL", "LULUS" if not gagal else f"GAGAL {gagal}", f"({len(cek)} cek)")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
