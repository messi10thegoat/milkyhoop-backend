"""ROW (v) -- uji di SALINAN (klon milkydb): handler ASLI GRN (daftar, summary, detail, bundel dokumen) + NK (daftar, detail, bundel dokumen),
pohon LAMA dan BARU di salinan yang SAMA -> JSON per pohon (banding-rov-v.py: hanya perubahan yang diputuskan)."""
import asyncio
import json
import os
import sys

from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import credit_notes as RC, dokumen as RD, goods_receipts as RG  # noqa: E402

TENANT = tuple(os.environ.get("TENANT", "kaos-biru-konveksi,pt-kita-nyawit-bersama").split(","))


def req(tid):
    r = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    r.state.user = {"tenant_id": tid, "user_id": "00000000-0000-0000-0000-000000000001", "username": "uji-salinan-rov-v"}
    return r


def j(v):
    return json.loads(json.dumps(v, default=lambda o: o.model_dump() if hasattr(o, "model_dump") else str(o)))


async def utama():
    pool = await RG.get_pool()
    out = {}
    async with pool.acquire() as conn:
        db = await conn.fetchval("SELECT current_database()")
        if not db.startswith("scratch_rov_v_"):
            sys.exit(f"BUKAN salinan: {db}")
        for tid in TENANT:
            ctx = {"tenant_id": tid, "user_id": "00000000-0000-0000-0000-000000000001"}
            o = out[tid] = {}
            d = (await RG.list_goods_receipts(req(tid), purchase_order_id=None, status=None, aksi=None, limit=200, offset=0, search=None,
                                              date_from=None, date_to=None, sort_by="receipt_date", sort_order="desc"))["data"]
            o["grn_daftar"] = {g["receipt_number"]: j(g) for g in d["items"]}
            o["grn_summary"] = j((await RG.goods_receipts_summary(req(tid), purchase_order_id=None, search=None))["data"])
            for g in d["items"]:
                o[f"grn_detail:{g['receipt_number']}"] = j((await RG.get_goods_receipt(req(tid), RG.UUID(g["id"])))["data"])
                o[f"grn_dok:{g['receipt_number']}"] = j(await RG.get_goods_receipt_documents(req(tid), RG.UUID(g["id"])))
            nk = await conn.fetch("SELECT id, credit_note_number FROM credit_notes WHERE tenant_id = $1 ORDER BY credit_note_number", tid)
            daftar = j(await RC.list_credit_notes(req(tid), status="all", customer_id=None, search=None, date_from=None, date_to=None, skip=0,
                                                   limit=100, sort_by="created_at", sort_order="desc", aksi=None, with_totals=False))
            o["nk_daftar"] = {x["credit_note_number"]: x for x in (daftar.get("items") or daftar.get("data", {}).get("items") or [])}
            for n in nk:
                o[f"nk_detail:{n['credit_note_number']}"] = j(await RC.get_credit_note(req(tid), n["id"]))
                o[f"nk_dok:{n['credit_note_number']}"] = j(await RD.susun_dokumen_nota_kredit(conn, ctx, n["id"]))
    json.dump(out, open(os.environ["KELUAR"], "w"), sort_keys=True, indent=1)
    print("[info] " + ", ".join(f"{t}: {len(v['grn_daftar'])} GRN, {len(v['nk_daftar'])} NK" for t, v in out.items()), flush=True)

asyncio.run(utama())
