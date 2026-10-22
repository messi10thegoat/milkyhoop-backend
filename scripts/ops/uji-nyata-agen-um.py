"""UJI NYATA agen chat create_vendor_deposit (22 Okt 2026, MASTER): payload yang DIRAKIT AGEN -> POST -> posted + jurnal == pratinjau.
DB SCRATCH (dump terbaru; dibuang sesudahnya). Jalur agen ditiru dengan fungsi registri ASLI:
  validate_payload (medan wajib kurang -> agen BERTANYA, bukan 422) -> apply_defaults -> buang display_only (aturan unified_chat.py
  confirm: {f.name for f in config.fields if f.display_only}) -> VendorDepositCreate -> pratinjau + tulis."""
import asyncio, os, sys, uuid
sys.path.insert(0, "/repo/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "boneka")
import asyncpg
from starlette.requests import Request

from app.routers import vendor_deposits as VD
from app.schemas import vendor_deposits as VS
from app.services.unified_agent.direct_action_registry import DIRECT_ACTIONS, apply_defaults, validate_payload

gagal = []


def cek(nama, ok, rinci=""):
    print(("PASS " if ok else "FAIL ") + nama + ("" if ok else f"  {str(rinci)[:300]}"))
    if not ok: gagal.append(nama)


def req(tid, uid):
    async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
    r = Request({"type": "http", "method": "POST", "path": "/", "headers": [], "query_string": b""}, receive)
    r.state.user = {"tenant_id": tid, "user_id": uid, "role": "OWNER"}
    return r


async def baris(c, tid, jid):
    return [(r["account_code"], float(r["debit"]), float(r["credit"])) for r in await c.fetch(
        """SELECT coa.account_code, jl.debit, jl.credit FROM journal_lines jl JOIN chart_of_accounts coa ON coa.id = jl.account_id AND coa.tenant_id = $2
           WHERE jl.journal_id = $1 ORDER BY jl.line_number""", jid, tid)]


async def main():
    c = await asyncpg.connect(os.environ["URL"])
    class Pool:
        def acquire(s):
            class CM:
                async def __aenter__(x): return c
                async def __aexit__(x, *a): return False
            return CM()
    async def gp(): return Pool()
    VD.get_pool = gp
    tid = "kaos-biru-konveksi"
    v = await c.fetchrow("SELECT id, name FROM vendors WHERE tenant_id = $1 ORDER BY created_at LIMIT 1", tid)
    ba = await c.fetchrow("SELECT id, account_name FROM bank_accounts WHERE tenant_id = $1 AND coa_id IS NOT NULL ORDER BY created_at LIMIT 1", tid)
    uid = str(await c.fetchval("SELECT created_by FROM sales_orders WHERE tenant_id = $1 AND created_by IS NOT NULL LIMIT 1", tid) or uuid.uuid4())
    cek("scratch: vendor + rekening kaos ada", bool(v and ba))
    hari = (await VD.tanggal_dokumen(c, tid)).isoformat()
    # bentuk yang dirakit agen (resolver mengisi id + nama tampilan; LLM mengisi jumlah/tanggal/referensi)
    agen = {"vendor_id": str(v["id"]), "vendor_name": v["name"], "bank_account_id": str(ba["id"]), "bank_account_name": ba["account_name"],
            "amount": 1500.5, "deposit_date": hari, "reference": "TES E2E agen"}
    ok, kurang = validate_payload("create_vendor_deposit", {k: x for k, x in agen.items() if k != "bank_account_id"})
    cek("medan wajib kurang (rekening) -> agen BERTANYA (validate_payload False), bukan 422", ok is False and kurang, kurang)
    ok, kurang = validate_payload("create_vendor_deposit", agen)
    cek("payload agen lengkap lolos validate_payload", ok is True, kurang)
    p = apply_defaults("create_vendor_deposit", agen)
    cfg = DIRECT_ACTIONS["create_vendor_deposit"]
    bersih = {k: x for k, x in p.items() if k not in {f.name for f in cfg.fields if f.display_only}}
    cek("payload agen membawa simpan_dan_posting=True", bersih.get("simpan_dan_posting") is True, bersih)
    body = VS.VendorDepositCreate(**bersih)
    pr = (await VD.preview_create_vendor_deposit(req(tid, uid), body))["data"]
    cek("pratinjau payload agen: can_proceed, status sesudah posted", pr["can_proceed"] and pr["after"]["status"] == "posted", pr["blocks"])
    hasil = await VD.create_vendor_deposit(req(tid, uid), VS.VendorDepositCreate(**bersih))
    vd = await c.fetchrow("SELECT status, journal_id, amount FROM vendor_deposits WHERE id = $1", uuid.UUID(str(hasil["id"])))
    cek("POST payload agen: status posted + jurnal ada", vd["status"] == "posted" and vd["journal_id"] is not None, dict(vd))
    rencana = [(l["account_code"], l["debit"], l["credit"]) for l in pr["journals"][0]["lines"]]
    cek("jurnal tertulis == rencana pratinjau", await baris(c, tid, vd["journal_id"]) == rencana, (await baris(c, tid, vd["journal_id"]), rencana))
    cek("nominal bersen tersimpan utuh", float(vd["amount"]) == 1500.5, vd["amount"])
    await c.close()
    print(f"SELESAI gagal={len(gagal)}")
    sys.exit(1 if gagal else 0)


asyncio.run(main())
