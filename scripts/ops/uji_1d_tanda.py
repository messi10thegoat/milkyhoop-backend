"""Pembantu harness BF-1d (dua pohon kode di salinan DB yang sama): pembayaran tagihan (buat+posting+void) lewat RUTE LAMA, lalu TANDA TANGAN jurnal + bank_transactions + baris pembayaran
+ alokasi + cache tagihan yang dinormalkan. Pohon LAMA (handler inline) dan BARU (inti hasil ekstraksi E4/E5) harus identik. Tak mengimpor `app` (diberi fungsi/kelas oleh pemanggil)."""
import uuid
from datetime import date
from decimal import Decimal as D

import uji_c3_tanda as C3

TGL = date(2026, 10, 6)


async def pilih_tagihan(c, tid, sisa_tagihan, minimal=D(100000), ber_pph=False, lewati=()):
    """Tagihan terbuka tenant dengan sisa >= minimal (sisa dari penentu jurnal, bukan kolom cache). ber_pph menurut kolom bills.pph_amount."""
    for r in await c.fetch("SELECT id, vendor_id, invoice_number, COALESCE(pph_amount, 0) AS pph FROM bills WHERE tenant_id = $1 AND status IN ('posted', 'partial', 'unpaid') ORDER BY invoice_number LIMIT 60", tid):
        if (r["pph"] > 0) != ber_pph or r["id"] in lewati:
            continue
        sisa = await sisa_tagihan(c, tid, r["id"])
        if sisa >= minimal:
            return r, sisa
    return None, None


async def tanda_bayar(c, tid, payment_id, peta):
    p = await c.fetchrow("SELECT status, payment_date, payment_method, total_amount, allocated_amount, unapplied_amount, discount_amount, bank_fee_amount, pph_amount, journal_id, void_journal_id "
                         "FROM bill_payments_v2 WHERE tenant_id = $1 AND id = $2", tid, payment_id)
    al = [[str(a["remaining_before"]), str(a["amount_applied"]), str(a["remaining_after"])] for a in await c.fetch(
        "SELECT a.remaining_before, a.amount_applied, a.remaining_after FROM bill_payment_allocations a JOIN bill_payments_v2 p ON p.id = a.payment_id WHERE p.tenant_id = $1 AND p.id = $2 ORDER BY a.amount_applied", tid, payment_id)]
    s = {"pembayaran": [p["status"], str(p["payment_date"]), p["payment_method"], str(p["total_amount"]), str(p["allocated_amount"]), str(p["unapplied_amount"]), str(p["discount_amount"]),
                        str(p["bank_fee_amount"]), str(p["pph_amount"])], "alokasi": al, "jurnal": await C3.tanda_dengan_pembalik(c, tid, p["journal_id"], peta)}
    if p["void_journal_id"]:
        s["void_jurnal"] = await C3.tanda_jurnal(c, tid, p["void_journal_id"], peta)
    return s


async def jalankan(pool, tid, req, Response, BP, BKR, BodyBuat, BodyAlok, BodyVoid):
    """Operasi deterministik lewat RUTE LAMA: bayar tagihan X 100000 (rekening A) lalu void. -> daftar tanda tangan."""
    async with pool.acquire() as c:
        a, coa_a = await C3.rekening_kembar(c, tid, "8888880401")
        t, sisa = await pilih_tagihan(c, tid, BKR.sisa_tagihan)
        if t is None:
            return [{"lewati": "tak ada tagihan terbuka tanpa PPh"}]
        vendor = await c.fetchrow("SELECT id, name FROM vendors WHERE tenant_id = $1 AND id = $2", tid, t["vendor_id"])
    peta = {str(coa_a): "A", str(a): "A"}
    r = await BP.create_bill_payment(req(tid), BodyBuat(vendor_id=str(vendor["id"]), payment_date=TGL, bank_account_id=str(a), total_amount=D(100000), notes="uji",
                                                         allocations=[BodyAlok(bill_id=str(t["id"]), amount_applied=D(100000))]))
    pid = uuid.UUID(r["data"]["id"])
    hasil = [{"respons": {"status": r["data"]["status"], "nomor": C3.norm(r["data"]["payment_number"]), "pesan": C3.norm(r["message"])}}]
    async with pool.acquire() as c:
        hasil.append({"sesudah_bayar": await tanda_bayar(c, tid, pid, peta), "tagihan": [str(x) for x in await c.fetchrow("SELECT status, amount_paid FROM bills WHERE tenant_id = $1 AND id = $2", tid, t["id"])]})
    v = await BP.void_bill_payment(req(tid), str(pid), BodyVoid(void_reason="uji batal bayar"), Response())
    vd, vp = (v["data"], v["message"]) if isinstance(v, dict) else (v.data, v.message)      # execute_idempotent mengembalikan dict; pohon lain bisa objek -- normalkan keduanya
    hasil.append({"void": {"status": vd["status"], "pesan": C3.norm(vp)}})
    async with pool.acquire() as c:
        hasil.append({"sesudah_void": await tanda_bayar(c, tid, pid, peta), "tagihan": [str(x) for x in await c.fetchrow("SELECT status, amount_paid FROM bills WHERE tenant_id = $1 AND id = $2", tid, t["id"])],
                      "sisa": str(await BKR.sisa_tagihan(c, tid, t["id"]))})
    return hasil
