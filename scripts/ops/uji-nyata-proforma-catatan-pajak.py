"""UJI NYATA (BACA SAJA, tx READ ONLY): data cetak + HTML render proforma nyata per tenant -- catatan 'BUKAN faktur pajak'
hanya bila "Tenant".is_pkp TRUE. Kontrol merah: kode master."""
import asyncio, os, re, sys, uuid
sys.path.insert(0, "/app/backend/api_gateway")
import asyncpg

HASIL = []


def cek(n, ok, info=""):
    HASIL.append((n, bool(ok))); print(("PASS " if ok else "FAIL ") + n + (f" | {info}" if info else ""), flush=True)


async def main():
    import app.routers.proformas as PF
    from app.services import pdf_service as PS
    tangkap = {}

    class _HTML:
        def __init__(self, string=None, **kw):
            tangkap["isi"] = string

        def write_pdf(self, **kw):
            return b"%PDF"
    PS.HTML = _HTML
    c = await asyncpg.connect(os.environ["DATABASE_URL"])
    tr = c.transaction(readonly=True); await tr.start()
    try:
        rows = await c.fetch("""SELECT DISTINCT ON (p.tenant_id) p.tenant_id, p.id, p.proforma_number, t.is_pkp
                                FROM proformas p JOIN "Tenant" t ON t.id = p.tenant_id
                                WHERE p.status = 'issued' ORDER BY p.tenant_id, p.created_at DESC""")
        gg = await c.fetchrow("SELECT id, tenant_id, proforma_number FROM proformas WHERE tenant_id='grapgrap-manado' AND proforma_number='PRO-2610-0016'")
        daftar = [(r["tenant_id"], r["id"], r["proforma_number"], r["is_pkp"]) for r in rows]
        if gg and all(d[1] != gg["id"] for d in daftar):
            daftar.append((gg["tenant_id"], gg["id"], gg["proforma_number"], False))
        for tid, pid, nomor, pkp in daftar:
            m = await PF.muat_pdf_proforma_id(c, {"tenant_id": tid, "user_id": None}, pid)
            PS.get_pdf_service().generate_proforma_pdf(m["proforma_data"], m["tenant_info"])
            teks = " ".join(re.sub(r"<[^>]+>", " ", tangkap["isi"].split("<body>")[1]).split())
            ada = "BUKAN faktur pajak" in teks
            cek(f"{tid} {nomor} (is_pkp={pkp}): catatan pajak {'TAMPIL' if pkp else 'TIDAK tampil'}", ada == (pkp is True),
                f"catatan_ada={ada} flag={m['proforma_data'].get('catatan_bukan_faktur_pajak')}")
    finally:
        await tr.rollback()
    await c.close()
    g = [n for n, ok in HASIL if not ok]
    print(f"RINGKAS {len(HASIL)-len(g)}/{len(HASIL)}", "GAGAL: " + "; ".join(g) if g else "SEMUA LULUS")

asyncio.run(main())
