import base64, os, sys
sys.path.insert(0, "/app/backend/api_gateway"); sys.path.insert(0, "/w/scripts")
import asyncio, asyncpg
from app.services.pdf_service import get_pdf_service
from gate_tpl import konteks

TENANT = "adhita-ariyani"
LOGO_DIR = "/logos"

def logo_data(nama):
    if not nama:
        return None
    p = os.path.join(LOGO_DIR, nama)
    if not os.path.exists(p):
        print(f"  ! logo {nama} tak ada di {LOGO_DIR}")
        return None
    return "data:image/png;base64," + base64.b64encode(open(p, "rb").read()).decode()

async def main():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"]); svc = get_pdf_service()
    row = await conn.fetchrow(
        "SELECT id, invoice_number FROM sales_invoices WHERE invoice_number='INV-2609-0001' AND tenant_id=$1", TENANT)
    ctx = await konteks(conn, row["id"])
    # NPWP pelanggan: faktur menang, kartu pelanggan mengisi kekosongan --
    # sama persis dengan rute PDF.
    if not ctx.get("customer_npwp"):
        ctx["customer_npwp"] = await conn.fetchval(
            "SELECT c.tax_id FROM customers c JOIN sales_invoices si ON si.customer_id=c.id WHERE si.id=$1", row["id"])
    ctx["tenant"] = dict(ctx["tenant"], logo_data=logo_data(ctx["tenant"]["logo_url"]))
    print(f"faktur: {row['invoice_number']} ({TENANT})")
    print(f"  alamat pelanggan: {ctx['customer_address']!r}")
    print(f"  telepon         : {ctx['customer_phone']!r}")
    print(f"  NPWP            : {ctx['customer_npwp']!r}")
    print(f"  is_pkp          : {ctx['tenant']['is_pkp']}")
    # (a) apa adanya: medan V239 masih kosong -> baris Workshop TIDAK dicetak
    open("/w/contoh-almantek.pdf", "wb").write(svc.generate_sales_invoice_pdf(ctx, template="b"))
    print(f"  (a) apa adanya      : {os.path.getsize('/w/contoh-almantek.pdf')} byte")
    # (b) seandainya medan V239 diisi -- DISUNTIK ke konteks render saja,
    #     basis data TIDAK disentuh.
    ctx_isi = dict(
        ctx,
        payment_bank_branch="KCP TEMANGGUNG",
        payment_bank_address="Jl. R. Suprapto No.21, Kauman, Temanggung II, Temanggung, Temanggung Regency, Central Java 56200",
    )
    # Alamat Head Office DIPENDEKKAN: sesudah medan workshop_address ada,
    # pemilik tak perlu lagi menjejalkan teks Workshop ke dalam `address`.
    # Tanpa ini contohnya mencetak Workshop DUA KALI dan tak mewakili keadaan
    # sesudah medannya dipakai.
    ctx_isi["tenant"] = dict(ctx["tenant"],
        address="Banyuurip Timur RT 02/RW04 No.258 Kel. Banyuurip Kec. Temanggung Kab.Temanggung Jawa Tengah 56211",
        workshop_address="Ruko Perumahan Bumi Cikarang Makmur Blok D2 No. 3-5 Kel. Sukadami Kec. Cikarang Selatan Bekasi Jawa Barat 17550",
        signatory_name="Vitus Dwi Nugroho W.")
    open("/w/contoh-almantek-terisi.pdf", "wb").write(svc.generate_sales_invoice_pdf(ctx_isi, template="b"))
    print(f"  (b) medan V239 diisi: {os.path.getsize('/w/contoh-almantek-terisi.pdf')} byte")
    await conn.close()
asyncio.run(main())
