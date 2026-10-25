"""UJI NYATA SA-KOREKSI (25 Okt 2026) -- ROLLBACK-ONLY di kaos, vendor "TES E2E Vendor Saldo mv3yrg63" (satu-satunya ber-saldo awal LAMA).
argv: <keluaran.json> <lama|baru>. Satu transaksi luar SELALU ROLLBACK; JARING: jumlah jurnal/tagihan/UM kaos dibaca lewat koneksi TERPISAH
sebelum & sesudah (bocor commit = beda = GAGAL). Langkah: (1) pratinjau balik [baru]; (2) balik + ulang ber-kunci [baru]; (3) saldo awal baru
5.000; (4) UM 2.000 dibuat+posting, diterapkan 1.000 ke tagihan pembuka; (5) void pembuka (baru: WAJIB ditolak SALDO_AWAL_ADA_UANG_MUKA;
lama: lolos, UM dilepas otomatis); (6) lepas UM lalu void (lolos); (7) saldo awal baru lagi (koreksi = void + buat baru). Dua sumber: compute_ap
vendor + GL per akun; jurnal lama tetap ada (reversed_by_id)."""
import asyncio, json, os, sys, uuid
from datetime import date
from decimal import Decimal
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "boneka")
import asyncpg
from fastapi import HTTPException, Response
from starlette.requests import Request

T = "kaos-biru-konveksi"
KELUAR, MODE = sys.argv[1], sys.argv[2]
BARU = MODE == "baru"


def js(v):
    return json.loads(json.dumps(v, default=str))


async def main():
    from app.routers import vendors as V, bills as B, vendor_deposits as VD
    from app.schemas.bills import VoidBillRequest
    from app.schemas.vendor_deposits import VendorDepositCreate, ApplyDepositRequest
    url = os.environ["URL"]
    c = await asyncpg.connect(url)

    async def jaring():
        k = await asyncpg.connect(url)
        try:
            # kolom diukur information_schema 25 Okt: vendor_deposit_applications TANPA tenant_id -> lewat vendor_deposits
            n = {t: await k.fetchval(f"SELECT count(*) FROM {t} WHERE tenant_id = $1", T)
                 for t in ("journal_entries", "bills", "vendor_deposits", "audit_logs", "vendor_activities", "idempotency_keys")}
            n["vendor_deposit_applications"] = await k.fetchval(
                "SELECT count(*) FROM vendor_deposit_applications a JOIN vendor_deposits d ON d.id = a.vendor_deposit_id WHERE d.tenant_id = $1", T)
            return n
        finally:
            await k.close()

    vid = await c.fetchval("SELECT id FROM vendors WHERE tenant_id = $1 AND name = 'TES E2E Vendor Saldo mv3yrg63'", T)
    if not vid:
        sys.exit("VENDOR UJI TAK ADA")
    uid = await c.fetchval("SELECT created_by FROM vendors WHERE tenant_id = $1 AND created_by IS NOT NULL LIMIT 1", T)
    bank = await c.fetchval("SELECT id FROM bank_accounts WHERE tenant_id = $1 AND is_active AND account_name = 'BCA Operasional'", T)

    class Kon:
        def __getattr__(s, n): return getattr(c, n)
        def transaction(s, **kw): return c.transaction()

    class CMA:
        async def __aenter__(s): return Kon()
        async def __aexit__(s, *a): return False

    class PA:
        def acquire(s): return CMA()
        async def release(s, *a): return None
    async def gpa(): return PA()
    for m in (V, B, VD):
        m.get_pool = gpa
    import app.services.db_pool as DBP
    DBP.get_db_pool = gpa

    def req(kunci=None, badan=None):
        h = [(b"x-idempotency-key", kunci.encode())] if kunci else []
        isi = json.dumps(badan).encode() if badan is not None else b""
        async def receive(): return {"type": "http.request", "body": isi, "more_body": False}
        r = Request({"type": "http", "method": "POST", "path": "/", "headers": h, "query_string": b""}, receive)
        r.state.user = {"tenant_id": T, "user_id": str(uid), "role": "OWNER", "username": "TES E2E"}
        return r

    async def panggil(f):
        try:
            r = await f()
            return {"ok": js(r.model_dump() if hasattr(r, "model_dump") else r)}
        except HTTPException as e:
            return {"galat": [e.status_code, js(e.detail)]}

    async def ap():
        return str(await c.fetchval("SELECT COALESCE(SUM(outstanding), 0) FROM compute_ap_outstanding($1) WHERE vendor_id = $2", T, vid))

    async def gl():
        rows = await c.fetch("""SELECT coa.account_code, SUM(jl.debit) AS d, SUM(jl.credit) AS k FROM journal_lines jl
                                JOIN journal_entries je ON je.id = jl.journal_id JOIN chart_of_accounts coa ON coa.id = jl.account_id
                                WHERE je.tenant_id = $1 AND je.status = 'POSTED' GROUP BY 1 ORDER BY 1""", T)
        return {r["account_code"]: [str(r["d"]), str(r["k"])] for r in rows}

    async def lama_jurnal():
        return js([dict(r) for r in await c.fetch("""SELECT id, journal_number, status, (reversed_by_id IS NOT NULL) AS dibalik FROM journal_entries
                   WHERE tenant_id = $1 AND source_type = 'VENDOR_OPENING_BALANCE' AND source_id = $2 ORDER BY journal_number""", T, vid)])

    h = {"mode": MODE, "jaring_sebelum": await jaring()}
    tx = c.transaction(); await tx.start()
    try:
        h["ap0"], h["gl0"], h["lama0"] = await ap(), await gl(), await lama_jurnal()
        if BARU:
            sb = (await gl(), await ap())
            h["pratinjau"] = await panggil(lambda: V.preview_reverse_legacy_opening_balance(req(), vid))
            h["pratinjau_tanpa_tulis"] = (await gl(), await ap()) == sb
            k = f"sak-{uuid.uuid4().hex}"
            h["balik"] = await panggil(lambda: V.reverse_legacy_opening_balance(req(k), vid, Response()))
            h["balik_ulang"] = await panggil(lambda: V.reverse_legacy_opening_balance(req(k), vid, Response()))
            h["balik_kedua_tanpa_kunci"] = await panggil(lambda: V.reverse_legacy_opening_balance(req(), vid, Response()))
        h["ap1"], h["gl1"], h["lama1"] = await ap(), await gl(), await lama_jurnal()
        sa = lambda: V.set_vendor_opening_balance(req(badan={"amount": 5000, "as_of_date": str(date.today())}), str(vid), Response())
        h["sa_baru"] = await panggil(sa)
        h["ap2"] = await ap()
        bill_id = (h["sa_baru"].get("ok") or {}).get("data", {}).get("bill_id")
        if bill_id:
            um = await panggil(lambda: VD.create_vendor_deposit(req(), VendorDepositCreate(
                deposit_date=date.today(), vendor_id=vid, bank_account_id=bank, amount=2000, simpan_dan_posting=True,
                notes="TES E2E SA-KOREKSI"), Response()))
            h["um_buat"] = list(um)[0]
            um_id = uuid.UUID(str((um.get("ok") or {}).get("id"))) if "ok" in um else None
            if um_id:
                ter = await panggil(lambda: VD.apply_vendor_deposit(req(), um_id, ApplyDepositRequest(bill_id=uuid.UUID(bill_id), amount=1000), Response()))
                h["um_terap"] = list(ter)[0]
                h["void_ber_um"] = await panggil(lambda: B.void_bill(req(), uuid.UUID(bill_id), VoidBillRequest(reason="TES E2E koreksi"), Response()))
                h["ap3"] = await ap()
                app_id = await c.fetchval("SELECT id FROM vendor_deposit_applications WHERE vendor_deposit_id = $1 AND status = 'active'", um_id)
                if app_id:
                    h["um_lepas"] = list(await panggil(lambda: VD.unapply_vendor_deposit(req(), um_id, app_id, Response())))[0]
                    h["void_bersih"] = await panggil(lambda: B.void_bill(req(), uuid.UUID(bill_id), VoidBillRequest(reason="TES E2E koreksi"), Response()))
                h["ap4"] = await ap()
                h["sa_baru_lagi"] = await panggil(sa)
                h["ap5"] = await ap()
    finally:
        await tx.rollback()
    h["jaring_sesudah"] = await jaring()
    await c.close()
    open(KELUAR, "w").write(json.dumps(h, sort_keys=True, indent=1, default=str))
    print("SELESAI", json.dumps({k: (list(v)[0] if isinstance(v, dict) and len(v) == 1 else v) for k, v in h.items()
                                 if k.startswith(("balik", "sa_", "void", "um_", "ap", "pratinjau"))}, default=str))


asyncio.run(main())
