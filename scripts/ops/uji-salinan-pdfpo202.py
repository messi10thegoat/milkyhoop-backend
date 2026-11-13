"""BUG-202 uji DB di SALINAN (bukan prod): render PDF PO dgn kode ASLI (muat_pdf_po + render_purchase_order + tulis_pdf) atas klon, tenant kaos + nyawit. Mutasi HANYA di klon.
A PO apa adanya: label "Tanggal kirim" (bukan "Jadwal terima"), kode barang cadangan = SKU master (kolom Kode bila ada), syarat bayar == termin vendor di DB, petunjuk kop muncul HANYA bila profil tanpa alamat dan NPWP
· B PO + profil DIISI lengkap (di klon): kop alamat+NPWP tercetak, petunjuk hilang, pajak per baris "Pajak 11% · Rp 1.100" + baris pajak ringkasan, alamat kirim · C tulis_pdf menghasilkan PDF sah (%PDF) untuk A dan B."""
import asyncio
import re
import sys
from datetime import date, timedelta

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import purchase_orders as PO  # noqa: E402
from app.services.pdf_service import PDFService  # noqa: E402
from app.services.po_cetak import muat_pdf_po  # noqa: E402

NY, KA = "pt-kita-nyawit-bersama", "kaos-biru-konveksi"
HASIL = []


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


def teks(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", re.sub(r"<style.*?</style>", "", html, flags=re.S))).strip()


async def render(c, ps, tid, po_id):
    m = await muat_pdf_po(c, {"tenant_id": tid}, po_id)
    r = ps.render_purchase_order(m["po_data"], m["tenant_info"])
    return r.html, m["po_data"], r


async def main():
    pool = await PO.get_pool()
    ps = PDFService()
    async with pool.acquire() as c:
        for tid in (KA, NY):
            po = await c.fetchrow("SELECT id, po_number, vendor_id, expected_date FROM purchase_orders WHERE tenant_id = $1 AND vendor_id IS NOT NULL AND expected_date IS NOT NULL ORDER BY created_at DESC LIMIT 1", tid)
            cek(f"prasyarat {tid}: PO berpemasok + tanggal kirim ada", po is not None)
            if po is None:
                continue
            tn = await c.fetchrow('SELECT address, tax_id FROM "Tenant" WHERE id = $1 /* tenant_id = id */', tid)
            termin = await c.fetchval("SELECT payment_terms_days FROM vendors WHERE id = $1 AND tenant_id = $2", po["vendor_id"], tid)
            # kode barang: barang PO tanpa item_code tapi master ber-SKU; bila tak ada di klon, SKU master diisi di klon
            baris = await c.fetchrow("SELECT i.id, i.item_id FROM purchase_order_items i JOIN purchase_orders p ON p.id = i.po_id AND p.tenant_id = $2 WHERE i.po_id = $1 AND i.item_id IS NOT NULL ORDER BY i.line_number LIMIT 1", po["id"], tid)
            sku = "BRG-UJI202"
            if baris is not None:
                await c.execute("UPDATE purchase_order_items i SET item_code = NULL FROM purchase_orders p WHERE p.id = i.po_id AND p.tenant_id = $2 AND i.id = $1", baris["id"], tid)
                await c.execute("UPDATE products SET item_code = $3 WHERE tenant_id = $1 AND id = $2", tid, baris["item_id"], sku)
            hA, dA, rA = await render(c, ps, tid, po["id"])
            tA = teks(hA)
            cek(f"A {tid} label 'Tanggal kirim' (bukan 'Jadwal terima')", "Tanggal kirim" in tA and "Jadwal terima" not in tA)
            cek(f"A {tid} kode barang cadangan SKU master: kolom Kode + {sku}", baris is not None and ">Kode<" in hA and sku in tA, f"baris_berkode={baris is not None}")
            harap = None if termin is None else ("Tunai" if int(termin) <= 0 else f"{int(termin)} hari")
            cek(f"A {tid} syarat bayar == termin vendor di DB ({harap})", (harap is None and "Syarat bayar" not in tA) or (harap is not None and f"Syarat bayar {harap}" in tA), f"termin={termin}")
            kosong = not (tn["address"] or tn["tax_id"])
            cek(f"A {tid} petunjuk kop: {'muncul' if kosong else 'tak muncul'} (profil alamat={'ya' if tn['address'] else 'kosong'}, NPWP={'ya' if tn['tax_id'] else 'kosong'})", ('class="petunjuk-kop"' in hA) == kosong)
            css = hA.split("</style>", 1)[0]
            cek(f"A {tid} petunjuk kop disembunyikan di cetak (display:none, tampil hanya @media screen)", ".petunjuk-kop{display:none}" in css and "@media screen{.petunjuk-kop{display:block" in css)
            pdfA = ps.tulis_pdf(rA)
            # B diisi lengkap
            await c.execute('UPDATE "Tenant" SET address = $2, tax_id = $3, phone = $4 WHERE id = $1 /* tenant_id = id */', tid, "Jl. Uji Kop 1, Manado", "01.234.567.8-901.000", "0431 123456")
            await c.execute("UPDATE purchase_orders SET ship_to_address = $2, tax_rate = 11, tax_amount = GREATEST(tax_amount, 11000) WHERE tenant_id = $1 AND id = $3", tid, "Gudang Uji, Jl. Kirim 2", po["id"])
            await c.execute("UPDATE purchase_order_items i SET tax_rate = 11, tax_amount = 1100 FROM purchase_orders p WHERE p.id = i.po_id AND p.tenant_id = $2 AND i.po_id = $1", po["id"], tid)
            hB, dB, rB = await render(c, ps, tid, po["id"])
            tB = teks(hB)
            cek(f"B {tid} kop: alamat + NPWP tercetak, petunjuk hilang", "Jl. Uji Kop 1" in tB and "NPWP 01.234.567.8-901.000" in tB and 'class="petunjuk-kop"' not in hB)
            cek(f"B {tid} pajak per baris 'Pajak 11% · Rp 1.100' + baris pajak ringkasan", "Pajak 11% · Rp 1.100" in tB and re.search(r"Pajak 11%\s+Rp", tB) is not None, tB[tB.find("Pajak"):tB.find("Pajak") + 80])
            cek(f"B {tid} alamat kirim tercetak ('Kirim ke' + alamat)", "Kirim ke" in tB and "Gudang Uji, Jl. Kirim 2" in tB)
            pdfB = ps.tulis_pdf(rB)
            cek(f"C {tid} tulis_pdf menghasilkan PDF sah untuk A dan B", pdfA[:4] == b"%PDF" and pdfB[:4] == b"%PDF" and len(pdfA) > 1000 and len(pdfB) > 1000, f"{len(pdfA)} {len(pdfB)}")
    print(f"HASIL: {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    sys.exit(0 if all(HASIL) else 1)


asyncio.run(main())
