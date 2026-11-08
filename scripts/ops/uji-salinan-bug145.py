"""BUG-145 / ROW (u) -- uji di SALINAN (klon milkydb): fungsi ASLI untuk 6 titik "Sisa" SO, dari pohon LAMA dan BARU di salinan yang SAMA
(keluaran JSON per pohon; banding-bug145.py). Per SO tenant (bukan draf): daftar (fakta_daftar posisi + outstanding_amount), sisa_per_so
(sort/Σ), detail payment_summary.remaining_amount, Rekap (status/sisa), bundel dokumen (balance/position_text) + ringkasan_pesanan mentah."""
import asyncio
import json
import os
import sys

from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import dokumen as RD, sales_orders as RS  # noqa: E402
from app.services import so_posisi as SP  # noqa: E402
from app.services.proforma_terbayar import ringkasan_pesanan  # noqa: E402
from app.services.rekap_pesanan import muat_rekap_pesanan  # noqa: E402
from app.utils.tanggal_tenant import tanggal_dokumen  # noqa: E402

TENANT = tuple(os.environ.get("TENANT", "kaos-biru-konveksi,pt-kita-nyawit-bersama").split(","))
SQL_SO = """SELECT id, order_number, status, total_amount, expected_ship_date, quote_id, fase_lokasi, fase_gudang_id, dp_percent, dp_amount
            FROM sales_orders WHERE tenant_id = $1 AND status <> 'draft' ORDER BY order_number"""


def req(tid):
    r = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    r.state.user = {"tenant_id": tid, "user_id": "00000000-0000-0000-0000-000000000001", "username": "uji-salinan-bug145"}
    return r


def s(v):
    return None if v is None else f"{float(v):.2f}"


async def utama():
    pool = await RS.get_pool()
    out = {}
    async with pool.acquire() as conn:
        db = await conn.fetchval("SELECT current_database()")
        if not db.startswith("scratch_b145_"):
            sys.exit(f"BUKAN salinan: {db}")
        for tid in TENANT:
            ctx = {"tenant_id": tid, "user_id": "00000000-0000-0000-0000-000000000001"}
            rows = [dict(r) for r in await conn.fetch(SQL_SO, tid)]
            hari = await tanggal_dokumen(conn, tid)
            fakta = await SP.fakta_daftar(conn, tid, rows, hari)
            sisa = await SP.sisa_per_so(conn, tid, rows)
            ring = await ringkasan_pesanan(conn, tid, [r["id"] for r in rows])
            per = {}
            for r in rows:
                f = fakta[str(r["id"])]
                det = (await RS.get_sales_order_detail(req(tid), str(r["id"])))
                det = det.get("data", det) if isinstance(det, dict) else det.model_dump()
                det = det.get("data", det) if isinstance(det, dict) and "data" in det else det
                ps = (det.get("payment_summary") or {}) if isinstance(det, dict) else {}
                rk = (await muat_rekap_pesanan(conn, ctx, r["id"]))["rekap"]
                dk = (await RD.susun_dokumen(conn, ctx, r["id"]))["so"]
                o = ring[r["id"]]
                per[r["order_number"]] = {
                    "status": r["status"], "total": s(r["total_amount"]), "invoiced": s(o["invoiced"]), "tertutup": s(o["tertutup"]),
                    "posisi": f["position_text"], "sisa_daftar": s(f["outstanding_amount"]), "sisa_per_so": s(sisa[r["id"]]),
                    "remaining": s(ps.get("remaining_amount")), "rekap_status": rk["status"], "rekap_sisa": s(rk["sisa"]),
                    "dok_balance": dk["balance"], "dok_posisi": dk["position_text"]}
            out[tid] = per
    json.dump(out, open(os.environ["KELUAR"], "w"), sort_keys=True, indent=1)
    print("[info] " + ", ".join(f"{t}={len(v)} SO" for t, v in out.items()), flush=True)

asyncio.run(utama())
