"""Harness NYATA FPB-DEBIT-PENUH + RECLASS-COGS-CLASS (kaos SAJA, penanda 'TES E2E FPB-PENUH'): SATU transaksi luar pada satu koneksi SELALU
di-ROLLBACK (pola F2 / FPB-DEBIT-SATU: pool tertambal, transaksi dalam = savepoint; PolicyEngine NYATA). Sesudah ROLLBACK diukur di koneksi
LAIN: nol sisa FPB / jurnal / stok / produk berubah.

Bagian 1 (aturan disetujui LANGSUNG pemilik): FPB isi SAMA -- barang NON-STOK 20.000 + JASA 7.000 + barang STOK 10.000 + tanpa produk 3.000
-- dibuat LANGSUNG posted vs draf lalu post_bill: debit per akun HARUS identik; non-stok + tanpa produk -> COGS_SALES, jasa -> COGS_SERVICE,
stok -> Persediaan. (Fixture di dalam ROLLBACK: satu produk non_inventory kaos diubah item_type='service' -- kaos tak punya produk jasa.)
Bagian 2 (koreksi riwayat, endpoint reclassify-bill-cogs-class): FPB X (non-stok 20.000 + jasa 7.000) diposting benar, lalu FIXTURE riwayat
salah kelas = jurnal RECLASSIFY_BILL_INVENTORY gaya lama "Reklas {nomor} ..." (Dr COGS_SERVICE / Cr COGS_SALES 20.000; Dr COGS_SALES /
Cr COGS_SERVICE 7.000). Harap: dry_run calon X saja (FPB benar lain BUKAN calon), pindah COGS_SERVICE->COGS_SALES 13.000 (bersih) ->
post -> GL akun kelas == harapan -> dry_run 0 calon -> post ulang 409 SUDAH_DIREKLAS -> reverse lewat POST /journals/{id}/reverse ->
GL kembali + calon muncul lagi. Gerbang juga menjalankan harness ini di pohon DASAR (kontrol merah: Bagian 1 beda, endpoint tak ada).
Kanal keluar di-stub: WAJIB 0. Argumen: <keluaran.json>."""
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
from app.routers import journals as RJ  # noqa: E402
from app.schemas.bills import CreateBillRequestV2 as CB  # noqa: E402
from app.services import db_pool  # noqa: E402
from app.services import policy_engine_client as PEC  # noqa: E402
from app.utils.tanggal_tenant import tanggal_dokumen  # noqa: E402

T = "kaos-biru-konveksi"
VENDOR, STOK = "73a2b4bd-72d3-46cb-8adb-79f14f9e28e7", "862be8e3-b35a-48e3-b8c3-c200f75feba9"  # == harness ND-BUAT / FPB-DEBIT-SATU
PENANDA = "TES E2E FPB-PENUH"
D = Decimal


def req(uid, role="OWNER"):
    async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
    r = Request({"type": "http", "method": "POST", "path": "/", "headers": [], "query_string": b""}, receive)
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
    RJ.get_pool = gp
    db_pool.get_db_pool = gp
    PEC.init_policy_engine(Pool())
    try:
        from backend.api_gateway.app.services import policy_engine_client as PEC_MW
        PEC_MW.init_policy_engine(Pool())
    except Exception:  # noqa: BLE001
        pass
    cek, catat = [], {}

    def harus(nama, kondisi, info=None):
        cek.append({"cek": nama, "ok": bool(kondisi), "info": info})
        print(("OK   " if kondisi else "GAGAL ") + nama + ("" if kondisi else f" -> {info}"), flush=True)

    async def saksi():
        return {"fpb": await c2.fetchval("SELECT count(*) FROM bills WHERE tenant_id = $1 AND notes = $2", T, PENANDA),
                "jurnal": await c2.fetchval("SELECT count(*) FROM journal_entries WHERE tenant_id = $1", T),
                "stok": await c2.fetchval("SELECT count(*) FROM inventory_ledger WHERE tenant_id = $1", T),
                "jasa": await c2.fetchval("SELECT count(*) FROM products WHERE tenant_id = $1 AND item_type = 'service'", T)}

    async def peran(k):
        return await c.fetchrow("""SELECT ca.id, ca.account_code FROM account_roles ar JOIN chart_of_accounts ca ON ca.id = ar.account_id
                                   WHERE ar.tenant_id = $1 AND ar.role_key = $2""", T, k)

    async def debit(bid):
        rows = await c.fetch("""SELECT ca.account_code, sum(jl.debit) d FROM bills b JOIN journal_lines jl ON jl.journal_id = b.journal_id
                                JOIN chart_of_accounts ca ON ca.id = jl.account_id WHERE b.id = $1 AND jl.debit > 0 GROUP BY 1 ORDER BY 1""", bid)
        return {r["account_code"]: str(r["d"]) for r in rows}

    async def gl(*akun):
        rows = await c.fetch("""SELECT ca.account_code, COALESCE(sum(jl.debit - jl.credit), 0) n FROM journal_lines jl
                                JOIN journal_entries je ON je.id = jl.journal_id AND je.tenant_id = $1 AND je.status = 'POSTED'
                                JOIN chart_of_accounts ca ON ca.id = jl.account_id WHERE ca.id = ANY($2::uuid[]) GROUP BY 1""", T, list(akun))
        return {r["account_code"]: D(str(r["n"])) for r in rows}

    async def buat(isi, status):
        nomor = f"TES-E2E-FPBP-{uuid.uuid4().hex[:6]}"
        await RB.create_bill_v2(req(uid), CB(vendor_id=VENDOR, invoice_number=nomor, due_date=hari + timedelta(days=30), notes=PENANDA,
                                             items=isi, status=status))
        bid = await c.fetchval("SELECT id FROM bills WHERE tenant_id = $1 AND invoice_number = $2", T, nomor)
        if status == "draft":
            await RB.post_bill(req(uid), bid)
        return bid, nomor

    async def reklas(dry):
        return (await RJ.reclassify_bill_cogs_class_endpoint(req(uid), RJ.ReclassifyBillCogsClassRequest(dry_run=dry, reason=PENANDA)))["data"]

    sebelum = await saksi()
    luar = c.transaction()
    await luar.start()
    try:
        hari = await tanggal_dokumen(c, T)
        S, J, I = await peran("COGS_SALES"), await peran("COGS_SERVICE"), await peran("INVENTORY_MERCHANDISE")
        nonstok = await c.fetchval("""SELECT id FROM products WHERE tenant_id = $1 AND item_type = 'non_inventory' AND purchase_account_id IS NULL
                                      ORDER BY created_at LIMIT 1""", T)
        jasa = await c.fetchval("""SELECT id FROM products WHERE tenant_id = $1 AND item_type = 'non_inventory' AND purchase_account_id IS NULL
                                   AND id <> $2 ORDER BY created_at LIMIT 1""", T, nonstok)
        harus("prasyarat: peran S/J/I terpetakan + 2 produk non-stok tanpa purchase_account_id", S and J and I and nonstok and jasa,
              [bool(S), bool(J), bool(I), str(nonstok), str(jasa)])
        await c.execute("UPDATE products SET item_type = 'service', bisa_dikirim = false WHERE id = $1 AND tenant_id = $2", jasa, T)  # fixture (di dalam ROLLBACK)
        catat["akun"] = {"S": S["account_code"], "J": J["account_code"], "I": I["account_code"]}
        isi = [{"product_id": str(nonstok), "product_name": "TES E2E non-stok", "qty": 1, "price": 20000},
               {"product_id": str(jasa), "product_name": "TES E2E jasa", "qty": 1, "price": 7000},
               {"product_id": STOK, "product_name": "TES E2E kain", "qty": 1, "price": 10000},
               {"product_name": f"{PENANDA} ongkos", "qty": 1, "price": 3000}]
        # ---------------- Bagian 1
        hasil = {}
        for jalur, st in (("langsung_posted", "posted"), ("draf_lalu_post", "draft")):
            sp = c.transaction()
            await sp.start()
            bid, _ = await buat(isi, st)
            hasil[jalur] = await debit(bid)
            await sp.rollback()
        catat["bagian1"] = hasil
        harap = {S["account_code"]: "23000.00", J["account_code"]: "7000.00", I["account_code"]: "10000.00"}
        harus("B1: debit per akun buat-langsung-posted == draf-lalu-post", hasil["langsung_posted"] == hasil["draf_lalu_post"], hasil)
        harus("B1: non-stok + tanpa produk -> COGS_SALES 23.000, jasa -> COGS_SERVICE 7.000, stok -> Persediaan 10.000 (kedua jalur)",
              hasil["langsung_posted"] == harap and hasil["draf_lalu_post"] == harap, [harap, hasil])
        # ---------------- Bagian 2
        d0 = await reklas(True)
        calon0 = {m["invoice_number"] for m in d0["moves"]}
        bx, nx = await buat(isi[:2], "draft")            # FPB X benar: S 20.000 / J 7.000
        by, ny = await buat(isi[:2], "posted")           # FPB Y benar (kontrol: BUKAN calon)
        fx = await c.fetchval("""INSERT INTO journal_entries (tenant_id, journal_number, journal_date, description, source_type,
                                 total_debit, total_credit, status) VALUES ($1, $2, $3, $4, 'RECLASSIFY_BILL_INVENTORY', 27000, 27000, 'DRAFT')
                                 RETURNING id""", T, f"TES-FX-{uuid.uuid4().hex[:6]}", hari, f"{PENANDA} fixture riwayat salah kelas")
        for i, (a, d_, k, m) in enumerate(((J["id"], 20000, 0, f"Reklas {nx} salah"), (S["id"], 0, 20000, f"Reklas {nx} salah"),
                                           (S["id"], 7000, 0, f"Reklas {nx} salah"), (J["id"], 0, 7000, f"Reklas {nx} salah")), 1):
            await c.execute("INSERT INTO journal_lines (journal_id, line_number, account_id, memo, debit, credit) VALUES ($1,$2,$3,$4,$5,$6)",
                            fx, i, a, m, d_, k)
        await c.execute("UPDATE journal_entries SET status = 'POSTED' WHERE id = $1", fx)
        gl0 = await gl(S["id"], J["id"])
        n_j0 = await c.fetchval("SELECT count(*) FROM journal_entries WHERE tenant_id = $1", T)
        d1 = await reklas(True)
        n_j1 = await c.fetchval("SELECT count(*) FROM journal_entries WHERE tenant_id = $1", T)
        baru = [m for m in d1["moves"] if m["invoice_number"] not in calon0]
        catat["dry_run"] = d1
        harus("B2: dry_run memuat X SAJA (Y benar bukan calon)", [m["invoice_number"] for m in baru] == [nx], d1["moves"])
        mx = baru[0] if baru else {}
        harus("B2: X pindah COGS_SERVICE -> COGS_SALES 13.000 (bersih)", mx.get("dari_kode") == J["account_code"]
              and mx.get("ke_kode") == S["account_code"] and mx.get("amount") == 13000.0, mx)
        harus("B2: dry_run nol tulis (jumlah jurnal sebelum == sesudah dry_run)", n_j0 == n_j1, [n_j0, n_j1])
        p = await reklas(False)
        catat["post"] = {k: p.get(k) for k in ("status", "journal_number", "total")}
        gl1 = await gl(S["id"], J["id"])
        delta_s = sum((D(str(m["amount"])) if m["ke_kode"] == S["account_code"] else -D(str(m["amount"])) for m in d1["moves"]), D("0"))
        harus("B2: posted; GL COGS_SALES berubah TEPAT +delta calon, COGS_SERVICE -delta (total kelas tetap)",
              p.get("status") == "posted" and gl1[S["account_code"]] - gl0[S["account_code"]] == delta_s
              and gl1[J["account_code"]] - gl0[J["account_code"]] == -delta_s, [str(gl0), str(gl1), str(delta_s)])
        jur = await c.fetchrow("SELECT source_type, description, total_debit, total_credit, status FROM journal_entries WHERE id = $1",
                               uuid.UUID(p["journal_id"]))
        harus("B2: jurnal ADJUSTMENT POSTED seimbang, deskripsi berpenanda RECLASS-COGS-CLASS", jur["source_type"] == "ADJUSTMENT"
              and jur["status"] == "POSTED" and jur["total_debit"] == jur["total_credit"] and jur["description"].startswith("RECLASS-COGS-CLASS"),
              dict(jur))
        d2 = await reklas(True)
        harus("B2: dry_run sesudah posting = 0 calon", d2["moves"] == [], d2["moves"])
        try:
            await reklas(False)
            harus("B2: posting ulang -> 409 SUDAH_DIREKLAS", False, "tak 409")
        except HTTPException as e:
            harus("B2: posting ulang -> 409 SUDAH_DIREKLAS", e.status_code == 409 and e.detail.get("code") == "SUDAH_DIREKLAS", e.detail)
        try:
            await RJ.reverse_journal(req(uid), uuid.UUID(p["journal_id"]), RJ.ReverseJournalRequest(reversal_date=hari, reason=PENANDA))
            harus("B2: jalur umum /journals/{id}/reverse MENOLAK akun HPP (pagar Gate 5 utuh)", False, "tak ditolak")
        except HTTPException as e:
            harus("B2: jalur umum /journals/{id}/reverse MENOLAK akun HPP (pagar Gate 5 utuh)", e.status_code == 400, e.detail)
        rv = (await RJ.reclassify_bill_cogs_class_endpoint(req(uid), RJ.ReclassifyBillCogsClassRequest(reverse_journal_id=p["journal_id"],
                                                                                                    reason=PENANDA)))["data"]
        harus("B2: pembalik lewat rute reklas (reverse_journal_id) -> reversed", rv.get("status") == "reversed", rv)
        try:
            await RJ.reclassify_bill_cogs_class_endpoint(req(uid), RJ.ReclassifyBillCogsClassRequest(reverse_journal_id=p["journal_id"]))
            harus("B2: balik ulang -> 409 SUDAH_DIBALIK", False, "tak 409")
        except HTTPException as e:
            harus("B2: balik ulang -> 409 SUDAH_DIBALIK", e.status_code == 409 and e.detail.get("code") == "SUDAH_DIBALIK", e.detail)
        gl2 = await gl(S["id"], J["id"])
        harus("B2: sesudah balik -> GL S/J kembali ke sebelum posting", gl2 == gl0, [str(gl0), str(gl2)])
        d3 = await reklas(True)
        harus("B2: sesudah reverse calon X muncul lagi", nx in {m["invoice_number"] for m in d3["moves"]}, d3["moves"])
        _ = (bx, by, ny)
    finally:
        await luar.rollback()
    sesudah = await saksi()
    harus("ROLLBACK: dari koneksi LAIN nol sisa FPB TES E2E, jumlah jurnal/stok/produk jasa tak berubah", sesudah == sebelum, [sebelum, sesudah])
    harus("nol panggilan kanal keluar", KELUAR == [], KELUAR)
    await c.close()
    await c2.close()
    gagal = [x["cek"] for x in cek if not x["ok"]]
    json.dump({"cek": cek, "catat": catat, "gagal": gagal}, open(keluar, "w"), default=str, indent=1)
    print("HASIL", "LULUS" if not gagal else f"GAGAL {gagal}", f"({len(cek)} cek)")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
