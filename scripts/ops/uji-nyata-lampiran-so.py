"""Harness NYATA baca-saja LAMPIRAN-SO (W-20261013). Pool READ ONLY (default_transaction_read_only=on):
tulis apa pun -> galat. Handler & PolicyEngine NYATA, satu objek modul (backend.api_gateway.app.*)."""
import asyncio
import os
import sys
from types import SimpleNamespace
from uuid import UUID

sys.path.insert(0, "/app")
import asyncpg  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from backend.api_gateway.app.services import policy_engine_client as pec  # noqa: E402
from backend.api_gateway.app.routers import sales_order_lampiran as SL  # noqa: E402
from backend.api_gateway.app.routers import customer_deposits as CD  # noqa: E402
from backend.api_gateway.app.routers import receive_payments as RP  # noqa: E402
from backend.api_gateway.app.routers import sales_invoices as SI  # noqa: E402

GG, KAOS = "grapgrap-manado", "kaos-biru-konveksi"
SO19 = UUID("31ee6038-074c-428e-8408-18d404d9fbb1")
DP15 = UUID("f766f4b4-a09f-4a92-9aed-7319d6ab25f0")
gagal = 0


def cek(ok, label, info=""):
    global gagal
    print(("PASS " if ok else "FAIL ") + label + (f" | {info}" if info else ""))
    if not ok:
        gagal += 1


def req(user_id, tenant):
    return SimpleNamespace(state=SimpleNamespace(user={"user_id": user_id, "tenant_id": tenant, "role": "ADMIN"}))


async def isi(resp):
    buf = b""
    async for ch in resp.body_iterator:
        buf += ch if isinstance(ch, bytes) else ch.encode()
    return buf


async def main():
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=3,
                                     server_settings={"default_transaction_read_only": "on"})

    async def gp():
        return pool

    for m in (SL, CD, RP, SI):
        m.get_pool = gp
    pec.init_policy_engine(pool)

    async with pool.acquire() as c:
        ro = await c.fetchval("SHOW default_transaction_read_only")
        cek(ro == "on", "kontrol: pool READ ONLY")
        try:
            async with c.transaction():
                await c.execute("CREATE TEMP TABLE _x(i int)")
            cek(False, "kontrol: tulis ditolak")
        except Exception as e:  # noqa: BLE001
            cek("read-only" in str(e), "kontrol: tulis ditolak", type(e).__name__)
        pemilik = {}
        for t in (GG, KAOS):
            pemilik[t] = str(await c.fetchval(
                "SELECT utr.user_id FROM user_tenant_roles utr JOIN roles r ON r.id = utr.role_id "
                "WHERE utr.tenant_id = $1 AND r.code = 'OWNER' AND r.is_active ORDER BY utr.is_primary DESC LIMIT 1", t))
        # SO lain: paling banyak jenis grup (penerimaan/faktur)
        so_kaya = await c.fetchval("""
            SELECT si.sales_order_id FROM receive_payment_allocations a
            JOIN sales_invoices si ON si.id = a.invoice_id AND si.tenant_id = $1
            JOIN document_attachments da ON da.entity_type = 'payment' AND da.entity_id = a.payment_id AND da.tenant_id = $1
            WHERE a.tenant_id = $1 AND si.sales_order_id IS NOT NULL
              AND EXISTS (SELECT 1 FROM sales_invoice_attachments sa WHERE sa.invoice_id = si.id AND sa.tenant_id = $1)
            LIMIT 1""", GG)
        so_kaya = so_kaya or await c.fetchval("""
            SELECT si.sales_order_id FROM receive_payment_allocations a
            JOIN sales_invoices si ON si.id = a.invoice_id AND si.tenant_id = $1
            JOIN document_attachments da ON da.entity_type = 'payment' AND da.entity_id = a.payment_id AND da.tenant_id = $1
            WHERE a.tenant_id = $1 AND si.sales_order_id IS NOT NULL LIMIT 1""", GG)
        so_lama = await c.fetchval("""
            SELECT si.sales_order_id FROM sales_invoice_attachments sa JOIN sales_invoices si ON si.id = sa.invoice_id
            WHERE sa.tenant_id = $1 AND si.tenant_id = $1 AND si.sales_order_id IS NOT NULL LIMIT 1""", GG)
    cek(all(pemilik.values()), "pemilik ditemukan", str({k: bool(v) for k, v in pemilik.items()}))

    # 1) SO-2610-0019: grup DP DEP-2610-0015 berisi IMG_1960.png
    out = await SL.lampiran_keluarga_so(req(pemilik[GG], GG), SO19)
    grup = [g for g in out["data"] if g["dokumen"].get("id") == str(DP15)]
    cek(len(grup) == 1 and grup[0]["dokumen"]["nomor"] == "DEP-2610-0015", "SO19: grup DP DEP-2610-0015",
        str([(g["dokumen"].get("jenis"), g["dokumen"].get("nomor"), len(g.get("lampiran", []))) for g in out["data"]]))
    lp = [x for x in grup[0]["lampiran"] if x["display_name"] == "IMG_1960.png"] if grup else []
    cek(len(lp) == 1 and lp[0]["tersedia"] and lp[0]["url"], "SO19: IMG_1960.png tersedia + url", lp[0]["url"] if lp else "")
    cek(out["jumlah"] >= 1 and not any(g.get("tersembunyi_izin") for g in out["data"]), "SO19 OWNER: tak ada grup tersembunyi")

    # 2) url /download -> handler modul DP NYATA -> stream PNG 948304 byte
    if lp:
        aid = lp[0]["id"]
        cek(lp[0]["url"] == f"/api/customer-deposits/{DP15}/attachments/{aid}/download", "url = rute download DP")
        r = await CD.download_deposit_attachment(req(pemilik[GG], GG), DP15, UUID(aid))
        b = await isi(r)
        cek(r.status_code == 200 and b[:8] == b"\x89PNG\r\n\x1a\n" and len(b) == 948304, "unduh IMG_1960.png 200 stream",
            f"status={r.status_code} byte={len(b)} media={r.media_type}")

    # 3) lintas tenant: pemilik kaos meminta SO grapgrap -> 404
    try:
        await SL.lampiran_keluarga_so(req(pemilik[KAOS], KAOS), SO19)
        cek(False, "lintas tenant 404")
    except HTTPException as e:
        cek(e.status_code == 404, "lintas tenant 404", str(e.status_code))

    # 4) SO dengan penerimaan (+ faktur lama bila ada): tiap url tersedia mengalir 200 lewat handler modulnya
    for so in [s for s in {so_kaya, so_lama} if s]:
        o = await SL.lampiran_keluarga_so(req(pemilik[GG], GG), so)
        jenis = sorted({g["dokumen"]["jenis"] for g in o["data"]})
        cek(o["jumlah"] > 0, f"SO {so}: ada lampiran", f"jenis={jenis} jumlah={o['jumlah']}")
        for g in o["data"]:
            for x in g["lampiran"]:
                if not x["tersedia"]:
                    cek(x["url"] is None, f"  {g['dokumen']['jenis']} tak tersedia -> url null")
                    continue
                _, _, modul, induk, _, aid, _ = x["url"].split("/")
                h = {"customer-deposits": CD.download_deposit_attachment, "receive-payments": RP.download_payment_attachment,
                     "sales-invoices": SI.download_invoice_attachment, "sales-orders": SL.unduh_lampiran_so}[modul]
                try:
                    r = await h(req(pemilik[GG], GG), UUID(induk), UUID(aid))
                    b = await isi(r)
                    cek(r.status_code == 200 and len(b) > 0, f"  unduh {g['dokumen']['jenis']} {g['dokumen']['nomor']} {x['display_name']}", f"{len(b)} byte")
                except HTTPException as e:
                    cek(False, f"  unduh {g['dokumen']['jenis']} {g['dokumen']['nomor']} {x['display_name']}", f"HTTP {e.status_code} {e.detail}")

    await pool.close()
    print(f"RINGKAS gagal={gagal}")


asyncio.run(main())
