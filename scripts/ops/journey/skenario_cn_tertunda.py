"""Skenario CN atas pendapatan TERTUNDA (26 Sep 2026, unit uang MASTER; temuan BACKEND3).

Tenant kaos (revenue_recognition_policy='delivery'): faktur diposting -> Cr Pendapatan Diterima Dimuka; barang
BELUM dikirim. Lalu nota kredit (reason='discount', faktur asal diisi) SEBESAR faktur = pesanan batal sebelum kirim.
Harapan = akuntansi BENAR (PSAK 72, bagian kewajiban yang belum dipenuhi): Dimuka faktur turun ke 0,
allocated_amount baris turun sebesar bagian tertunda, Retur Penjualan TIDAK didebit untuk bagian itu, AR faktur 0,
Check 16 tetap PASS. Kode hari ini: Dr Retur / Cr Piutang -> harapan Dimuka/allocated/Retur MERAH (itu buktinya).
Lalu VOID nota kredit: semua kembali ke keadaan sebelum NK (cermin)."""
import uuid
from decimal import Decimal as D

from journey_lib import BARANG, NAMA, PELANGGAN, T
from skenario_so_penuh import so_body


async def _ukur(J, inv_id, akun_dimuka):
    async with J.pool.acquire() as c:
        baris = await c.fetch("select allocated_amount, coalesce(recognized_amount,0) r from sales_invoice_items "
                              "where invoice_id=$1", uuid.UUID(inv_id))
        dimuka = await c.fetchval(
            """select coalesce(sum(jl.credit - jl.debit),0) from journal_lines jl
               join journal_entries je on je.id=jl.journal_id
               where je.tenant_id=$1 and je.status='POSTED' and je.created_at >= $2 and jl.account_id=$3""",
            T, J.mulai, akun_dimuka)
        retur = await c.fetchval(
            """select coalesce(sum(jl.debit - jl.credit),0) from journal_lines jl
               join journal_entries je on je.id=jl.journal_id join chart_of_accounts a on a.id=jl.account_id
               where je.tenant_id=$1 and je.status='POSTED' and je.created_at >= $2 and a.account_code='4-10300'""",
            T, J.mulai)
        ar = await c.fetchval("select coalesce(sum(outstanding),0) from compute_ar_outstanding($1) where invoice_id=$2",
                              T, uuid.UUID(inv_id))
        c16 = await c.fetchrow("select verdict, drift from verify_deferred_revenue_reconciliation_all() where tenant_id=$1", T)
    tertunda = sum((D(str(b["allocated_amount"] or 0)) - D(str(b["r"])) for b in baris), D(0))
    return {"alokasi": sum((D(str(b["allocated_amount"] or 0)) for b in baris), D(0)), "tertunda_subledger": tertunda,
            "dimuka_gl": D(str(dimuka)), "retur_gl": D(str(retur)), "ar": D(str(ar)), "check16": c16["verdict"]}


def _cek(J, nama, nyata, harap):
    beda = {k: (str(nyata[k]), str(v)) for k, v in harap.items() if nyata[k] != v}
    J._catat(nama, {"langkah": nama, "hasil": "FAIL" if beda else "PASS", "nyata": {k: str(v) for k, v in nyata.items()},
                    "harap": {k: str(v) for k, v in harap.items()}, "beda_nyata_vs_harap": beda})
    print(f"{J.no:02d} {'FAIL' if beda else 'PASS':5} cek {nama}: {({k: str(v) for k, v in nyata.items()})} beda={beda}")


async def jalankan(J):
    async with J.pool.acquire() as c:
        pol = await c.fetchval("select revenue_recognition_policy from tenant_config where tenant_id=$1", T)
    if pol != "delivery":
        return J.gagal("prasyarat", f"kaos revenue_recognition_policy={pol}, skenario butuh 'delivery'")
    _, so = await J.langkah("01_so", "POST", "/api/sales-orders", so_body(J),
                            headers={"X-Idempotency-Key": f"journey-{uuid.uuid4()}"})
    so_id = so["data"]["id"]
    await J.langkah("01b_confirm", "POST", f"/api/sales-orders/{so_id}/confirm")
    _, det = await J.langkah("01c_detail", "GET", f"/api/sales-orders/{so_id}")
    await J.langkah("02_to_invoice", "POST", f"/api/sales-orders/{so_id}/to-invoice",
                    {"items": [{"so_item_id": det["data"]["items"][0]["id"], "quantity": 10}]})
    inv = (await J.faktur_so(so_id))[0]
    await J.langkah("02b_post", "POST", f"/api/sales-invoices/{inv}/post", {})
    async with J.pool.acquire() as c:   # akun Dimuka = yang DIKREDIT jurnal INVOICE faktur ini (bukan kode tebakan)
        akun_dimuka = await c.fetchval(
            """select jl.account_id from journal_lines jl join journal_entries je on je.id=jl.journal_id
               join chart_of_accounts a on a.id=jl.account_id
               where je.tenant_id=$1 and je.source_type='INVOICE' and je.source_id=$2 and jl.credit>0
                 and a.account_type <> 'ASSET' order by jl.credit desc limit 1""", T, uuid.UUID(inv))
    awal = await _ukur(J, inv, akun_dimuka)
    _cek(J, "03_sesudah_post_tertunda", awal, {"tertunda_subledger": D("500000.00"), "dimuka_gl": D("500000.00"),
                                               "ar": D("500000.00"), "retur_gl": D("0"), "check16": "PASS"})

    _, cn = await J.langkah("04_nk_buat", "POST", "/api/credit-notes", {
        "customer_id": PELANGGAN, "customer_name": NAMA, "credit_note_date": J.hari.isoformat(),
        "original_invoice_id": inv, "reason": "discount", "reason_detail": "pesanan batal sebelum kirim (journey)",
        "items": [{"item_id": BARANG, "description": "Batal sebelum kirim", "quantity": "10", "unit_price": "50000"}]})
    cn_id = ((cn or {}).get("data") or {}).get("id")
    if not cn_id:
        return J.gagal("04_nk_id", f"NK tak dibuat: {str(cn)[:200]}")
    await J.langkah("04b_nk_post", "POST", f"/api/credit-notes/{cn_id}/post")
    sesudah = await _ukur(J, inv, akun_dimuka)
    # BENAR: kewajiban yang belum dipenuhi dibatalkan -> Dimuka & alokasi turun; Retur tak didebit; AR 0
    _cek(J, "05_sesudah_nk_BENAR", sesudah, {"ar": D("0"), "dimuka_gl": D("0"), "tertunda_subledger": D("0"),
                                             "alokasi": D("0"), "retur_gl": D("0"), "check16": "PASS"})

    # P5 detektor (V312): NK ini harus TERCATAT porsinya -> PASS. Kode lama: FAIL (cn_tanpa_porsi_tertunda=1).
    async with J.pool.acquire() as c:
        det = await c.fetchrow("select * from verify_cn_deferral_all() where tenant_id=$1", T)
    J._catat("05b_detektor_v312", {"langkah": "05b_detektor_v312", "hasil": "PASS" if det and det["verdict"] == "PASS"
                                   else "FAIL", "nyata": dict(det) if det else None})
    print(f"{J.no:02d} {'PASS' if det and det['verdict'] == 'PASS' else 'FAIL':5} detektor v312: {dict(det) if det else None}")
    await J.langkah("06_nk_void", "POST", f"/api/credit-notes/{cn_id}/void", {"reason": "uji cermin"})
    balik = await _ukur(J, inv, akun_dimuka)
    _cek(J, "07_sesudah_void_nk_cermin", balik, {"ar": D("500000.00"), "dimuka_gl": D("500000.00"),
                                                  "tertunda_subledger": D("500000.00"), "alokasi": D("500000.00"),
                                                  "retur_gl": D("0"), "check16": "PASS"})
    await J.potret("08_akhir", so_id, [inv])

    # ---- bagian 2: NK SEBAGIAN (diskon 100k) -> kirim penuh -> pendapatan = nilai kontrak BARU -> void NK 409
    from skenario_so_penuh import kirim
    _, so2 = await J.langkah("11_so2", "POST", "/api/sales-orders", so_body(J),
                             headers={"X-Idempotency-Key": f"journey-{uuid.uuid4()}"})
    so2_id = so2["data"]["id"]
    await J.langkah("11b_confirm", "POST", f"/api/sales-orders/{so2_id}/confirm")
    _, det2 = await J.langkah("11c_detail", "GET", f"/api/sales-orders/{so2_id}")
    await J.langkah("12_to_invoice", "POST", f"/api/sales-orders/{so2_id}/to-invoice",
                    {"items": [{"so_item_id": det2["data"]["items"][0]["id"], "quantity": 10}]})
    inv2 = (await J.faktur_so(so2_id))[0]
    await J.langkah("12b_post", "POST", f"/api/sales-invoices/{inv2}/post", {})
    _, cn2 = await J.langkah("13_nk_diskon_100k", "POST", "/api/credit-notes", {
        "customer_id": PELANGGAN, "customer_name": NAMA, "credit_note_date": J.hari.isoformat(),
        "original_invoice_id": inv2, "reason": "discount",
        "items": [{"item_id": BARANG, "description": "Diskon", "quantity": "1", "unit_price": "100000"}]})
    cn2_id = cn2["data"]["id"]
    await J.langkah("13b_nk_post", "POST", f"/api/credit-notes/{cn2_id}/post")
    await kirim(J, inv2, "14", kenal=None)
    async with J.pool.acquire() as c:
        pendapatan = await c.fetchval(
            """select coalesce(sum(jl.credit - jl.debit),0) from journal_lines jl
               join journal_entries je on je.id=jl.journal_id
               where je.tenant_id=$1 and je.status='POSTED' and je.source_type='INVOICE_REVENUE'
                 and je.source_id=$2 and jl.account_id <> $3""", T, uuid.UUID(inv2), akun_dimuka)
        # sisi PENDAPATAN saja: jurnal INVOICE_REVENUE = Dr Dimuka / Cr Penjualan -> Σ semua baris selalu 0
    k2 = await _ukur(J, inv2, akun_dimuka)
    k2["pendapatan_faktur"] = D(str(pendapatan))
    # (Dimuka/Retur di _ukur = kumulatif skenario: bagian 1 berakhir 500k Dimuka sesudah void; bagian 2 menambah 0)
    _cek(J, "15_sesudah_kirim_nilai_kontrak_baru", k2, {"pendapatan_faktur": D("400000.00"), "tertunda_subledger": D("0"),
                                                         "ar": D("400000.00"), "check16": "PASS"})
    await J.langkah("16_void_nk_sesudah_kirim_409", "POST", f"/api/credit-notes/{cn2_id}/void", {"reason": "uji"},
                    harap=(409,))
    await J.potret("17_akhir_so2", so2_id, [inv2])
