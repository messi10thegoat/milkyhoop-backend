"""GERBANG validasi pelanggan di tiga penulis varchar. Label lama|baru, data sama, ROLLBACK, harapan dari STRUKTUR.

Skenario x penulis (buat CN, buat DP, ubah draf CN):
  NAMA            "Toko Melati"                 lama: tersimpan apa adanya (buat) / 500 (ubah)   baru: 400
  TENANT_LAIN     UUID pelanggan grapgrap       lama: tersimpan (buat) / 500 (ubah)               baru: 400
  SAH_HURUF_BESAR UUID pelanggan sendiri UPPER  lama: tersimpan UPPER (buat) / 500 (ubah)         baru: 201/200, tersimpan huruf kecil
  KOSONG          None                          lama & baru: 201/200, NULL (perilaku opsional dipertahankan)
Pesan 400 NAMA vs TENANT_LAIN tak dibandingkan (nama bukan UUID -> pesan format), tapi TENANT_LAIN vs UUID karangan
harus SAMA ("Pelanggan tidak ditemukan") -> tak membocorkan keberadaan lintas tenant (baru saja).
Cacah harapan dihitung dari daftar skenario, bukan angka ketik.
"""
import asyncio
import importlib.util
import os
import sys
import uuid
from datetime import date

sys.path.insert(0, "/app/backend/api_gateway")
import asyncpg  # noqa: E402
from starlette.requests import Request  # noqa: E402

CN_PATH, DP_PATH, LABEL = sys.argv[1], sys.argv[2], sys.argv[3]
T, B = "kaos-biru-konveksi", "grapgrap-manado"
hasil = []


def catat(s, u, ok, k=""):
    hasil.append((s, u, bool(ok), str(k)[:260]))


def muat(nama, path):
    spec = importlib.util.spec_from_file_location(nama, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[nama] = m
    spec.loader.exec_module(m)
    return m


def baca_id(r):
    for k in (r, getattr(r, "data", None), (r.get("data") if isinstance(r, dict) else None)):
        if isinstance(k, dict) and k.get("id"):
            return k["id"]
        if k is not None and getattr(k, "id", None):
            return k.id
    raise RuntimeError(f"ALAT: id tak terbaca dari {type(r).__name__}")


async def main():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
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
    if LABEL == "baru":
        muat("app.services.pihak_helpers", "/tmp/pihak_helpers_baru.py")
    cn = muat("app.routers.credit_notes_uji", CN_PATH)
    dp = muat("app.routers.customer_deposits_uji", DP_PATH)
    import app.schemas.credit_notes as scn
    import app.schemas.customer_deposits as sdp
    Item = scn.CreateCreditNoteRequest.model_fields["items"].annotation.__args__[0]

    uid = await conn.fetchval("SELECT created_by FROM bank_transactions WHERE tenant_id=$1 AND created_by IS NOT NULL LIMIT 1", T)
    sendiri = await conn.fetchval("SELECT id FROM customers WHERE tenant_id=$1 ORDER BY id LIMIT 1", T)
    lain = await conn.fetchval("SELECT id FROM customers WHERE tenant_id=$1 ORDER BY id LIMIT 1", B)
    akun = await conn.fetchval("SELECT coa_id FROM bank_accounts WHERE tenant_id=$1 AND is_active ORDER BY account_name LIMIT 1", T)
    req = Request({"type": "http", "method": "POST", "path": "/", "headers": [], "query_string": b"",
                   "state": {"user": {"tenant_id": T, "user_id": str(uid)}}})
    cacah0 = (await conn.fetchval("SELECT count(*) FROM credit_notes"), await conn.fetchval("SELECT count(*) FROM customer_deposits"))

    SKENARIO = [
        ("NAMA", "Toko Melati"),
        ("TENANT_LAIN", str(lain)),
        ("KARANGAN", str(uuid.uuid4())),
        ("SAH_HURUF_BESAR", str(sendiri).upper()),
        ("KOSONG", None),
    ]

    async def buat_cn(nilai):
        body = scn.CreateCreditNoteRequest(customer_id=nilai, customer_name="Uji Gerbang", credit_note_date=date.today(),
                                          reason="other", items=[Item(description="uji", quantity=1, unit_price=1000)])
        return await cn.create_credit_note(req, body), "credit_notes"

    async def buat_dp(nilai):
        body = sdp.CreateCustomerDepositRequest(customer_id=nilai, customer_name="Uji Gerbang", amount=1000,
                                               deposit_date=date.today(), payment_method="transfer", account_id=str(akun))
        return await dp.create_customer_deposit(req, body), "customer_deposits"

    async def ubah_cn(nilai):
        r, _ = await buat_cn(None)
        cid = baca_id(r)
        await cn.update_credit_note(req, uuid.UUID(str(cid)), scn.UpdateCreditNoteRequest(customer_id=nilai))
        return {"data": {"id": cid}}, "credit_notes"

    PENULIS = [("buat CN", buat_cn), ("buat DP", buat_dp), ("ubah draf CN", ubah_cn)]
    jawaban = {}
    luar = conn.transaction(); await luar.start()
    try:
        for pnama, fn in PENULIS:
            for snama, nilai in SKENARIO:
                sp = conn.transaction(); await sp.start()
                tersimpan, kode, det = "?", None, ""
                try:
                    r, tabel = await fn(nilai)
                    kode = 200
                    tersimpan = await conn.fetchval(f"SELECT customer_id FROM {tabel} WHERE id=$1", uuid.UUID(str(baca_id(r))))
                except Exception as e:  # noqa: BLE001
                    kode, det = getattr(e, "status_code", type(e).__name__), getattr(e, "detail", str(e))
                await sp.rollback()
                jawaban[(pnama, snama)] = (kode, det)
                label = f"{pnama} / {snama}"
                if LABEL == "lama":
                    if snama == "KOSONG":
                        ok = kode == 200 and tersimpan is None
                    elif pnama == "ubah draf CN":
                        ok = kode == 500  # UUID(value) ke varchar / ValueError
                    elif snama == "SAH_HURUF_BESAR":
                        ok = kode == 200 and tersimpan == nilai  # disimpan UPPER apa adanya
                    else:
                        ok = kode == 200 and tersimpan == nilai  # sampah/tenant lain DITERIMA
                    catat("MERAH lama" if snama != "KOSONG" else "KONTROL", label, ok, f"{kode} tersimpan={tersimpan} {det}")
                else:
                    if snama == "KOSONG":
                        ok = kode == 200 and tersimpan is None
                        catat("KONTROL", label + " -> 200, NULL (opsional dipertahankan)", ok, f"{kode} {tersimpan} {det}")
                    elif snama == "SAH_HURUF_BESAR":
                        ok = kode == 200 and tersimpan == str(sendiri).lower()
                        catat("HIJAU baru", label + " -> 200, tersimpan UUID huruf kecil", ok, f"{kode} {tersimpan} {det}")
                    else:
                        ok = kode == 400
                        catat("TOLAK baru", label + " -> 400", ok, f"{kode} {det}")
        if LABEL == "baru":
            for pnama, _ in PENULIS:
                a, b = jawaban[(pnama, "TENANT_LAIN")], jawaban[(pnama, "KARANGAN")]
                catat("KEBERADAAN", f"{pnama}: tenant lain vs karangan -> jawaban SAMA", a == b, f"{a} | {b}")
    finally:
        await luar.rollback()
    cacah1 = (await conn.fetchval("SELECT count(*) FROM credit_notes"), await conn.fetchval("SELECT count(*) FROM customer_deposits"))
    catat("KONTROL", "nol menetap (credit_notes, customer_deposits)", cacah0 == cacah1, f"{cacah0}->{cacah1}")
    await conn.close()
    harap = len(PENULIS) * len(SKENARIO) + (len(PENULIS) if LABEL == "baru" else 0) + 1
    for s, u, ok, k in hasil:
        print(("[H] " if ok else "[X] ") + f"{s:10} {u}  | {k}")
    g = sum(1 for h in hasil if not h[2])
    print(f"\n[{LABEL}] gagal={g} total={len(hasil)} harap={harap} (dari struktur) -> {'LENGKAP' if len(hasil) == harap else 'TAK SAH'}")
    sys.exit(0 if g == 0 and len(hasil) == harap else 1)


asyncio.run(main())
