"""Uji SALINAN dua sisi V483 (BUG-098 nomor RCV-YYMM per bulan) + V484 (BUG-099(b) get_inventory_balance = Σ mutasi, DROP
calculate_weighted_average). Klon milkydb (bukan prod); berkas migrasi dijalankan apa adanya (psql -1 -v ON_ERROR_STOP=1, == apply_mig.sh)
oleh skrip .sh; skrip ini dipanggil per FASE: sebelum | sesudah | pembalik. Penghitung nomor diubah HANYA di salinan.
SEBELUM (kontrol merah): nomor RCV-<tahun>-NNNN; ada produk dgn snapshot != Σ mutasi; calculate_weighted_average ada.
SESUDAH: RCV-<YYMM>-0001 lalu 0002; cermin Python pratinjau == nomor berikut; ganti bulan -> 0001; nol nomor lama berubah;
         get_inventory_balance == Σ mutasi SEMUA produk; calculate_weighted_average hilang.
PEMBALIK: nomor kembali RCV-<tahun>-(maks+1) dan TIDAK bentrok nomor yang ada; snapshot kembali; calculate_weighted_average kembali."""
import asyncio
import re
import sys

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import receive_payments as RP  # noqa: E402

FASE = sys.argv[1]
TID = "pt-kita-nyawit-bersama"
HASIL = []

SQL_SELISIH = """SELECT count(*) FROM (SELECT DISTINCT tenant_id, product_id FROM inventory_ledger) p
                 WHERE get_inventory_balance(p.tenant_id, p.product_id)
                       <> (SELECT COALESCE(SUM(COALESCE(quantity_in,0) - COALESCE(quantity_out,0)), 0) FROM inventory_ledger l
                           WHERE l.tenant_id = p.tenant_id AND l.product_id = p.product_id)"""


def cek(nama, ok, info=""):
    HASIL.append(ok)
    print(("PASS " if ok else "FAIL ") + f"[{FASE}] {nama}" + (f" -- {info}" if info else ""), flush=True)


async def main():
    pool = await RP.get_pool()
    async with pool.acquire() as c:
        th, yymm = await c.fetchrow("SELECT to_char(t, 'YYYY'), to_char(t, 'YYMM') FROM tanggal_bisnis($1) t", TID)
        nomor_lama = await c.fetchval("SELECT count(*) FROM receive_payments WHERE payment_number ~ '^RCV-[0-9]{4}-'")
        ada_cwa = await c.fetchval("SELECT count(*) FROM pg_proc WHERE proname = 'calculate_weighted_average'")
        selisih = await c.fetchval(SQL_SELISIH)
        print(f"[{FASE}] th={th} yymm={yymm} nomor_lama_RCV-YYYY={nomor_lama} cwa={ada_cwa} produk_selisih={selisih}", flush=True)
        if FASE == "sebelum":
            n = await c.fetchval("SELECT generate_receive_payment_number($1)", TID)
            cek("kontrol merah: nomor lama RCV-<tahun>-NNNN", re.fullmatch(rf"RCV-{th}-\d{{4}}", n) is not None, n)
            cek("kontrol merah: ada produk snapshot != Σ mutasi", selisih > 0, str(selisih))
            cek("kontrol merah: calculate_weighted_average ada", ada_cwa == 1)
            print(f"NOMOR_LAMA={nomor_lama}", flush=True)
        elif FASE == "sesudah":
            pratinjau = await RP.nomor_rcv_pratinjau(c, TID)
            n1 = await c.fetchval("SELECT generate_receive_payment_number($1)", TID)
            n2 = await c.fetchval("SELECT generate_receive_payment_number($1)", TID)
            cek("bulan pertama mulai 0001 lalu 0002", (n1, n2) == (f"RCV-{yymm}-0001", f"RCV-{yymm}-0002"), f"{n1} {n2}")
            cek("cermin Python pratinjau == nomor berikut", pratinjau == n1, f"{pratinjau} vs {n1}")
            cek("cermin Python sesudah 2 nomor", await RP.nomor_rcv_pratinjau(c, TID) == f"RCV-{yymm}-0003")
            await c.execute("UPDATE receive_payment_sequences SET year_month = '2000-01' WHERE tenant_id = $1", TID)
            n3 = await c.fetchval("SELECT generate_receive_payment_number($1)", TID)
            cek("ganti bulan -> reset 0001", n3 == f"RCV-{yymm}-0001", n3)
            await c.execute("UPDATE receive_payment_sequences SET year_month = to_char(tanggal_bisnis($1), 'YYYY-MM'), last_number = 3 WHERE tenant_id = $1", TID)
            cek("nomor lama tak berubah", nomor_lama == int(sys.argv[2]), f"{nomor_lama} vs {sys.argv[2]}")
            cek("get_inventory_balance == Σ mutasi SEMUA produk", selisih == 0, str(selisih))
            cek("calculate_weighted_average dihapus", ada_cwa == 0)
        else:
            n = await c.fetchval("SELECT generate_receive_payment_number($1)", TID)
            ada = await c.fetchval("SELECT count(*) FROM receive_payments WHERE tenant_id = $1 AND payment_number = $2", TID, n)
            maks = await c.fetchval("""SELECT MAX(substring(payment_number FROM '^RCV-[0-9]{4}-([0-9]+)$')::int) FROM receive_payments
                                       WHERE tenant_id = $1 AND payment_number ~ ('^RCV-' || $2 || '-[0-9]+$')""", TID, th)
            cek("pembalik: nomor RCV-<tahun>-(maks+1), tak bentrok", n == f"RCV-{th}-{(maks or 0) + 1:04d}" and ada == 0, f"{n} maks={maks}")
            cek("pembalik: snapshot kembali (ada selisih)", selisih > 0, str(selisih))
            cek("pembalik: calculate_weighted_average kembali", ada_cwa == 1)
    print(f"HASIL[{FASE}]: {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    sys.exit(0 if all(HASIL) else 1)


asyncio.run(main())
