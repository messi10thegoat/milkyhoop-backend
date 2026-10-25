"""UJI NYATA PPh pembayaran tagihan dihitung SERVER (putusan pemilik 25 Okt 2026). kaos SAJA; SEMUA tulis di transaksi luar ROLLBACK.

Dijalankan per pohon (MODE=LAMA pohon utama, MODE=BARU worktree). Tagihan uji = tagihan terposting kaos dengan sisa (compute_ap_rincian),
jenis = PPH23-2, rekening = rekening aktif pertama. Harapan dihitung DI SINI dari kolom DB (bukan dari fungsi yang diuji):
  dasar = (grand_total - tax_amount) x dibayar / grand_total ; pph = dasar x 2% -> rupiah penuh HALF_UP.
P1 pratinjau /bills/{id}/payments  tanpa pph_amount      BARU: pph == harapan, baris jurnal Cr PPh == harapan, jurnal seimbang
P2 pratinjau /bills/{id}/payments  pph_amount = harap+1000 BARU: blok PPH_TIDAK_SESUAI         (LAMA: tanpa blok -> pembeda)
P3 pratinjau /bill-payments (alokasi ke tagihan yang sama) BARU: pph == harapan
W1 tulis BillsService.record_payment (ROLLBACK)          BARU: rekaman PPh tax_period YYYY-MM, tax_amount == harapan, dasar == harapan
W2 tulis POST /bill-payments create_bill_payment (ROLLBACK) BARU: sama + bill_payments_v2.pph_amount == harapan
W3 tulis POST /bill-payments dgn pph_amount beda (ROLLBACK) BARU: 400 PPH_TIDAK_SESUAI, nol baris tertulis
Sesudah ROLLBACK: hitung baris withholding/jurnal/pembayaran kaos == sebelum (net 0). Pindai dampak: expenses ber-kode PPh (WORKSPACE)."""
import asyncio, os, sys, uuid
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "x")
import asyncpg
from starlette.requests import Request

T = "kaos-biru-konveksi"
MODE, MERAH = os.environ["MODE"], os.environ.get("MERAH", "")
gagal = []
JEJAK = """SELECT (SELECT count(*) FROM withholding_tax_records WHERE tenant_id = $1) w, (SELECT count(*) FROM journal_entries WHERE tenant_id = $1) j,
                  (SELECT count(*) FROM bill_payments_v2 WHERE tenant_id = $1) p2, (SELECT count(*) FROM bill_payments WHERE tenant_id = $1) p1"""


def cek(ok, l, i=""):
    print(("PASS " if ok else "FAIL ") + l + (f" | {i}" if i else ""), flush=True)
    if not ok:
        gagal.append(l)


def pool_untuk(conn):
    class K:
        def __getattr__(s, a): return getattr(conn, a)
        def transaction(s, **kw): return conn.transaction()
    k = K()
    class CM:
        async def __aenter__(s): return k
        async def __aexit__(s, *a): return False
    class Pool:
        def acquire(s): return CM()
    return Pool()


def req(uid):
    async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
    r = Request({"type": "http", "method": "POST", "path": "/", "headers": [], "query_string": b""}, receive)
    r.state.user = {"tenant_id": T, "user_id": str(uid), "id": str(uid), "role": "OWNER"}
    return r


def harapan(gt, ppn, bayar):
    dasar = (Decimal(str(gt)) - Decimal(str(ppn or 0))) * Decimal(str(bayar)) / Decimal(str(gt))
    return dasar, (dasar * 2 / 100).quantize(Decimal(1), rounding=ROUND_HALF_UP)


def baris_pph(jurnal):
    return [l for j in jurnal for l in j.get("lines", []) if "PPh" in str(l.get("memo") or l.get("description") or "")]


async def main():
    from app.services import bayar_keluar_rencana as BKR
    from app.services.bills_service import BillsService
    from app.routers import bill_payments as BP
    from app.schemas.bill_payments import CreateBillPaymentRequest
    url = os.environ["DATABASE_URL"]
    c = await asyncpg.connect(url)
    try:
        cek(await c.fetchval('SELECT 1 FROM "Tenant" WHERE id = $1', T) == 1, "tenant kaos ada")
        b = await c.fetchrow(
            """SELECT b.id, b.vendor_id, b.vendor_name, COALESCE(b.grand_total, b.amount) gt, b.tax_amount, r.outstanding
               FROM compute_ap_rincian($1) r JOIN bills b ON b.id = r.bill_id AND b.tenant_id = $1
               WHERE r.outstanding >= 2000 AND COALESCE(b.status_v2, b.status) NOT IN ('draft', 'void') ORDER BY b.created_at DESC LIMIT 1""", T)
        cek(b is not None, "ada tagihan terposting bersisa di kaos")
        tc = await c.fetchval("SELECT id FROM tax_codes WHERE tenant_id = $1 AND code = 'PPH23-2' AND is_withholding", T)
        ba = await c.fetchrow("""SELECT ba.id, ba.account_name FROM bank_accounts ba JOIN chart_of_accounts coa ON coa.id = ba.coa_id
                                 WHERE ba.tenant_id = $1 AND ba.is_active AND NOT coa.is_header AND ba.account_type <> 'credit_card'
                                 ORDER BY ba.created_at LIMIT 1""", T)
        uid = await c.fetchval("SELECT created_by FROM bill_payments_v2 WHERE tenant_id = $1 AND created_by IS NOT NULL LIMIT 1", T)
        cek(uid is not None, "pengguna nyata kaos (created_by pembayaran lama)")
        bayar = int(Decimal(str(b["outstanding"])) // 2)
        dasar, harap = harapan(b["gt"], b["tax_amount"], bayar)
        if MERAH == "pembulatan":
            harap = harap + 1  # pembanding tersabotase -> P1/W1 WAJIB merah
        print(f"tagihan {b['id']} gt={b['gt']} ppn={b['tax_amount']} sisa={b['outstanding']} bayar={bayar} -> dasar={dasar:.2f} pph={harap}")
        sebelum = dict(await c.fetchrow(JEJAK, T))
        rq = {"amount": bayar, "payment_method": "transfer", "bank_account_id": str(ba["id"]), "pph_tax_code_id": str(tc), "payment_date": date.today().isoformat()}

        # ── pratinjau (READ ONLY) ──
        tr = c.transaction(readonly=True); await tr.start()
        try:
            p1 = await BKR.pratinjau_bayar_tagihan(c, T, b["id"], dict(rq))
            pp = p1.get("pph") or {}
            lp = baris_pph(p1["journals"])
            if MODE == "BARU":
                cek(Decimal(str(pp.get("amount", -1))) == harap and not p1["blocks"], "P1 pph server == harapan", f"{pp} blok={p1['blocks']}")
                cek(len(lp) == 1 and Decimal(str(lp[0].get("credit"))) == harap, "P1 baris jurnal Cr PPh == harapan", str(lp))
            else:
                cek(not lp, "P1 LAMA: tanpa pph_amount tak ada baris PPh (pembeda)", str(lp))
            p2 = await BKR.pratinjau_bayar_tagihan(c, T, b["id"], dict(rq, pph_amount=int(harap) + 1000))
            kode = [x["code"] for x in p2["blocks"]]
            if MODE == "BARU":
                cek("PPH_TIDAK_SESUAI" in kode, "P2 nominal klien beda -> blok", str(p2["blocks"]))
            else:
                cek("PPH_TIDAK_SESUAI" not in kode, "P2 LAMA: angka klien dipercaya (pembeda)")
            pl = CreateBillPaymentRequest(vendor_id=str(b["vendor_id"]), payment_date=date.today(), bank_account_id=str(ba["id"]),
                                          total_amount=bayar, allocations=[{"bill_id": str(b["id"]), "amount_applied": bayar}],
                                          pph_tax_code_id=str(tc))
            p3 = await BKR.rencana_bayar(c, {"tenant_id": T, "user_id": uuid.UUID(str(uid))}, pl, pratinjau=True)
            if MODE == "BARU":
                cek(Decimal(str((p3.get("pph") or {}).get("amount", -1))) == harap and not p3["blocks"],
                    "P3 /bill-payments pratinjau pph == harapan", f"{p3.get('pph')} blok={p3['blocks']}")
        finally:
            await tr.rollback()

        # ── tulis (transaksi luar, SELALU ROLLBACK) ──
        tx = c.transaction(); await tx.start()
        try:
            await c.execute("SELECT set_config('app.tenant_id', $1, true)", T)
            h = await BillsService(pool_untuk(c)).record_payment(T, b["id"], dict(rq), uuid.UUID(str(uid)))
            w = await c.fetch("""SELECT tax_period, base_amount, tax_amount FROM withholding_tax_records
                                 WHERE tenant_id = $1 AND created_at >= now() - interval '1 minute'""", T)
            if MODE == "BARU":
                ok = h.get("success") and len(w) == 1 and w[0]["tax_period"] == date.today().strftime("%Y-%m") \
                    and Decimal(str(w[0]["tax_amount"])) == harap and abs(Decimal(str(w[0]["base_amount"])) - dasar) < Decimal("0.01")
                cek(ok, "W1 record_payment: rekaman PPh YYYY-MM, angka server", f"{h.get('message')} {[dict(x) for x in w]}")
            else:
                cek(h.get("success") and len(w) == 0, "W1 LAMA: tanpa pph_amount tak ada rekaman PPh (pembeda)", str([dict(x) for x in w]))
        finally:
            await tx.rollback()
        tx = c.transaction(); await tx.start()
        try:
            await c.execute("SELECT set_config('app.tenant_id', $1, true)", T)
            BP.get_pool = lambda: _ap(pool_untuk(c))
            r = await BP.create_bill_payment(req(uid), pl)
            r = r if isinstance(r, dict) else r.model_dump()
            pid = (r.get("data") or {}).get("id") or (r.get("data") or {}).get("payment_id")
            v2 = await c.fetchrow("SELECT pph_amount, pph_tax_code_id FROM bill_payments_v2 WHERE tenant_id = $1 AND id = $2::uuid", T, str(pid))
            w = await c.fetch("SELECT tax_period, tax_amount FROM withholding_tax_records WHERE tenant_id = $1 AND payment_id = $2::uuid", T, str(pid))
            if MODE == "BARU":
                ok = v2 and Decimal(str(v2["pph_amount"])) == harap and len(w) == 1 and w[0]["tax_period"] == date.today().strftime("%Y-%m")
                cek(ok, "W2 POST /bill-payments: pph_amount tersimpan == server, rekaman YYYY-MM", f"{dict(v2) if v2 else None} {[dict(x) for x in w]}")
            else:
                cek(v2 is not None and Decimal(str(v2["pph_amount"] or 0)) == 0, "W2 LAMA: tanpa pph_amount tersimpan 0 (pembeda)")
        except Exception as e:  # noqa: BLE001
            cek(False, f"W2 {type(e).__name__}: {getattr(e, 'detail', e)}")
        finally:
            await tx.rollback()
        # W3 kontrol positif + pembeda: nominal klien BEDA. LAMA menerima & menulis angka klien (rekaman PPh terbentuk = jalur tulis PPh
        # benar-benar tercapai); BARU menolak 400 tanpa menulis apa pun.
        tx = c.transaction(); await tx.start()
        try:
            await c.execute("SELECT set_config('app.tenant_id', $1, true)", T)
            beda = int(harap) + 1000
            pl_beda = pl.model_copy(update={"pph_amount": beda})
            try:
                r = await BP.create_bill_payment(req(uid), pl_beda)
                r = r if isinstance(r, dict) else r.model_dump()
                pid = (r.get("data") or {}).get("id")
                v2 = await c.fetchrow("SELECT pph_amount FROM bill_payments_v2 WHERE tenant_id = $1 AND id = $2::uuid", T, str(pid))
                nw = await c.fetchval("SELECT count(*) FROM withholding_tax_records WHERE tenant_id = $1 AND payment_id = $2::uuid", T, str(pid))
                if MODE == "BARU":
                    cek(False, "W3 nominal beda harus ditolak", str(r))
                else:
                    cek(v2 is not None and Decimal(str(v2["pph_amount"])) == beda and nw == 1,
                        "W3 LAMA: angka klien beda DITERIMA + rekaman PPh tertulis (kontrol positif + pembeda)", f"{dict(v2) if v2 else None} rekaman={nw}")
            except Exception as e:  # noqa: BLE001
                if MODE == "BARU":
                    cek(getattr(e, "status_code", None) == 400 and "PPh" in str(getattr(e, "detail", "")), "W3 BARU: nominal klien beda -> 400",
                        str(getattr(e, "detail", e)))
                else:
                    cek(False, f"W3 LAMA galat tak terduga {type(e).__name__}: {getattr(e, 'detail', e)}")
        finally:
            await tx.rollback()
        sesudah = dict(await c.fetchrow(JEJAK, T))
        cek(sebelum == sesudah, "net 0 sesudah ROLLBACK", f"{sebelum} -> {sesudah}")
        if MODE == "BARU":
            n = await c.fetchval("""SELECT count(*) FROM expenses e JOIN tax_codes tc ON tc.id::text = e.tax_id::text
                                    WHERE tc.is_withholding""") if await c.fetchval(
                "SELECT 1 FROM information_schema.columns WHERE table_name = 'expenses' AND column_name = 'tax_id'") else "kolom tax_id tak ada"
            print(f"DAMPAK beban ber-kode PPh sebagai pajak baris (semua tenant): {n}")
    finally:
        await c.close()
    print(f"SELESAI {MODE}: {len(gagal)} gagal")
    return 1 if gagal else 0


async def _ap(p):
    return p


sys.exit(asyncio.run(main()))
