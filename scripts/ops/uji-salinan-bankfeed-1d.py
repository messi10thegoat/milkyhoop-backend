"""BF-1d -- uji CATAT bayar_tagihan + BATALKAN BILL_PAYMENT di SALINAN (klon milkydb) lewat RUTE asli Bank Feed. Dua tenant UJI; tanpa data grapgrap/fixture golden.
(A) DUA POHON: pembayaran tagihan (buat+posting+void) lewat rute LAMA di pohon LAMA (c3) dan BARU: tanda tangan jurnal/bank_transactions/pembayaran/alokasi/cache tagihan IDENTIK (E4/E5).
(B) Aksi Bank Feed: pratinjau==tulis, tautan hanya ke rekening ini, void langsung 409 BANK_TX_BERTAUT, batal = pembalik sungguhan, replay, serentak, kelebihan tampil, sen, PPh DITOLAK (+kontrol),
periode tertutup (+kontrol), kartu kredit (hasil == modul; +kontrol bank). Izin disetubkan (logika diuji unit + sabotase). Tulis HANYA di salinan."""
import asyncio
import re
import sys
import uuid
from decimal import Decimal as D
from uuid import UUID

from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import Response

sys.path.insert(0, "/app/backend/api_gateway")
sys.path.insert(0, "/tanda")
import uji_1d_tanda as UT  # noqa: E402
import uji_c3_tanda as C3  # noqa: E402
from app.routers import bill_payments as BP  # noqa: E402
from app.routers import mutasi_bank as MR  # noqa: E402
from app.schemas.bill_payments import BillAllocationInput, CreateBillPaymentRequest, VoidBillPaymentRequest  # noqa: E402
from app.schemas.bankfeed import AlokasiTagihan, BatalkanCatatanRequest, CatatRequest  # noqa: E402
from app.services import bankfeed_aksi as BA  # noqa: E402
from app.services import bayar_keluar_rencana as BKR  # noqa: E402
from app.services.db_pool import get_db_pool  # noqa: E402

HASIL = []
TENANT = ("kaos-biru-konveksi", "pt-kita-nyawit-bersama")
TGL = UT.TGL


async def _izin_stub(user, jalur, method="GET"):
    return {j: True for j in jalur}
BA.izin_rute_pengguna = _izin_stub


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


def req(tid, kunci=None):
    r = Request({"type": "http", "method": "POST", "path": "/x", "headers": [(b"x-idempotency-key", kunci.encode())] if kunci else []})
    r.state.user = {"tenant_id": tid, "user_id": C3.USER, "username": "uji-bf1d"}
    return r


def _hasil(e):
    d = e.detail
    return {"code": d.get("code"), "message": d.get("message")} if isinstance(d, dict) else {"code": getattr(e, "code", None), "message": d}


async def panggil(coro):
    try:
        return 200, await coro
    except HTTPException as e:
        return e.status_code, _hasil(e)


def kode(h):
    return [b["code"] for b in h["blocks"]]


async def mut(c, tid, rid, arah, nominal, tgl=TGL, ket="TRSF BAYAR UJI"):
    mid = uuid.uuid4()
    await c.execute("INSERT INTO mutasi_bank (id, tenant_id, bank_account_id, tanggal, keterangan, keterangan_normal, arah, nominal, sidik) VALUES ($1, $2, $3, $4, $5, $5, $6, $7, $8)",
                    mid, tid, rid, tgl, f"{ket} {mid.hex[:6]}", arah, nominal, uuid.uuid4().hex)
    return mid


async def hitung(c, tid, rid):
    return {"je": await c.fetchval("SELECT count(*) FROM journal_entries WHERE tenant_id = $1", tid),
            "bt": await c.fetchval("SELECT count(*) FROM bank_transactions WHERE tenant_id = $1", tid),
            "bp": await c.fetchval("SELECT count(*) FROM bill_payments_v2 WHERE tenant_id = $1", tid),
            "msl": await c.fetchval("SELECT count(*) FROM bank_transactions WHERE tenant_id = $1 AND bank_account_id = $2 AND matched_statement_line_id IS NOT NULL", tid, rid)}


async def baris_jurnal(c, tid, jid):
    return {(str(r["account_id"]), r["debit"] > 0) for r in await c.fetch(
        "SELECT jl.account_id, jl.debit FROM journal_lines jl JOIN journal_entries e ON e.id = jl.journal_id WHERE e.tenant_id = $1 AND e.id = $2", tid, jid)}


async def utama():
    pool = await get_db_pool()
    async with pool.acquire() as c:
        db = await c.fetchval("SELECT current_database()")
    if not db.startswith("scratch_bf1d"):
        print(f"TOLAK: bukan salinan ({db})")
        return 2
    for tid in TENANT:
        lama = C3.muat(f"/sig/lama_{tid}.json")
        baru = await UT.jalankan(pool, tid, req, Response, BP, BKR, CreateBillPaymentRequest, BillAllocationInput, VoidBillPaymentRequest)
        cek(f"{tid} A E4/E5: pembayaran tagihan (buat+posting+void) lewat rute: tanda tangan jurnal/bank_transactions/pembayaran/alokasi/cache tagihan IDENTIK pohon LAMA vs BARU ({len(baru)} entri)",
            lama == baru and len(baru) > 1, "" if lama == baru else next((f"beda di entri {i}: {a} != {b}" for i, (a, b) in enumerate(zip(lama, baru)) if a != b), "panjang beda"))
        async with pool.acquire() as c:
            rid, coa_x = await C3.rekening_kembar(c, tid, "8888880411")
            rid_cc, coa_cc = await C3.rekening_kembar(c, tid, "8888880412", tipe="credit_card", saldo=0)
            t1, sisa1 = await UT.pilih_tagihan(c, tid, BKR.sisa_tagihan)
            t2, sisa2 = await UT.pilih_tagihan(c, tid, BKR.sisa_tagihan, lewati=(t1["id"],) if t1 else ())
            tp, _ = await UT.pilih_tagihan(c, tid, BKR.sisa_tagihan, ber_pph=True)
        if t1 is None:
            print(f"INFO {tid}: tak ada tagihan terbuka tanpa PPh di salinan -> bagian B dilewati", flush=True)
            continue

        async def prev(rek, mid, **b):
            return await panggil(MR.pratinjau_catat(req(tid), rek, mid, CatatRequest(**b)))

        async def catat(rek, mid, kunci=None, **b):
            return await panggil(MR.catat(req(tid, kunci), rek, mid, CatatRequest(**b), Response()))

        async def prev_b(rek, mid, alasan="uji batal"):
            return await panggil(MR.pratinjau_batalkan_catatan(req(tid), rek, mid, BatalkanCatatanRequest(alasan=alasan)))

        async def batal(rek, mid, alasan="uji batal"):
            return await panggil(MR.batalkan_catatan(req(tid), rek, mid, BatalkanCatatanRequest(alasan=alasan), Response()))

        async def status(mid):
            async with pool.acquire() as c:
                return await c.fetchrow("SELECT status, cara FROM mutasi_bank WHERE tenant_id = $1 AND id = $2", tid, mid)

        async def tautan(mid):
            async with pool.acquire() as c:
                return [(r["bank_transaction_id"], r["bank_account_id"], r["dilepas_pada"] is None) for r in await c.fetch(
                    "SELECT t.bank_transaction_id, bt.bank_account_id, t.dilepas_pada FROM mutasi_bank_tautan t JOIN bank_transactions bt ON bt.id = t.bank_transaction_id AND bt.tenant_id = t.tenant_id "
                    "WHERE t.tenant_id = $1 AND t.mutasi_bank_id = $2 ORDER BY t.dibuat_pada", tid, mid)]

        async def sisa(bid):
            async with pool.acquire() as c:
                return await BKR.sisa_tagihan(c, tid, bid)

        async def dok_id(mid):
            async with pool.acquire() as c:
                return UUID(str(await c.fetchval("SELECT je.source_id FROM mutasi_bank_tautan t JOIN bank_transactions bt ON bt.id = t.bank_transaction_id AND bt.tenant_id = t.tenant_id "
                                                 "JOIN journal_entries je ON je.id = bt.journal_id AND je.tenant_id = bt.tenant_id WHERE t.tenant_id = $1 AND t.mutasi_bank_id = $2 AND t.dilepas_pada IS NULL "
                                                 "ORDER BY t.dibuat_pada LIMIT 1", tid, mid)))
        vend1 = str(t1["vendor_id"])
        al1 = [AlokasiTagihan(bill_id=t1["id"], nominal=D(100000))]
        # ---- blok berkode
        async with pool.acquire() as c:
            m_in, m_x = await mut(c, tid, rid, "masuk", D("100000")), await mut(c, tid, rid, "keluar", D("100000"))
            pra = await hitung(c, tid, rid)
        s_a, h_a = await catat(rid, m_in, jenis="bayar_tagihan", kontak=vend1, alokasi=al1)
        s_b, h_b = await catat(rid, m_x, jenis="bayar_tagihan", kontak=str(uuid.uuid4()), alokasi=al1)
        s_c, h_c = await catat(rid, m_x, jenis="bayar_tagihan", kontak=vend1)
        s_d, h_d = await catat(rid, m_x, jenis="bayar_tagihan", kontak=vend1, alokasi=[AlokasiTagihan(bill_id=t1["id"], nominal=D(100001))])
        s_e, h_e = await catat(rid, m_x, jenis="bayar_tagihan", kontak=vend1, alokasi=[AlokasiTagihan(bill_id=t1["id"], nominal=D(50000)), AlokasiTagihan(bill_id=t1["id"], nominal=D(50000))])
        async with pool.acquire() as c:
            pas = await hitung(c, tid, rid)
        cek(f"{tid} B0 blok berkode: masuk JENIS_TAK_SESUAI_ARAH; vendor tak ada VENDOR_TAK_ADA; alokasi wajib; melebihi nominal; faktur ganda ALOKASI_TAK_SAH; NOL jurnal",
            h_a["code"] == "JENIS_TAK_SESUAI_ARAH" and h_b["code"] == "VENDOR_TAK_ADA" and h_c["code"] == "ALOKASI_WAJIB" and h_d["code"] == "ALOKASI_MELEBIHI_NOMINAL" and h_e["code"] == "ALOKASI_TAK_SAH" and pas == pra,
            f"{h_a} {h_b} {h_c} {h_d} {h_e}")
        # ---- catat + void langsung + batal
        s1, pv = await prev(rid, m_x, jenis="bayar_tagihan", kontak=vend1, alokasi=al1)
        sisa0 = await sisa(t1["id"])
        cek(f"{tid} B1 pratinjau pembayaran pas: ok, tanpa blok, tanpa kelebihan, faktur sesudah dan jurnal ringkas tampil, NOL tulis", s1 == 200 and pv["data"]["ok"] and pv["data"]["kelebihan"] is None and pv["data"]["faktur_sesudah"] and pv["data"]["jurnal_ringkas"]
            and (await hitung_u(pool, tid, rid)) == pra, f"{s1} {pv}")
        s2, h2 = await catat(rid, m_x, jenis="bayar_tagihan", kontak=vend1, alokasi=al1, versi=pv["data"]["payload"]["versi"])
        async with pool.acquire() as c:
            pas = await hitung(c, tid, rid)
            bp = await c.fetchrow("SELECT status, total_amount, unapplied_amount FROM bill_payments_v2 WHERE tenant_id = $1 AND journal_id = $2", tid, UUID(h2["data"]["journal_id"])) if s2 == 200 else None
        tt = await tautan(m_x)
        cek(f"{tid} B2 catat: 200; pembayaran posted Rp100.000; sisa tagihan turun Rp100.000; tautan aktif 1 ke bt rekening ini; baris dicatat/catat",
            s2 == 200 and bp and bp["status"] == "posted" and bp["total_amount"] == D(100000) and (await sisa(t1["id"])) == sisa0 - D(100000) and len(tt) == 1 and tt[0][2] and tt[0][1] == rid
            and (await status(m_x))["status"] == "dicatat" and pas["bp"] == pra["bp"] + 1 and pas["msl"] == 0, f"{s2} {h2} {bp}")
        pid = await dok_id(m_x)
        s3, h3 = await panggil(BP.void_bill_payment(req(tid), str(pid), VoidBillPaymentRequest(void_reason="void langsung"), Response()))
        cek(f"{tid} B3 void LANGSUNG pembayaran yang masih bertaut: 409 BANK_TX_BERTAUT (rute nyata modul)", s3 == 409 and h3["code"] == "BANK_TX_BERTAUT", f"{s3} {h3}")
        s4, pb = await prev_b(rid, m_x)
        s5, h5 = await batal(rid, m_x)
        async with pool.acquire() as c:
            rv = await c.fetchval("SELECT id FROM journal_entries WHERE tenant_id = $1 AND reversal_of_id = $2", tid, UUID(h2["data"]["journal_id"]))
            rl = await baris_jurnal(c, tid, rv) if rv else set()
            pst = await c.fetchval("SELECT status FROM bill_payments_v2 WHERE tenant_id = $1 AND id = $2", tid, pid)
        pr = {(j["account_id"], bool(re.search(r"[1-9]", j["debit"]))) for j in pb["data"]["jurnal"]}
        cek(f"{tid} B4 batalkan catatan: pratinjau tanpa blok + tiga kalimat, jurnal pembalik == pembalik SUNGGUHAN; pembayaran voided; sisa tagihan pulih; baris untuk_ditinjau; tautan dilepas",
            s4 == 200 and pb["data"]["ok"] and len(pb["data"]["kalimat_tiga"]) == 3 and s5 == 200 and pr == rl and pst == "voided" and (await sisa(t1["id"])) == sisa0
            and (await status(m_x))["status"] == "untuk_ditinjau" and not any(x[2] for x in await tautan(m_x)), f"{s4} {s5} {h5} {pst} {pr} vs {rl}")
        s6, h6 = await catat(rid, m_x, jenis="bayar_tagihan", kontak=vend1, alokasi=al1)
        cek(f"{tid} B5 catat ULANG setelah batal: lolos (kunci dokumen berurut); tautan aktif 1 dari total 2", s6 == 200 and len([x for x in await tautan(m_x) if x[2]]) == 1 and len(await tautan(m_x)) == 2, f"{s6} {h6}")
        s7, h7 = await batal(rid, m_x)
        # ---- kelebihan + sen
        async with pool.acquire() as c:
            m_l, m_s = await mut(c, tid, rid, "keluar", D("150000")), await mut(c, tid, rid, "keluar", D("100000.50"))
        s8, pv2 = await prev(rid, m_l, jenis="bayar_tagihan", kontak=vend1, alokasi=al1)
        s9, h9 = await catat(rid, m_l, jenis="bayar_tagihan", kontak=vend1, alokasi=al1, versi=pv2["data"]["payload"]["versi"])
        async with pool.acquire() as c:
            un = await c.fetchval("SELECT unapplied_amount FROM bill_payments_v2 WHERE tenant_id = $1 AND journal_id = $2", tid, UUID(h9["data"]["journal_id"])) if s9 == 200 else None
        cek(f"{tid} B6 kelebihan Rp50.000: pratinjau menampilkannya (kalimat + kelebihan.jumlah); tulis 200; pembayaran mencatat unapplied sesuai modul", s8 == 200 and pv2["data"]["kelebihan"] and D(pv2["data"]["kelebihan"]["jumlah"]) == D(50000)
            and "kelebihan" in pv2["data"]["kalimat"].lower() and s9 == 200 and un == D(50000), f"{s8} {pv2} {s9} {un}")
        await batal(rid, m_l)
        s10, pv3 = await prev(rid, m_s, jenis="bayar_tagihan", kontak=vend1, alokasi=[AlokasiTagihan(bill_id=t1["id"], nominal=D("100000.50"))])
        cek(f"{tid} B7 nominal bersen (Rp100.000,50) diterima (tak ada NOMINAL_BERSEN) dan pratinjau ok bila modul menerima", s10 == 200 and "NOMINAL_BERSEN" not in kode(pv3["data"]), f"{s10} {kode(pv3['data'])}")
        # ---- PPh ditolak + kontrol
        if tp is not None:
            async with pool.acquire() as c:
                m_p = await mut(c, tid, rid, "keluar", D(100000))
            s11, h11 = await prev(rid, m_p, jenis="bayar_tagihan", kontak=str(tp["vendor_id"]), alokasi=[AlokasiTagihan(bill_id=tp["id"], nominal=D(100000))])
            cek(f"{tid} B8 tagihan ber-PPh DITOLAK TAGIHAN_BER_PPH dengan arahan ke form Bayar Tagihan; kontrol: tagihan tanpa PPh (B1) lolos", s11 == 200 and "TAGIHAN_BER_PPH" in kode(h11["data"]) and "Bayar Tagihan" in str(h11["data"]["blocks"]), f"{s11} {h11}")
        else:
            print(f"INFO {tid}: tak ada tagihan ber-PPh di salinan -> B8 dilewati (dijaga unit + sabotase b25)", flush=True)
        # ---- serentak + replay
        async with pool.acquire() as c:
            m_c = await mut(c, tid, rid, "keluar", D(100000))
            pra = await hitung(c, tid, rid)
        r1, r2 = await asyncio.gather(catat(rid, m_c, jenis="bayar_tagihan", kontak=vend1, alokasi=al1), catat(rid, m_c, jenis="bayar_tagihan", kontak=vend1, alokasi=al1))
        async with pool.acquire() as c:
            pas = await hitung(c, tid, rid)
        cek(f"{tid} B9 serentak: dua catat pada baris yang sama -> TEPAT satu 200 dan satu 409; hanya satu pembayaran", sorted([r1[0], r2[0]]) == [200, 409] and pas["bp"] == pra["bp"] + 1, f"{r1[0]} {r2[0]}")
        await batal(rid, m_c)
        async with pool.acquire() as c:
            m_r = await mut(c, tid, rid, "keluar", D(100000))
            pra = await hitung(c, tid, rid)
        k = f"uji-{uuid.uuid4().hex}"
        a1, a2 = await catat(rid, m_r, kunci=k, jenis="bayar_tagihan", kontak=vend1, alokasi=al1), await catat(rid, m_r, kunci=k, jenis="bayar_tagihan", kontak=vend1, alokasi=al1)
        async with pool.acquire() as c:
            pas = await hitung(c, tid, rid)
        cek(f"{tid} B10 replay kunci idempotensi sama: respons sama, TIDAK ada pembayaran kedua", a1[0] == 200 and a2[0] == 200 and a1[1]["data"]["journal_id"] == a2[1]["data"]["journal_id"] and pas["bp"] == pra["bp"] + 1, f"{a1[0]} {a2[0]}")
        await batal(rid, m_r)
        # ---- periode tertutup + kontrol
        async def periode(status_):
            async with pool.acquire() as c:
                await c.execute("UPDATE fiscal_periods SET status = $3, closed_at = CASE WHEN $3 = 'CLOSED' THEN now() ELSE NULL END WHERE tenant_id = $1 AND start_date <= $2 AND end_date >= $2", tid, TGL, status_)
        async with pool.acquire() as c:
            m_pt, m_pb = await mut(c, tid, rid, "keluar", D(100000)), await mut(c, tid, rid, "keluar", D(100000))
        s_ok, _ = await catat(rid, m_pb, jenis="bayar_tagihan", kontak=vend1, alokasi=al1)
        await periode("CLOSED")
        async with pool.acquire() as c:
            pra = await hitung(c, tid, rid)
        s_pv, h_pv = await prev(rid, m_pt, jenis="bayar_tagihan", kontak=vend1, alokasi=al1)
        s_pc, h_pc = await catat(rid, m_pt, jenis="bayar_tagihan", kontak=vend1, alokasi=al1)
        s_bc, h_bc = await batal(rid, m_pb)
        async with pool.acquire() as c:
            pas = await hitung(c, tid, rid)
        cek(f"{tid} P1 periode TERTUTUP: catat -> blok PERIODE_TERTUTUP + ditolak; batalkan catatan lama ditolak; NOL jurnal; baris tetap", s_ok == 200 and "PERIODE_TERTUTUP" in "".join(kode(h_pv["data"])) + str(h_pc) and s_pc in (400, 403)
            and s_bc in (400, 403) and pas["je"] == pra["je"] and (await status(m_pt))["status"] == "untuk_ditinjau" and (await status(m_pb))["status"] == "dicatat", f"{h_pv['data']['blocks'] if s_pv == 200 else h_pv} {s_pc} {s_bc} {h_bc}")
        await periode("OPEN")
        s_po, _ = await catat(rid, m_pt, jenis="bayar_tagihan", kontak=vend1, alokasi=al1)
        s_bo, _ = await batal(rid, m_pb)
        cek(f"{tid} P2 kontrol: periode dibuka kembali -> catat dan batalkan yang sama lolos", s_po == 200 and s_bo == 200)
        await batal(rid, m_pt)
        # ---- kartu kredit: hasil == modul (kontrol: bank = payment_made)
        async with pool.acquire() as c:
            m_cc = await mut(c, tid, rid_cc, "keluar", D(100000))
            pcc = CreateBillPaymentRequest(vendor_id=vend1, payment_date=TGL, payment_method="bank_transfer", bank_account_id=str(rid_cc), total_amount=D(100000),
                                           allocations=[BillAllocationInput(bill_id=str(t1["id"]), amount_applied=D(100000))])
            modul = await BKR.rencana_bayar(c, {"tenant_id": tid, "user_id": C3.USER}, pcc, True)
        s_cc, h_cc = await prev(rid_cc, m_cc, jenis="bayar_tagihan", kontak=vend1, alokasi=al1)
        modul_ok = not modul["blocks"]
        cek(f"{tid} C1 kartu kredit: pratinjau Bank Feed == modul Bayar Tagihan (ok bila modul ok; blok yang sama bila modul blok)", s_cc == 200 and h_cc["data"]["ok"] == modul_ok and (modul_ok or set(kode(h_cc["data"])) >= {b["code"] for b in modul["blocks"]}), f"modul_ok={modul_ok} {kode(h_cc['data'])}")
        if modul_ok:
            s_ccw, h_ccw = await catat(rid_cc, m_cc, jenis="bayar_tagihan", kontak=vend1, alokasi=al1)
            async with pool.acquire() as c:
                tipe = await c.fetchval("SELECT transaction_type FROM bank_transactions WHERE tenant_id = $1 AND journal_id = $2 AND bank_account_id = $3", tid, UUID(h_ccw["data"]["journal_id"]), rid_cc) if s_ccw == 200 else None
            cek(f"{tid} C2 kartu kredit ditulis: cermin bank 'charge' (bukan payment_made); kontrol rekening bank biasa = payment_made (B2)", s_ccw == 200 and tipe == "charge", f"{s_ccw} {tipe}")
            await batal(rid_cc, m_cc)
        async with pool.acquire() as c:
            akhir = await hitung(c, tid, rid)
        cek(f"{tid} Z invariants: matched_statement_line_id tetap 0", akhir["msl"] == 0)
    n = len(HASIL)
    print(f"HASIL {sum(HASIL)}/{n} lulus", flush=True)
    return 0 if all(HASIL) and n else 1


async def hitung_u(pool, tid, rid):
    async with pool.acquire() as c:
        return await hitung(c, tid, rid)


sys.exit(asyncio.run(utama()))
