"""GERBANG V244 G3(b) + T3 — jalur sah pasca-posting tetap HIDUP di bawah pagar, lewat HANDLER.

- Satu koneksi, satu transaksi LUAR di-ROLLBACK. Badan V244 dipasang DI DALAM transaksi itu.
- app.services.db_pool.get_db_pool ditambal -> semua handler memakai koneksi gerbang;
  conn.transaction() handler menjadi SAVEPOINT.
- Tiap jalur di savepoint sendiri. Hasil disimpan di MEMORI Python (pelajaran V242).
- Jalur yang gagal DIJALANKAN ULANG TANPA PAGAR (trigger di-drop di savepoint) untuk
  memisahkan "dimatikan pagar" dari "gagal karena hal lain".
- T3: assert kolom pasca-posting BENAR-BENAR berubah (bukan cuma 200).

Jalankan: cd /app/backend/api_gateway && python3 /tmp/gerbang_v244_handler.py
"""
import asyncio
import os
import sys
import traceback
from datetime import date

sys.path.insert(0, "/app/backend/api_gateway")

import asyncpg  # noqa: E402
from starlette.requests import Request  # noqa: E402

T = "kaos-biru-konveksi"
BADAN = "/tmp/V244_badan.sql"
hasil = []  # (jalur, uji, ok, ket)
# cacah diturunkan dari rincian per-jalur (handler + cek T3), bukan angka ketik-tangan.
# tiap jalur = 1 (handler berhasil) + jumlah cek fn_cek; prasyarat/vendor/kontrol = 1.
_CEK_PER_JALUR = {"prasyarat": 1, "j1": 3, "j2": 3, "j3": 2, "j4": 2, "j5": 2, "j6": 3, "vendor": 1, "kontrol": 1}
HARAP_CACAH = sum(_CEK_PER_JALUR.values())
# 14 Sep 2026 unit B: label pengecualian jalur 6 DICABUT -> jalur 6 diuji lewat handler seperti jalur lain.


def catat(jalur, uji, ok, ket=""):
    hasil.append((jalur, uji, bool(ok), str(ket)[:220]))


def req(user_id):
    return Request({"type": "http", "method": "POST", "path": "/", "headers": [],
                    "query_string": b"", "state": {"user": {"tenant_id": T, "user_id": str(user_id)}}})


async def main():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])

    import app.services.db_pool as dbp

    class FakePool:
        def acquire(self, *a, **k):
            class Ctx:
                async def __aenter__(s):
                    return conn
                async def __aexit__(s, *e):
                    return False
                def __await__(s):
                    async def _c():
                        return conn
                    return _c().__await__()
            return Ctx()

        async def release(self, c):
            return None

        async def fetchval(self, *a, **k):
            return await conn.fetchval(*a, **k)

        async def fetch(self, *a, **k):
            return await conn.fetch(*a, **k)

        async def fetchrow(self, *a, **k):
            return await conn.fetchrow(*a, **k)

        async def execute(self, *a, **k):
            return await conn.execute(*a, **k)

    async def fake_get_db_pool(*a, **k):
        return FakePool()

    dbp.get_db_pool = fake_get_db_pool

    from app.routers import sales_invoices as si, expenses as ex, bill_payments as bp
    from app.routers import customer_deposits as cd
    if len(sys.argv) > 1:
        # uji sebelum deploy: modul unit B dari dir salinan (helper dimuat dulu karena rute mengimpornya relatif)
        import importlib.util

        def _muat(nama, path):
            spec = importlib.util.spec_from_file_location(nama, path)
            m = importlib.util.module_from_spec(spec)
            sys.modules[nama] = m
            spec.loader.exec_module(m)
            return m
        import app.routers  # noqa: F401
        _muat("app.services.pihak_helpers", f"{sys.argv[1]}/pihak_helpers.py")
        cn = _muat("app.routers.credit_notes_b", f"{sys.argv[1]}/credit_notes.py")
    else:
        from app.routers import credit_notes as cn
    from app.schemas.sales_invoices import InvoicePaymentCreate, VoidInvoiceRequest
    from app.schemas.expenses import VoidExpenseRequest
    from app.schemas.bill_payments import CreateBillPaymentRequest, BillAllocationInput
    from app.schemas.customer_deposits import ApplyCustomerDepositRequest, ApplyDepositItem
    from app.schemas.credit_notes import ApplyCreditNoteRequest, ApplyCreditNoteItem, CreateCreditNoteRequest

    user_id = await conn.fetchval(
        "SELECT created_by FROM bank_transactions WHERE tenant_id=$1 AND created_by IS NOT NULL LIMIT 1", T)
    ba = await conn.fetchrow(
        "SELECT id, coa_id FROM bank_accounts WHERE tenant_id=$1 AND is_active ORDER BY account_name LIMIT 1", T)

    # Fase: sebelum deploy trigger tak ada (harap 0 sesudah ROLLBACK); sesudah deploy ada 5.
    # Run pertama atas trigger hidup memerah di kontrol ini karena harapnya dipatok ke fase uji kering.
    trg_awal = await conn.fetchval("SELECT count(*) FROM pg_trigger WHERE tgname='trg_law19_bekukan_nominal'")

    luar = conn.transaction()
    await luar.start()
    try:
        await conn.execute(open(BADAN, encoding="utf-8").read())
        n_trg = await conn.fetchval(
            "SELECT count(*) FROM pg_trigger WHERE tgname='trg_law19_bekukan_nominal' AND NOT tgisinternal")
        catat("prasyarat", "pagar V244 terpasang di transaksi gerbang (5 trigger)", n_trg == 5, n_trg)

        async def jalankan(nama, fn_panggil, fn_cek):
            """fn_panggil() -> (ok, ket); fn_cek() -> list[(uji, ok, ket)] dibaca SEBELUM rollback."""
            sp = conn.transaction(); await sp.start()
            try:
                ok, ket = await fn_panggil()
            except Exception as e:  # noqa: BLE001
                ok, ket = False, f"{type(e).__name__}: {getattr(e, 'detail', e)}"
            cek = await fn_cek() if ok else []
            await sp.rollback()
            catat(nama, "handler berhasil di bawah pagar", ok, ket)
            for u, o, k in cek:
                catat(nama, u, o, k)
            if not ok:
                sp2 = conn.transaction(); await sp2.start()
                for tb in ("sales_invoices", "bills", "expenses", "receive_payments", "bill_payments_v2"):
                    await conn.execute(f"DROP TRIGGER IF EXISTS trg_law19_bekukan_nominal ON {tb}")
                try:
                    ok2, ket2 = await fn_panggil()
                except Exception as e:  # noqa: BLE001
                    ok2, ket2 = False, f"{type(e).__name__}: {getattr(e, 'detail', e)}"
                await sp2.rollback()
                # (pengecualian sempit jalur 6 DICABUT 14 Sep 2026, unit B: semua jalur yang mati dua arah MERAH)
                if True:
                    catat(nama, "KLASIFIKASI: tanpa pagar berhasil? (True = DIMATIKAN PAGAR)", False,
                          f"tanpa_pagar_ok={ok2} {ket2}")

        # ---------- 1. bayar faktur ----------
        inv = await conn.fetchrow(
            """SELECT a.invoice_id, a.outstanding FROM compute_ar_outstanding($1) a
               JOIN sales_invoices s ON s.id = a.invoice_id
               WHERE a.outstanding >= 1000 AND s.journal_id IS NOT NULL ORDER BY a.invoice_id LIMIT 1""", T)
        if inv:
            s0 = await conn.fetchrow("SELECT amount_paid, status FROM sales_invoices WHERE id=$1", inv["invoice_id"])

            async def p1():
                body = InvoicePaymentCreate(amount=1000, payment_date=date.today(), payment_method="transfer",
                                            account_id=str(ba["coa_id"]), bank_account_id=str(ba["id"]))
                await si.record_payment(req(user_id), inv["invoice_id"], body)
                return True, "200"

            async def c1():
                s1 = await conn.fetchrow("SELECT amount_paid, status FROM sales_invoices WHERE id=$1", inv["invoice_id"])
                return [("T3 amount_paid faktur BERUBAH (+1000)",
                         (s1["amount_paid"] or 0) - (s0["amount_paid"] or 0) == 1000, f"{s0['amount_paid']}->{s1['amount_paid']}"),
                        ("T3 status faktur konsisten dgn pembayaran parsial",
                         s1["status"] in ("partial", "paid"), f"{s0['status']}->{s1['status']}")]
            await jalankan("1 bayar faktur", p1, c1)
        else:
            catat("1 bayar faktur", "subjek ada", False, "tak ada faktur berjurnal ber-outstanding")

        # ---------- 2. bayar tagihan (buat draf -> post) ----------
        bill = await conn.fetchrow(
            """SELECT a.bill_id, a.outstanding, b.vendor_id FROM compute_ap_outstanding($1) a
               JOIN bills b ON b.id = a.bill_id
               WHERE a.bill_id IS NOT NULL AND a.outstanding >= 1000 AND b.journal_id IS NOT NULL
               ORDER BY a.bill_id LIMIT 1""", T)
        if bill:
            b0 = await conn.fetchrow("SELECT amount_paid, status, status_v2 FROM bills WHERE id=$1", bill["bill_id"])
            dicatat = {}

            async def p2():
                body = CreateBillPaymentRequest(vendor_id=str(bill["vendor_id"]), payment_date=date.today(),
                                                bank_account_id=str(ba["id"]), total_amount=1000,
                                                allocations=[BillAllocationInput(bill_id=str(bill["bill_id"]), amount_applied=1000)],
                                                save_as_draft=True)
                r = await bp.create_bill_payment(req(user_id), body)
                # BillPaymentResponse.data adalah DICT (run pertama gerbang salah baca -> pid None)
                data = r.data if hasattr(r, "data") else r.get("data")
                pid = (data or {}).get("id") or (data or {}).get("payment_id")
                if not pid:
                    return False, f"ALAT: id pembayaran tak terbaca dari respons, data keys={list((data or {}).keys())}"
                dicatat["pid"] = pid
                await bp.post_bill_payment(req(user_id), str(pid))
                return True, f"payment={pid}"

            async def c2():
                b1 = await conn.fetchrow("SELECT amount_paid, status, status_v2 FROM bills WHERE id=$1", bill["bill_id"])
                return [("T3 amount_paid tagihan BERUBAH (+1000)",
                         (b1["amount_paid"] or 0) - (b0["amount_paid"] or 0) == 1000, f"{b0['amount_paid']}->{b1['amount_paid']}"),
                        ("T3 status tagihan terbaca sesudah bayar", b1["status_v2"] is not None,
                         f"{b0['status']}/{b0['status_v2']} -> {b1['status']}/{b1['status_v2']}")]
            await jalankan("2 bayar tagihan", p2, c2)
        else:
            catat("2 bayar tagihan", "subjek ada", False, "tak ada tagihan ber-outstanding")

        # ---------- 3. void beban ----------
        exp = await conn.fetchval(
            "SELECT id FROM expenses WHERE tenant_id=$1 AND status='posted' AND journal_id IS NOT NULL ORDER BY id LIMIT 1", T)

        async def p3():
            await ex.void_expense(req(user_id), exp, VoidExpenseRequest(reason="gerbang V244"))
            return True, "200"

        async def c3():
            r = await conn.fetchrow("SELECT status, total_amount FROM expenses WHERE id=$1", exp)
            return [("beban ber-status void sesudahnya", r["status"] == "void", r["status"])]
        await jalankan("3 void beban", p3, c3)

        # ---------- 4. void faktur (tanpa pembayaran) ----------
        vinv = await conn.fetchval(
            """SELECT id FROM sales_invoices WHERE tenant_id=$1 AND status='posted' AND journal_id IS NOT NULL
               AND COALESCE(amount_paid,0)=0 ORDER BY id LIMIT 1""", T)
        if vinv:
            async def p4():
                await si.void_invoice(req(user_id), vinv, VoidInvoiceRequest(reason="gerbang V244"))
                return True, "200"

            async def c4():
                r = await conn.fetchrow("SELECT status FROM sales_invoices WHERE id=$1", vinv)
                return [("faktur ber-status void sesudahnya", r["status"] == "void", r["status"])]
            await jalankan("4 void faktur", p4, c4)
        else:
            catat("4 void faktur", "subjek ada", False, "tak ada faktur posted tanpa pembayaran")

        # ---------- 5. terapkan DP pelanggan ke faktur ----------
        pasangan = await conn.fetchrow(
            """SELECT d.id AS dep, a.invoice_id, LEAST(d.amount - COALESCE(d.amount_applied,0) - COALESCE(d.amount_refunded,0), a.outstanding) AS maks
               FROM customer_deposits d
               JOIN sales_invoices s ON s.customer_id::text = d.customer_id::text AND s.journal_id IS NOT NULL
               JOIN compute_ar_outstanding($1) a ON a.invoice_id = s.id
               WHERE d.tenant_id=$1 AND d.status IN ('posted','partial')
                 AND d.amount - COALESCE(d.amount_applied,0) - COALESCE(d.amount_refunded,0) >= 1000
                 AND a.outstanding >= 1000
               ORDER BY d.id, a.invoice_id LIMIT 1""", T)
        if pasangan:
            s0 = await conn.fetchrow("SELECT amount_paid, status FROM sales_invoices WHERE id=$1", pasangan["invoice_id"])

            async def p5():
                body = ApplyCustomerDepositRequest(applications=[ApplyDepositItem(invoice_id=str(pasangan["invoice_id"]), amount=1000)])
                await cd.apply_customer_deposit(req(user_id), pasangan["dep"], body)
                return True, "200"

            async def c5():
                s1 = await conn.fetchrow("SELECT amount_paid, status FROM sales_invoices WHERE id=$1", pasangan["invoice_id"])
                return [("T3 amount_paid faktur BERUBAH lewat DP (+1000)",
                         (s1["amount_paid"] or 0) - (s0["amount_paid"] or 0) == 1000, f"{s0['amount_paid']}->{s1['amount_paid']}")]
            await jalankan("5 terapkan DP", p5, c5)
        else:
            catat("5 terapkan DP", "subjek ada", False, "tak ada pasangan DP posted + faktur outstanding pelanggan sama")

        # ---------- 6. terapkan nota kredit ke faktur (unit B: CN SINTETIS, seluruh nilai, satu faktur) ----------
        # CN historis tak dipakai sebagai subjek (putusan pemilik: tak dikaitkan otomatis).
        pcn = await conn.fetchrow(
            """SELECT a.invoice_id, s.customer_id, a.outstanding FROM compute_ar_outstanding($1) a
               JOIN sales_invoices s ON s.id = a.invoice_id
               WHERE a.outstanding >= 1000 AND s.journal_id IS NOT NULL AND s.customer_id IS NOT NULL
               ORDER BY a.invoice_id LIMIT 1""", T)
        if pcn:
            s0 = await conn.fetchrow("SELECT amount_paid, status FROM sales_invoices WHERE id=$1", pcn["invoice_id"])
            o0 = pcn["outstanding"]

            async def p6():
                CItem = CreateCreditNoteRequest.model_fields["items"].annotation.__args__[0]
                r = await cn.create_credit_note(req(user_id), CreateCreditNoteRequest(
                    customer_id=str(pcn["customer_id"]), customer_name="gerbang V244 j6", credit_note_date=date.today(),
                    reason="other", items=[CItem(description="gerbang V244 j6", quantity=1, unit_price=1000)]))
                cid = (r.get("data") or r)["id"]
                await cn.post_credit_note(req(user_id), cid)
                body = ApplyCreditNoteRequest(applications=[ApplyCreditNoteItem(invoice_id=str(pcn["invoice_id"]), amount=1000)])
                await cn.apply_credit_note(req(user_id), cid, body)
                return True, "200"

            async def c6():
                s1 = await conn.fetchrow("SELECT amount_paid, status FROM sales_invoices WHERE id=$1", pcn["invoice_id"])
                o1 = await conn.fetchval("SELECT COALESCE(SUM(outstanding),0) FROM compute_ar_outstanding($1) WHERE invoice_id=$2", T, pcn["invoice_id"])
                return [("T3 amount_paid faktur BERUBAH lewat nota kredit (+1000)",
                         (s1["amount_paid"] or 0) - (s0["amount_paid"] or 0) == 1000, f"{s0['amount_paid']}->{s1['amount_paid']}"),
                        ("T3 outstanding compute_ar_outstanding TURUN 1000 (atribusi, bukan cuma cache)",
                         o0 - o1 == 1000, f"{o0}->{o1}")]
            await jalankan("6 terapkan nota kredit", p6, c6)
        else:
            catat("6 terapkan nota kredit", "subjek ada", False, "TAK SAH: tak ada faktur ber-outstanding >= 1000 dengan pelanggan")

        # ---------- tambahan: kredit vendor ----------
        nvc = await conn.fetchval("SELECT count(*) FROM vendor_credits WHERE tenant_id=$1", T)
        catat("tambahan kredit vendor", "TIDAK DIJALANKAN: subjek kredit vendor", True, f"vendor_credits={nvc} baris -> tak ada subjek (bukan lulus)")
    except Exception:
        traceback.print_exc()
        catat("GERBANG", "tanpa galat tak tertangani", False, "lihat traceback")
    finally:
        await luar.rollback()

    n_trg_sesudah = await conn.fetchval("SELECT count(*) FROM pg_trigger WHERE tgname='trg_law19_bekukan_nominal'")
    catat("KONTROL", "cacah trigger sesudah ROLLBACK = sebelum gerbang (nol perubahan menetap)",
          n_trg_sesudah == trg_awal, f"sebelum={trg_awal} sesudah={n_trg_sesudah}")
    await conn.close()

    print()
    for j, u, ok, k in hasil:
        print(("[H] " if ok else "[X] ") + f"{j:24} {u}  | {k}")
    gagal = sum(1 for h in hasil if not h[2])
    print(f"\ngagal={gagal} total={len(hasil)} harap={HARAP_CACAH} -> "
          f"{'LENGKAP' if len(hasil) == HARAP_CACAH else 'CACAH BEDA (baca daftar; jalur yang gagal menambah baris klasifikasi)'}")
    sys.exit(0 if gagal == 0 and len(hasil) == HARAP_CACAH else 1)


asyncio.run(main())
