"""UJI NYATA Penawaran / void Diterima (22 Okt 2026): BACA-SAJA. Mode: baru [out.json]
Untuk SEMUA penawaran (kaos+grapgrap): penentu `quotes._rencana_void_penawaran` (alasan diberikan) == SQL/Python INDEPENDEN dari baris mentah:
  void sudah -> QUOTE_SUDAH_BATAL; converted -> QUOTE_SUDAH_DIKONVERSI (bila turunan ketemu) / QUOTE_STATUS_TAK_BOLEH_BATAL;
  declined -> QUOTE_STATUS_TAK_BOLEH_BATAL; draft|sent|accepted TANPA pesanan/faktur turunan -> boleh; accepted DENGAN turunan -> QUOTE_SUDAH_DIKONVERSI.
  Tanpa alasan: accepted -> +ALASAN_WAJIB, draft/sent -> tidak. (Blok uang muka dibandingkan terpisah.)"""
import asyncio, os, sys
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "boneka")
import asyncpg
from app.routers import quotes as Q

gagal, dangling = [], []


def cek(nama, ok, rinci=""):
    if not ok:
        print("FAIL " + nama + f"  {str(rinci)[:200]}"); gagal.append(nama)


async def main():
    c = await asyncpg.connect(os.environ["URL"]); await c.execute("SET default_transaction_read_only = on")
    rows = await c.fetch("SELECT id, tenant_id, status, quote_number, converted_to_type, converted_to_id FROM quotes ORDER BY tenant_id, quote_number")
    n = {"boleh": 0, "diblok": 0}
    for q in rows:
        t = q["tenant_id"]
        so = await c.fetchval("SELECT count(*) FROM sales_orders WHERE tenant_id=$1 AND quote_id=$2 AND status<>'cancelled'", t, q["id"])
        fk = await c.fetchval("SELECT count(*) FROM sales_invoices WHERE tenant_id=$1 AND quote_id=$2 AND status<>'void'", t, q["id"])
        turunan = bool(so or fk)
        target = None
        if q["status"] == "converted" and q["converted_to_id"]:
            tab, kol = ("sales_orders", "order_number") if q["converted_to_type"] in ("sales_order", "order", "so") else ("sales_invoices", "invoice_number")
            target = await c.fetchval(f"SELECT {kol} FROM {tab} WHERE tenant_id=$1 AND id=$2", t, q["converted_to_id"])
            if not target and not turunan: dangling.append(q["quote_number"])
        for alasan in ("alasan uji", None):
            blok = await Q._rencana_void_penawaran(c, t, q, alasan)
            kode = [b["code"] for b in blok if b["code"] != "QUOTE_ADA_UANG_MUKA"]
            st = q["status"]
            if st == "void": harap = ["QUOTE_SUDAH_BATAL"]
            elif st == "converted": harap = ["QUOTE_SUDAH_DIKONVERSI"] if (turunan or target) else ["QUOTE_STATUS_TAK_BOLEH_BATAL"]
            elif st in ("draft", "sent", "accepted"):
                harap = (["QUOTE_SUDAH_DIKONVERSI"] if (st == "accepted" and turunan) else [])
                if st == "accepted" and not (alasan or "").strip(): harap = harap + ["ALASAN_WAJIB"]
            else: harap = ["QUOTE_STATUS_TAK_BOLEH_BATAL"]
            cek(f"{t} {q['quote_number']} [{st}] alasan={'ya' if alasan else 'tidak'}", kode == harap, (kode, harap))
            if alasan:
                n["boleh" if not kode else "diblok"] += 1
    print(f"INFO {len(rows)} penawaran: boleh dibatalkan={n['boleh']} diblok={n['diblok']} (accepted tanpa turunan = kandidat tombol baru)")
    print(f"INFO converted dengan target TAK ADA (penunjuk menggantung; dilaporkan, tak diubah): {dangling}")
    print("RINGKAS PENAWARAN-VOID gagal=%d %s" % (len(gagal), gagal[:4]))
    await c.close(); sys.exit(1 if gagal else 0)

asyncio.run(main())
