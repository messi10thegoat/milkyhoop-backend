"""UJI NYATA Pesanan Pembelian sisi TULIS (22 Okt 2026, P-U2): BACA-SAJA. Mode: baru [out.json]
 A. po_hitung (Python Decimal HALF_UP) == PostgreSQL numeric ROUND (independen) pada 400 kasus acak (baris + header, diskon %/nominal, pajak).
 B. qty tertagih (po_daftar.SQL_QTY_TAGIH) pada data SINTETIS (CTE yang menutupi bill_items/bills): bill void tak ikut, draf ikut, banyak bill, tenant lain tak ikut.
 C. info skema nyata: trigger trg_update_po_totals (ada = V435 belum diterapkan), kolom bill_items.purchase_order_item_id (ada = V436 diterapkan)."""
import asyncio, os, random, sys, uuid
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "boneka")
from decimal import Decimal
import asyncpg
from app.services import po_daftar as PD, po_hitung as PH

gagal = []
D = Decimal


def cek(nama, ok, rinci=""):
    print(("PASS " if ok else "FAIL ") + nama + ("" if ok else f"  {str(rinci)[:200]}"))
    if not ok: gagal.append(nama)


async def a_hitung(c):
    rnd = random.Random(20261022)
    salah = []
    for n in range(400):
        qty = D(rnd.choice(["1", "2", "3", "7", "2.5", "0.125", "12.345", "100"]))
        harga = D(rnd.randint(1, 5_000_000)) / D(rnd.choice([1, 4, 100, 1000]))
        harga = harga.quantize(D("0.01"))
        pct = D(rnd.choice([0, 0, 5, 10, 12.5, 33.33]))
        nom = D(rnd.choice([0, 0, 0, 500, 12345.67]))
        tarif = D(rnd.choice([0, 0, 11, 12]))
        b = PH.hitung_baris({"quantity": qty, "unit_price": harga, "discount_percent": pct, "discount_amount": nom, "tax_rate": tarif})
        r = await c.fetchrow("""
            WITH x AS (SELECT ROUND($1::numeric * $2::numeric, 2) AS sub),
                 y AS (SELECT sub, LEAST(CASE WHEN $3::numeric > 0 THEN ROUND(sub * $3::numeric / 100, 2) ELSE ROUND($4::numeric, 2) END, sub) AS dsk FROM x)
            SELECT sub, dsk, CASE WHEN $5::numeric > 0 THEN ROUND((sub - dsk) * $5::numeric / 100, 2) ELSE 0 END AS pjk FROM y""", qty, harga, pct, nom, tarif)
        if (b["subtotal"], b["discount_amount"], b["tax_amount"]) != (r["sub"], r["dsk"], r["pjk"]) or b["total"] != r["sub"] - r["dsk"] + r["pjk"]:
            salah.append((str(qty), str(harga), str(pct), str(nom), str(tarif)))
    cek("hitung_baris Python == PostgreSQL ROUND (400 kasus acak)", not salah, salah[:2])
    salah = []
    for n in range(200):
        baris = [PH.hitung_baris({"quantity": D(rnd.randint(1, 20)), "unit_price": D(rnd.randint(100, 900000)) / 100, "discount_percent": D(rnd.choice([0, 5, 10])),
                                  "tax_rate": D(rnd.choice([0, 11]))}) for _ in range(rnd.randint(1, 4))]
        pct, nom, tarif = D(rnd.choice([0, 0, 10, 2.5])), D(rnd.choice([0, 0, 1500])), D(rnd.choice([0, 0, 11]))
        h = PH.hitung_header(baris, pct, nom, tarif)
        sub = sum((b["subtotal"] - b["discount_amount"] for b in baris), D(0))
        tp = sum((b["tax_amount"] for b in baris), D(0))
        r = await c.fetchrow("""
            WITH s AS (SELECT $1::numeric AS sub, $2::numeric AS tp),
                 d AS (SELECT sub, tp, LEAST(CASE WHEN $3::numeric > 0 THEN ROUND(sub * $3::numeric / 100, 2) ELSE ROUND($4::numeric, 2) END, sub) AS dsk FROM s)
            SELECT sub, dsk, CASE WHEN $5::numeric > 0 THEN ROUND((sub - dsk) * $5::numeric / 100, 2)
                                  ELSE (CASE WHEN sub > 0 THEN ROUND(tp * (sub - dsk) / sub, 2) ELSE 0 END) END AS pjk FROM d""", sub, tp, pct, nom, tarif)
        if (h["subtotal"], h["discount_amount"], h["tax_amount"]) != (r["sub"], r["dsk"], r["pjk"]) or h["total_amount"] != r["sub"] - r["dsk"] + r["pjk"]:
            salah.append((str(pct), str(nom), str(tarif)))
    cek("hitung_header Python == PostgreSQL ROUND (200 kasus acak)", not salah, salah[:2])


def vals(nama, kolom, baris):
    if not baris:
        return f"{nama} AS (SELECT {', '.join(f'NULL::{t} AS {col}' for col, t in kolom)} WHERE false)"
    def lit(v, t):
        if v is None: return f"NULL::{t}"
        return f"'{v}'::{t}" if t in ("uuid", "text") else f"{v}::{t}"
    return f"{nama} AS (SELECT * FROM (VALUES {', '.join('(' + ', '.join(lit(r[col], t) for col, t in kolom) + ')' for r in baris)}) v({', '.join(col for col, _ in kolom)}))"


async def b_tagih(c):
    T = "tenant-uji"
    i1, i2, i3 = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    bills, bi = [], []
    def bill(status_v2, status, tenant=T):
        b = str(uuid.uuid4()); bills.append(dict(id=b, tenant_id=tenant, status_v2=status_v2, status=status)); return b
    def baris(bid, item, qty): bi.append(dict(bill_id=bid, purchase_order_item_id=item, quantity=qty))
    baris(bill("posted", "posted"), i1, 4)
    baris(bill("draft", "draft"), i1, 3)                 # draf IKUT
    baris(bill("void", "void"), i1, 99)                  # void TIDAK
    baris(bill(None, "void"), i1, 77)                    # status_v2 NULL -> pakai status (void) TIDAK
    baris(bill("posted", "posted", tenant="tenant-lain"), i1, 55)   # tenant lain TIDAK
    baris(bill("posted", "posted"), i2, 10)
    cte = "WITH " + ", ".join([
        vals("purchase_order_items", [("id", "uuid")], [{"id": i1}, {"id": i2}, {"id": i3}]),
        vals("bill_items", [("bill_id", "uuid"), ("purchase_order_item_id", "uuid"), ("quantity", "numeric")], bi),
        vals("bills", [("id", "uuid"), ("tenant_id", "text"), ("status_v2", "text"), ("status", "text")], bills)]) + " "
    rows = {str(r["id"]): r["q"] for r in await c.fetch(cte + f"SELECT i.id, {PD.SQL_QTY_TAGIH} AS q FROM purchase_order_items i WHERE $1::text IS NOT NULL", "x", T)}
    cek("sintetis: qty tertagih = Σ bill tak-void bertaut (draf ikut; void, status_v2 NULL+void & tenant lain tidak)", rows == {i1: D(7), i2: D(10), i3: D(0)}, rows)


async def c_info(c):
    trg = await c.fetchval("SELECT count(*) FROM pg_trigger WHERE tgname='trg_update_po_totals' AND NOT tgisinternal")
    kol = await c.fetchval("SELECT count(*) FROM information_schema.columns WHERE table_name='bill_items' AND column_name='purchase_order_item_id'")
    print(f"INFO skema: trg_update_po_totals={'ADA (V435 belum)' if trg else 'TIDAK ADA (V435 terapan)'} · bill_items.purchase_order_item_id={'ADA (V436 terapan)' if kol else 'TIDAK ADA (V436 belum)'}")


async def main():
    c = await asyncpg.connect(os.environ["URL"]); await c.execute("SET default_transaction_read_only = on")
    await a_hitung(c); await b_tagih(c); await c_info(c)
    print("RINGKAS PO-TULIS gagal=%d %s" % (len(gagal), gagal[:4]))
    await c.close(); sys.exit(1 if gagal else 0)

asyncio.run(main())
