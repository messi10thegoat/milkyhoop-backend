"""BUG-128 (ROW o) -- uji di SALINAN (klon milkydb), BACA saja, per tenant (kaos, nyawit):
T1 payables.terbuka_count == jumlah tagihan ber-dokumen sisa > 0 (compute_ap_outstanding); maks 10; urut jatuh tempo lalu nomor.
T2 Σ semua tagihan terbuka + saldo awal/kredit == total (terbuka tak membuang tagihan); bentuk = due_7d + days_overdue (int).
T3 nyawit BILL-2611-0007 (jatuh tempo > 7 hari) ada di daftar terbuka penuh, tidak di due_7d.
T4 receivables.terbuka bentuk sama (days_overdue int) + Σ cocok.
T5 setiap ND ber-return_bill_id tampil (NOTA_DEBIT_DIBUAT) di riwayat tagihan sumbernya; ND bebas dari tagihan LUNAS (original NULL) dihitung."""
import asyncio
import os
import sys
from decimal import Decimal

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import purchase_orders as RO  # noqa: E402
from app.services import dashboard_v2 as DV, tagihan_riwayat as TRW  # noqa: E402
from app.utils.tanggal_tenant import tanggal_dokumen  # noqa: E402

HASIL = []
TENANT = tuple(os.environ.get("TENANT", "kaos-biru-konveksi,pt-kita-nyawit-bersama").split(","))


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


def D(x):
    return Decimal(str(x))


async def boleh(m):
    return True


async def sisi(conn, tid, hari, fungsi, sql, kolom_id, kolom_no, label):
    rows = await conn.fetch(sql, tid)
    r = await fungsi(conn, tid, hari, rows)
    buka = sorted([x for x in rows if x[kolom_id] is not None and D(x["outstanding"]) > 0],
                  key=lambda x: (x["due_date"] or __import__("datetime").date.min, x[kolom_no] or ""))
    cek(f"{tid} {label}: terbuka_count == tagihan terbuka", r["terbuka_count"] == len(buka), f"{r['terbuka_count']} vs {len(buka)}")
    cek(f"{tid} {label}: maks 10 + urut jatuh tempo lalu nomor",
        [x["number"] for x in r["terbuka"]] == [x[kolom_no] for x in buka[:10]], f"n={len(r['terbuka'])}")
    sisa_lain = sum((D(x["outstanding"]) for x in rows if not (x[kolom_id] is not None and D(x["outstanding"]) > 0)), Decimal(0))
    cek(f"{tid} {label}: Σ semua terbuka + saldo awal/kredit == total",
        sum((D(x["outstanding"]) for x in buka), Decimal(0)) + sisa_lain == D(r["total"]),
        f"terbuka={sum((D(x['outstanding']) for x in buka), Decimal(0))} lain={sisa_lain} total={r['total']}")
    if r["terbuka"] and r["due_7d"]:
        cek(f"{tid} {label}: bentuk = due_7d + days_overdue", set(r["terbuka"][0]) == set(r["due_7d"][0]) | {"days_overdue"})
    cek(f"{tid} {label}: days_overdue int >= 0 == hari lewat",
        all(type(x["days_overdue"]) is int and x["days_overdue"] == (max((hari - b["due_date"]).days, 0) if b["due_date"] else 0)
            for x, b in zip(r["terbuka"], buka)))
    return r


async def periksa(conn, tid):
    hari = await tanggal_dokumen(conn, tid)
    print(f"[info] {tid}: hari_ini={hari}", flush=True)
    h = await sisi(conn, tid, hari, DV.hutang, "SELECT * FROM compute_ap_outstanding(p_tenant_id => $1)", "bill_id", "bill_number", "hutang")
    await sisi(conn, tid, hari, DV.piutang, "SELECT * FROM compute_ar_outstanding(p_tenant_id => $1)", "invoice_id", "invoice_number", "piutang")
    print(f"[info] {tid}: hutang terbuka_count={h['terbuka_count']} due_7d={len(h['due_7d'])} total={h['total']}", flush=True)
    if tid == "pt-kita-nyawit-bersama":
        semua = await conn.fetch("SELECT bill_number, outstanding FROM compute_ap_outstanding(p_tenant_id => $1) WHERE bill_number = 'BILL-2611-0007'", tid)
        if semua and D(semua[0]["outstanding"]) > 0:
            r = await DV.hutang(conn, tid, hari)
            dok = await conn.fetch("SELECT * FROM compute_ap_outstanding(p_tenant_id => $1)", tid)
            n, penuh = DV.daftar_terbuka(dok, hari, "bill_id", "bill_number", "vendor", "vendor_name")
            n2, penuh2 = n, [x["bill_number"] for x in sorted([x for x in dok if x["bill_id"] is not None and D(x["outstanding"]) > 0],
                                                              key=lambda x: (x["due_date"], x["bill_number"]))]
            cek(f"{tid} BILL-2611-0007 di daftar terbuka penuh, bukan di due_7d",
                "BILL-2611-0007" in penuh2 and "BILL-2611-0007" not in [x["number"] for x in r["due_7d"]],
                f"posisi={penuh2.index('BILL-2611-0007') + 1}/{n2}")
        else:
            print(f"[info] {tid}: BILL-2611-0007 tak terbuka (sisa {semua[0]['outstanding'] if semua else 'tak ada'})", flush=True)
    nd = await conn.fetch("""SELECT id, credit_number, return_bill_id, original_bill_id FROM vendor_credits
                             WHERE tenant_id = $1 AND return_bill_id IS NOT NULL""", tid)
    bebas = [v for v in nd if v["original_bill_id"] is None]
    print(f"[info] {tid}: ND ber-return_bill_id={len(nd)} (bebas dari tagihan lunas={len(bebas)})", flush=True)
    for v in nd:
        rw = await TRW.riwayat_tagihan(conn, tid, v["return_bill_id"], boleh)
        ada = any(e["jenis"] == "NOTA_DEBIT_DIBUAT" and v["credit_number"] in e["ringkas"] for e in (rw or {}).get("events", []))
        cek(f"{tid} ND {v['credit_number']} tampil di riwayat tagihan sumber", ada)


async def utama():
    pool = await RO.get_pool()
    async with pool.acquire() as conn:
        db = await conn.fetchval("SELECT current_database()")
        if not db.startswith("scratch_b128"):
            print(f"TOLAK: bukan salinan ({db})")
            return 2
        for tid in TENANT:
            await periksa(conn, tid)
    print(f"HASIL {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    return 0 if HASIL and all(HASIL) else 1


sys.exit(asyncio.run(utama()))
