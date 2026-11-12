"""BF-1c3 -- uji CATAT transfer/uang_muka_pelanggan/terima_pembayaran + BATALKAN generik di SALINAN (klon milkydb) lewat RUTE asli Bank Feed.
(A) DUA POHON: operasi Transfer/Uang Muka lewat rute LAMA dijalankan di pohon LAMA (uji-salinan-bankfeed-1c3-lama.py) dan di pohon BARU; tanda tangan jurnal+bank_transactions HARUS identik (E1-E3).
(B) Aksi Bank Feed: pratinjau==tulis, jurnal benar, tautan HANYA sisi rekening ini, batal = jurnal pembalik (pratinjau DP/bayar = jurnal asli ditukar, dicocokkan dengan pembalik sungguhan),
BANK_TX_BERTAUT pada void langsung, idempotensi (replay), serentak, blok berkode, periode tertutup, KAS_MINUS (kontrol), izin (disetubkan; logikanya diuji unit + sabotase). Tulis HANYA di salinan."""
import asyncio
import re
import sys
import uuid
from datetime import date
from decimal import Decimal as D
from uuid import UUID

from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import Response

sys.path.insert(0, "/app/backend/api_gateway")
sys.path.insert(0, "/tanda")
import uji_c3_tanda as UT  # noqa: E402
from app.routers import bank_transfers as BTR  # noqa: E402
from app.routers import customer_deposits as CD  # noqa: E402
from app.routers import mutasi_bank as MR  # noqa: E402
from app.routers import receive_payments as RP  # noqa: E402
from app.schemas.bank_transfers import CreateBankTransferRequest, VoidBankTransferRequest  # noqa: E402
from app.schemas.bankfeed import AlokasiFaktur, BatalkanCatatanRequest, CatatRequest  # noqa: E402
from app.schemas.customer_deposits import VoidCustomerDepositRequest  # noqa: E402
from app.services import bankfeed_aksi as BA  # noqa: E402
from app.services.db_pool import get_db_pool  # noqa: E402

HASIL = []
TENANT = ("kaos-biru-konveksi", "pt-kita-nyawit-bersama")
TGL = UT.TGL
IZIN = {"boleh": True, "diminta": []}


async def _izin_stub(user, jalur, method="GET"):       # layanan kebijakan tak ada di kontainer uji; logika izin diuji unit + sabotase
    IZIN["diminta"].append((tuple(jalur), method))
    return {j: IZIN["boleh"] for j in jalur}
BA.izin_rute_pengguna = _izin_stub


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


def req(tid, kunci=None):
    r = Request({"type": "http", "method": "POST", "path": "/x", "headers": [(b"x-idempotency-key", kunci.encode())] if kunci else []})
    r.state.user = {"tenant_id": tid, "user_id": UT.USER, "username": "uji-bf1c3"}
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


async def hitung(c, tid, rid, rid2=None):
    ids = [rid] + ([rid2] if rid2 else [])
    return {"je": await c.fetchval("SELECT count(*) FROM journal_entries WHERE tenant_id = $1", tid),
            "bt": await c.fetchval("SELECT count(*) FROM bank_transactions WHERE tenant_id = $1", tid),
            "tautan_aktif": await c.fetchval("SELECT count(*) FROM mutasi_bank_tautan WHERE tenant_id = $1 AND dilepas_pada IS NULL AND mutasi_bank_id IN (SELECT id FROM mutasi_bank WHERE tenant_id = $1 AND bank_account_id = ANY($2::uuid[]))", tid, ids),
            "msl": await c.fetchval("SELECT count(*) FROM bank_transactions WHERE tenant_id = $1 AND bank_account_id = ANY($2::uuid[]) AND matched_statement_line_id IS NOT NULL", tid, ids)}


async def mut(c, tid, rid, arah, nominal, tgl=TGL, ket="TRSF UJI"):
    mid = uuid.uuid4()
    await c.execute("INSERT INTO mutasi_bank (id, tenant_id, bank_account_id, tanggal, keterangan, keterangan_normal, arah, nominal, sidik) VALUES ($1, $2, $3, $4, $5, $5, $6, $7, $8)",
                    mid, tid, rid, tgl, f"{ket} {mid.hex[:6]}", arah, nominal, uuid.uuid4().hex)
    return mid


async def baris_jurnal(c, tid, jid):
    return {(str(r["account_id"]), r["debit"] > 0) for r in await c.fetch(
        "SELECT jl.account_id, jl.debit, jl.credit FROM journal_lines jl JOIN journal_entries e ON e.id = jl.journal_id WHERE e.tenant_id = $1 AND e.id = $2", tid, jid)}


def baris_pratinjau(h):
    return {(j["account_id"], bool(re.search(r"[1-9]", j["debit"]))) for j in h["data"]["jurnal"]}


async def utama():
    pool = await get_db_pool()
    async with pool.acquire() as c:
        db = await c.fetchval("SELECT current_database()")
    if not db.startswith("scratch_bf1c3"):
        print(f"TOLAK: bukan salinan ({db})")
        return 2
    for tid in TENANT:
        # ---------------------------------------------------------------- A: E1-E3 dua pohon
        lama = UT.muat(f"/sig/lama_{tid}.json")
        baru = await UT.jalankan(pool, tid, req, Response, BTR, CD, CreateBankTransferRequest, VoidBankTransferRequest, CD.CreateCustomerDepositRequest, VoidCustomerDepositRequest)
        cek(f"{tid} A E1-E3: transfer (buat+posting+void) dan uang muka (buat+auto_post+void) lewat rute: tanda tangan jurnal/bank_transactions/saldo IDENTIK pohon LAMA vs BARU ({len(baru)} entri)",
            lama == baru, "" if lama == baru else next((f"beda di entri {i}: {a} != {b}" for i, (a, b) in enumerate(zip(lama, baru)) if a != b), "panjang beda"))
        # ---------------------------------------------------------------- B: setup
        async with pool.acquire() as c:
            rid, coa_x = await UT.rekening_kembar(c, tid, "8888880311")
            rid_b, coa_b = await UT.rekening_kembar(c, tid, "8888880312")
            rid_k, coa_k = await UT.rekening_kembar(c, tid, "8888880313", tipe="cash", saldo=0)
            p = await UT.pelanggan(c, tid)
            fakt = list(await c.fetch("SELECT i.id, i.invoice_number, i.customer_id, i.total_amount - i.amount_paid AS sisa FROM sales_invoices i WHERE i.tenant_id = $1 AND i.status IN ('posted', 'partial') "
                                      "AND i.total_amount - i.amount_paid >= 100000 AND (i.total_amount - i.amount_paid) = trunc(i.total_amount - i.amount_paid) "
                                      "AND EXISTS (SELECT 1 FROM customers k WHERE k.tenant_id = i.tenant_id AND k.id = i.customer_id AND k.deleted_at IS NULL) ORDER BY i.invoice_number LIMIT 2", tid))
            dasar = await hitung(c, tid, rid, rid_b)

        async def prev(rek, mid, **b):
            return await panggil(MR.pratinjau_catat(req(tid), rek, mid, CatatRequest(**b)))

        async def catat(rek, mid, kunci=None, **b):
            return await panggil(MR.catat(req(tid, kunci), rek, mid, CatatRequest(**b), Response()))

        async def prev_b(rek, mid, alasan="uji batal"):
            return await panggil(MR.pratinjau_batalkan_catatan(req(tid), rek, mid, BatalkanCatatanRequest(alasan=alasan)))

        async def batal(rek, mid, alasan="uji batal", kunci=None):
            return await panggil(MR.batalkan_catatan(req(tid, kunci), rek, mid, BatalkanCatatanRequest(alasan=alasan), Response()))

        async def status(mid):
            async with pool.acquire() as c:
                return await c.fetchrow("SELECT status, cara FROM mutasi_bank WHERE tenant_id = $1 AND id = $2", tid, mid)

        async def tautan(mid):
            async with pool.acquire() as c:
                return [(r["bank_transaction_id"], r["bank_account_id"], r["dilepas_pada"] is None) for r in await c.fetch(
                    "SELECT t.bank_transaction_id, bt.bank_account_id, t.dilepas_pada FROM mutasi_bank_tautan t JOIN bank_transactions bt ON bt.id = t.bank_transaction_id AND bt.tenant_id = t.tenant_id "
                    "WHERE t.tenant_id = $1 AND t.mutasi_bank_id = $2 ORDER BY t.dibuat_pada", tid, mid)]

        # ---------------------------------------------------------------- T: transfer keluar + masuk
        async with pool.acquire() as c:
            m_out, m_in = await mut(c, tid, rid, "keluar", D("300000")), await mut(c, tid, rid, "masuk", D("200000"))
            pra = await hitung(c, tid, rid, rid_b)
        IZIN["boleh"] = False
        s_d, h_d = await prev(rid, m_out, jenis="transfer", rekening_lawan=rid_b)
        s_dt, h_dt = await catat(rid, m_out, jenis="transfer", rekening_lawan=rid_b)
        async with pool.acquire() as c:
            pas = await hitung(c, tid, rid, rid_b)
        cek(f"{tid} T0 tanpa izin modul tujuan: pratinjau blok IZIN_DITOLAK; tulis ditolak; NOL jurnal", "IZIN_DITOLAK" in kode(h_d["data"]) and s_dt in (400, 403) and pas == pra, f"{s_d} {s_dt} {h_dt}")
        IZIN["boleh"] = True
        s1, pv = await prev(rid, m_out, jenis="transfer", rekening_lawan=rid_b)
        cek(f"{tid} T1 pratinjau transfer keluar: ok, tanpa blok, jurnal Dr B / Cr X, NOL jurnal ditulis",
            s1 == 200 and pv["data"]["ok"] and baris_pratinjau(pv) == {(str(coa_b), True), (str(coa_x), False)} and (await hitung_ulang(pool, tid, rid, rid_b)) == pra, f"{s1} {pv}")
        s2, h2 = await catat(rid, m_out, jenis="transfer", rekening_lawan=rid_b, versi=pv["data"]["payload"]["versi"])
        async with pool.acquire() as c:
            pas = await hitung(c, tid, rid, rid_b)
            bt_ids = [r["id"] for r in await c.fetch("SELECT id FROM bank_transactions WHERE tenant_id = $1 AND journal_id = $2::uuid", tid, UUID(h2["data"]["journal_id"]))] if s2 == 200 else []
            jr = await baris_jurnal(c, tid, UUID(h2["data"]["journal_id"])) if s2 == 200 else set()
        tt = await tautan(m_out)
        st = await status(m_out)
        cek(f"{tid} T2 catat transfer keluar: 200; jurnal == pratinjau; transfer posted (2 bt, sisi X dan B); tautan aktif TEPAT 1 dan HANYA ke bt rekening ini (bukan rekening lawan); baris dicatat/catat",
            s2 == 200 and jr == baris_pratinjau(pv) and pas["je"] == pra["je"] + 1 and pas["bt"] == pra["bt"] + 2 and len(bt_ids) == 2 and len(tt) == 1 and tt[0][2] and tt[0][1] == rid
            and st["status"] == "dicatat" and st["cara"] == "catat" and pas["msl"] == 0, f"{s2} {h2} {pas} {tt}")
        s3, pvi = await prev(rid, m_in, jenis="transfer", rekening_lawan=rid_b)
        s4, h4 = await catat(rid, m_in, jenis="transfer", rekening_lawan=rid_b, versi=pvi["data"]["payload"]["versi"])
        async with pool.acquire() as c:
            jr = await baris_jurnal(c, tid, UUID(h4["data"]["journal_id"])) if s4 == 200 else set()
        tt2 = await tautan(m_in)
        cek(f"{tid} T3 transfer MASUK: arah dibalik (Dr X / Cr B); tautan hanya ke bt rekening X", s4 == 200 and jr == {(str(coa_x), True), (str(coa_b), False)} and len(tt2) == 1 and tt2[0][1] == rid, f"{s4} {h4}")
        s5, h5 = await catat(rid, m_in, jenis="transfer", rekening_lawan=rid_b)
        s6, h6 = await catat(rid, m_in, jenis="transfer", rekening_lawan=rid)
        async with pool.acquire() as c:
            m_x = await mut(c, tid, rid, "masuk", D("1000.50"))
            m_y = await mut(c, tid, rid, "keluar", D("5000"))
        s7, h7 = await catat(rid, m_x, jenis="transfer", rekening_lawan=rid_b)
        s8, h8 = await catat(rid, m_y, jenis="transfer")
        cek(f"{tid} T4 blok berkode: sudah diputus 409 SUDAH_DIPUTUS; lawan = rekening sendiri; nominal berkoma NOMINAL_BERSEN; lawan wajib",
            s5 == 409 and h5["code"] == "SUDAH_DIPUTUS" and h6["code"] in ("SUDAH_DIPUTUS", "TRANSFER_LAWAN_SAMA") and h7["code"] == "NOMINAL_BERSEN" and h8["code"] == "TRANSFER_LAWAN_WAJIB", f"{h5} {h6} {h7} {h8}")
        # batal + void langsung + pembalik
        s9, pb = await prev_b(rid, m_out)
        cek(f"{tid} T5 pratinjau batalkan transfer: tiga kalimat terpisah, jurnal pembalik (Dr X / Cr B), tanpa blok BANK_TX_BERTAUT", s9 == 200 and pb["data"]["ok"] and len(pb["data"]["kalimat_tiga"]) == 3 and baris_pratinjau(pb) == {(str(coa_x), True), (str(coa_b), False)}, f"{s9} {pb}")
        trf_id = (await tautan_dok(pool, tid, m_out))
        s10, h10 = await panggil(BTR.void_bank_transfer(req(tid), trf_id, VoidBankTransferRequest(reason="void langsung"), Response()))
        cek(f"{tid} T6 void LANGSUNG transfer yang masih bertaut dengan baris bank: 409 BANK_TX_BERTAUT (rute nyata modul)", s10 == 409 and h10["code"] == "BANK_TX_BERTAUT", f"{s10} {h10}")
        s11, h11 = await batal(rid, m_out)
        st = await status(m_out)
        tt = await tautan(m_out)
        async with pool.acquire() as c:
            trf = await c.fetchval("SELECT status FROM bank_transfers WHERE tenant_id = $1 AND id = $2", tid, trf_id)
            bal = await hitung(c, tid, rid, rid_b)
        cek(f"{tid} T7 batalkan catatan transfer: 200; transfer void; baris kembali untuk_ditinjau; tautan dilepas (0 aktif untuk baris itu)", s11 == 200 and trf == "void" and st["status"] == "untuk_ditinjau" and not any(x[2] for x in tt), f"{s11} {h11} {trf} {st}")
        s12, h12 = await catat(rid, m_out, jenis="transfer", rekening_lawan=rid_b, kunci=None)
        cek(f"{tid} T8 catat ULANG baris yang sama setelah batal: lolos (kunci idempotensi dokumen berurut, tak bentrok); tautan aktif 1, total 2", s12 == 200 and len([x for x in await tautan(m_out) if x[2]]) == 1 and len(await tautan(m_out)) == 2, f"{s12} {h12}")
        # serentak + replay
        async with pool.acquire() as c:
            m_c = await mut(c, tid, rid, "keluar", D("12000"))
            pra = await hitung(c, tid, rid, rid_b)
        r1, r2 = await asyncio.gather(catat(rid, m_c, kunci=None, jenis="transfer", rekening_lawan=rid_b), catat(rid, m_c, kunci=None, jenis="transfer", rekening_lawan=rid_b))
        async with pool.acquire() as c:
            pas = await hitung(c, tid, rid, rid_b)
        cek(f"{tid} T9 serentak: dua catat bersamaan pada baris yang sama -> TEPAT satu 200 dan satu 409, hanya satu transfer", sorted([r1[0], r2[0]]) == [200, 409] and pas["je"] == pra["je"] + 1 and pas["bt"] == pra["bt"] + 2, f"{r1[0]} {r2[0]} {r2[1] if r2[0] != 200 else r1[1]}")
        async with pool.acquire() as c:
            m_r = await mut(c, tid, rid, "keluar", D("13000"))
            pra = await hitung(c, tid, rid, rid_b)
        k = f"uji-{uuid.uuid4().hex}"
        a1, a2 = await catat(rid, m_r, kunci=k, jenis="transfer", rekening_lawan=rid_b), await catat(rid, m_r, kunci=k, jenis="transfer", rekening_lawan=rid_b)
        async with pool.acquire() as c:
            pas = await hitung(c, tid, rid, rid_b)
        cek(f"{tid} T10 replay kunci idempotensi sama: respons sama, TIDAK ada transfer kedua", a1[0] == 200 and a2[0] == 200 and a1[1]["data"]["journal_id"] == a2[1]["data"]["journal_id"] and pas["je"] == pra["je"] + 1, f"{a1[0]} {a2[0]}")
        # ---------------------------------------------------------------- U: uang muka pelanggan
        async with pool.acquire() as c:
            m_dp = await mut(c, tid, rid, "masuk", D("500000"), ket="DP PELANGGAN")
            m_dpk = await mut(c, tid, rid, "keluar", D("500000"))
            pra = await hitung(c, tid, rid, rid_b)
        sk, hk = await catat(rid, m_dpk, jenis="uang_muka_pelanggan", kontak=str(p["id"]))
        sn, hn = await catat(rid, m_dp, jenis="uang_muka_pelanggan", kontak=str(uuid.uuid4()))
        sw, hw = await catat(rid, m_dp, jenis="uang_muka_pelanggan")
        s1, pv = await prev(rid, m_dp, jenis="uang_muka_pelanggan", kontak=str(p["id"]))
        cek(f"{tid} U1 uang muka: arah keluar ditolak JENIS_TAK_SESUAI_ARAH; pelanggan tak ada PELANGGAN_TAK_ADA; kontak wajib KONTAK_WAJIB; pratinjau ok dengan jurnal Dr X / Cr uang muka pelanggan (nol tulis)",
            hk["code"] == "JENIS_TAK_SESUAI_ARAH" and hn["code"] == "PELANGGAN_TAK_ADA" and hw["code"] == "KONTAK_WAJIB" and s1 == 200 and pv["data"]["ok"] and len(pv["data"]["jurnal"]) == 2
            and (str(coa_x), True) in baris_pratinjau(pv) and (await hitung_ulang(pool, tid, rid, rid_b)) == pra, f"{hk} {hn} {hw} {s1}")
        s2, h2 = await catat(rid, m_dp, jenis="uang_muka_pelanggan", kontak=str(p["id"]), versi=pv["data"]["payload"]["versi"])
        async with pool.acquire() as c:
            pas = await hitung(c, tid, rid, rid_b)
            dep = await c.fetchrow("SELECT status, amount, customer_id FROM customer_deposits WHERE tenant_id = $1 AND journal_id = $2", tid, UUID(h2["data"]["journal_id"])) if s2 == 200 else None
            jr = await baris_jurnal(c, tid, UUID(h2["data"]["journal_id"])) if s2 == 200 else set()
        tt = await tautan(m_dp)
        cek(f"{tid} U2 catat uang muka: 200; jurnal == pratinjau; deposit posted Rp500.000 milik pelanggan terpilih; tautan aktif 1 ke bt rekening X; baris dicatat",
            s2 == 200 and jr == baris_pratinjau(pv) and dep and dep["status"] == "posted" and dep["amount"] == 500000 and dep["customer_id"] == p["id"] and len(tt) == 1 and tt[0][1] == rid and pas["je"] == pra["je"] + 1, f"{s2} {h2} {dep}")
        dep_id = await tautan_dok(pool, tid, m_dp)
        s3, h3 = await panggil(CD.void_customer_deposit(req(tid), dep_id, VoidCustomerDepositRequest(reason="void langsung"), Response()))
        cek(f"{tid} U3 void LANGSUNG uang muka yang masih bertaut: 409 BANK_TX_BERTAUT (rute nyata modul)", s3 == 409 and (h3["code"] == "BANK_TX_BERTAUT"), f"{s3} {h3}")
        s4, pb = await prev_b(rid, m_dp)
        s5, h5 = await batal(rid, m_dp)
        async with pool.acquire() as c:
            rv = await c.fetchval("SELECT id FROM journal_entries WHERE tenant_id = $1 AND reversal_of_id = $2", tid, UUID(h2["data"]["journal_id"]))
            rl = await baris_jurnal(c, tid, rv) if rv else set()
            dst = await c.fetchval("SELECT status FROM customer_deposits WHERE tenant_id = $1 AND id = $2", tid, dep_id)
        cek(f"{tid} U4 batalkan catatan uang muka: pratinjau tanpa blok dan jurnal pembalik == pembalik SUNGGUHAN (jurnal asli ditukar); deposit void; baris kembali untuk_ditinjau; tautan dilepas",
            s4 == 200 and pb["data"]["ok"] and s5 == 200 and rv and baris_pratinjau(pb) == rl and dst == "void" and (await status(m_dp))["status"] == "untuk_ditinjau" and not any(x[2] for x in await tautan(m_dp)), f"{s4} ok={pb['data']['ok']} {pb['data']['blocks']} {s5} {dst} {await status(m_dp)} {await tautan(m_dp)}")
        # ---------------------------------------------------------------- V: terima pembayaran
        if len(fakt) >= 2:
            f1, f2 = fakt[0], fakt[1]
            sisa1, sisa2 = D(f1["sisa"]), D(f2["sisa"])
            async with pool.acquire() as c:
                m_p1 = await mut(c, tid, rid, "masuk", sisa1, ket="BAYAR FAKTUR")
                m_p2 = await mut(c, tid, rid, "masuk", sisa2 + 100000, ket="BAYAR LEBIH")
                m_pk = await mut(c, tid, rid, "keluar", D("1000"))
            sa, ha = await catat(rid, m_p1, jenis="terima_pembayaran", kontak=str(f1["customer_id"]))
            sb, hb = await catat(rid, m_p1, jenis="terima_pembayaran", kontak=str(f1["customer_id"]), alokasi=[AlokasiFaktur(invoice_id=f1["id"], nominal=sisa1 + 1)])
            sc, hc = await catat(rid, m_pk, jenis="terima_pembayaran", kontak=str(f1["customer_id"]), alokasi=[AlokasiFaktur(invoice_id=f1["id"], nominal=D("1000"))])
            cek(f"{tid} V0 blok berkode: alokasi wajib ALOKASI_WAJIB; alokasi melebihi nominal ALOKASI_MELEBIHI_NOMINAL; uang keluar JENIS_TAK_SESUAI_ARAH", ha["code"] == "ALOKASI_WAJIB" and hb["code"] == "ALOKASI_MELEBIHI_NOMINAL" and hc["code"] == "JENIS_TAK_SESUAI_ARAH", f"{ha} {hb} {hc}")
            async with pool.acquire() as c:
                pra = await hitung(c, tid, rid, rid_b)
            s1, pv = await prev(rid, m_p1, jenis="terima_pembayaran", kontak=str(f1["customer_id"]), alokasi=[AlokasiFaktur(invoice_id=f1["id"], nominal=sisa1)])
            cek(f"{tid} V1 pratinjau pembayaran pas: ok, tanpa kelebihan, faktur sesudah ditampilkan, nol tulis", s1 == 200 and pv["data"]["ok"] and pv["data"].get("kelebihan") is None and pv["data"].get("faktur_sesudah") and (await hitung_ulang(pool, tid, rid, rid_b)) == pra, f"{s1} {pv}")
            s2, h2 = await catat(rid, m_p1, jenis="terima_pembayaran", kontak=str(f1["customer_id"]), alokasi=[AlokasiFaktur(invoice_id=f1["id"], nominal=sisa1)], versi=pv["data"]["payload"]["versi"])
            async with pool.acquire() as c:
                inv = await c.fetchrow("SELECT status, amount_paid, total_amount FROM sales_invoices WHERE tenant_id = $1 AND id = $2", tid, f1["id"])
            tt = await tautan(m_p1)
            cek(f"{tid} V2 catat pembayaran: 200; faktur {f1['invoice_number']} LUNAS (paid, amount_paid==total); tautan aktif 1 ke bt rekening X", s2 == 200 and inv["status"] == "paid" and inv["amount_paid"] == inv["total_amount"] and len(tt) == 1 and tt[0][1] == rid, f"{s2} {h2} {inv}")
            s3, h3 = await prev_b(rid, m_p1)
            pay_id = await tautan_dok(pool, tid, m_p1)
            s4, h4 = await panggil(RP.void_receive_payment(req(tid), pay_id, RP.VoidPaymentRequest(void_reason="void langsung"), Response()))
            cek(f"{tid} V3 void LANGSUNG pembayaran yang masih bertaut: 409 BANK_TX_BERTAUT (rute nyata modul)", s4 == 409 and h4["code"] == "BANK_TX_BERTAUT", f"{s4} {h4}")
            s5, h5 = await batal(rid, m_p1)
            async with pool.acquire() as c:
                inv = await c.fetchrow("SELECT status, amount_paid, total_amount FROM sales_invoices WHERE tenant_id = $1 AND id = $2", tid, f1["id"])
                rv = await c.fetchval("SELECT id FROM journal_entries WHERE tenant_id = $1 AND reversal_of_id = (SELECT journal_id FROM bank_transactions WHERE tenant_id = $1 AND id = $2)", tid, tt[0][0])
                rl = await baris_jurnal(c, tid, rv) if rv else set()
            cek(f"{tid} V4 batalkan catatan pembayaran: pratinjau tanpa blok, jurnal pembalik == pembalik SUNGGUHAN; faktur kembali terutang; baris untuk_ditinjau", s3 == 200 and pb is not None and h3["data"]["ok"] and s5 == 200 and baris_pratinjau(h3) == rl and inv["amount_paid"] == inv["total_amount"] - f1["sisa"] and (await status(m_p1))["status"] == "untuk_ditinjau", f"{s3} ok={h3['data']['ok']} {h3['data']['blocks']} {s5} {inv} {baris_pratinjau(h3)} vs {rl}")
            s6, pv2 = await prev(rid, m_p2, jenis="terima_pembayaran", kontak=str(f2["customer_id"]), alokasi=[AlokasiFaktur(invoice_id=f2["id"], nominal=sisa2)])
            cek(f"{tid} V5 pratinjau kelebihan: kelebihan Rp100.000 DITAMPILKAN (jadi uang muka pelanggan)", s6 == 200 and pv2["data"]["ok"] and pv2["data"]["kelebihan"] and D(pv2["data"]["kelebihan"]["jumlah"]) == D("100000") and "uang muka" in pv2["data"]["kalimat"].lower(), f"{s6} {pv2}")
            s7, h7 = await catat(rid, m_p2, jenis="terima_pembayaran", kontak=str(f2["customer_id"]), alokasi=[AlokasiFaktur(invoice_id=f2["id"], nominal=sisa2)], versi=pv2["data"]["payload"]["versi"])
            async with pool.acquire() as c:
                ov = await c.fetchval("SELECT count(*) FROM customer_deposits WHERE tenant_id = $1 AND customer_id = $2 AND amount = 100000 AND status = 'posted' AND created_at > now() - interval '5 minutes'", tid, f2["customer_id"])
            cek(f"{tid} V6 catat pembayaran lebih: 200; uang muka kelebihan Rp100.000 terbentuk", s7 == 200 and ov >= 1, f"{s7} {h7} ov={ov}")
        else:
            print(f"INFO {tid}: faktur terutang <2 di salinan -> bagian V (terima pembayaran) dilewati", flush=True)
        # ---------------------------------------------------------------- K: kas minus (kontrol) + periode tertutup (kontrol)
        async with pool.acquire() as c:
            m_ki = await mut(c, tid, rid_k, "masuk", D("100000"))
            m_ko = await mut(c, tid, rid_k, "keluar", D("100000"))
            m_k1 = await mut(c, tid, rid_k, "keluar", D("1"))
        sa, ha = await prev(rid_k, m_ko, jenis="transfer", rekening_lawan=rid_b)
        cek(f"{tid} K1 transfer keluar dari KAS tanpa saldo: pratinjau blok KAS_MINUS", "KAS_MINUS" in kode(ha["data"]), f"{sa} {kode(ha['data'])}")
        sb, hb = await catat(rid_k, m_ki, jenis="transfer", rekening_lawan=rid_b)
        sc, hc = await catat(rid_k, m_ko, jenis="transfer", rekening_lawan=rid_b)
        sd, hd = await catat(rid_k, m_k1, jenis="transfer", rekening_lawan=rid_b)
        cek(f"{tid} K2 kontrol: kas masuk dari B lalu keluar PAS saldo lolos; keluar Rp1 lagi -> KAS_MINUS (bukan 'selalu blok')", ha is not None and sb == 200 and sc in (200, 400) and sd == 400 and hd["code"] == "KAS_MINUS", f"{sb} {sc} {hc} {sd} {hd}")
        async def periode(status):
            async with pool.acquire() as c:
                await c.execute("UPDATE fiscal_periods SET status = $3, closed_at = CASE WHEN $3 = 'CLOSED' THEN now() ELSE NULL END WHERE tenant_id = $1 AND start_date <= $2 AND end_date >= $2", tid, TGL, status)
        async with pool.acquire() as c:
            m_pt = await mut(c, tid, rid, "keluar", D("7000"))
            m_pb = await mut(c, tid, rid, "keluar", D("8000"))
        s_ok, h_ok = await catat(rid, m_pb, jenis="transfer", rekening_lawan=rid_b)
        await periode("CLOSED")
        async with pool.acquire() as c:
            pra = await hitung(c, tid, rid, rid_b)
        s_pv, h_pv = await prev(rid, m_pt, jenis="transfer", rekening_lawan=rid_b)
        s_pc, h_pc = await catat(rid, m_pt, jenis="transfer", rekening_lawan=rid_b)
        s_bc, h_bc = await batal(rid, m_pb)
        async with pool.acquire() as c:
            pas = await hitung(c, tid, rid, rid_b)
        cek(f"{tid} P1 periode TERTUTUP: catat transfer -> blok PERIODE_TERTUTUP (pratinjau) dan ditolak (tulis); batalkan catatan lama ditolak; NOL jurnal/bt; baris tetap", s_ok == 200 and "PERIODE_TERTUTUP" in "".join(kode(h_pv["data"])) + str(h_pc) and s_pc in (400, 403) and s_bc in (400, 403) and pas["je"] == pra["je"] and pas["bt"] == pra["bt"] and (await status(m_pt))["status"] == "untuk_ditinjau" and (await status(m_pb))["status"] == "dicatat", f"{h_pv} {s_pc} {h_pc} {s_bc} {h_bc}")
        await periode("OPEN")
        s_po, _ = await catat(rid, m_pt, jenis="transfer", rekening_lawan=rid_b)
        s_bo, _ = await batal(rid, m_pb)
        cek(f"{tid} P2 kontrol: periode dibuka kembali -> catat dan batalkan yang sama lolos (blok memang karena periode)", s_po == 200 and s_bo == 200)
        async with pool.acquire() as c:
            akhir = await hitung(c, tid, rid, rid_b)
        cek(f"{tid} Z invariants: matched_statement_line_id tetap 0 (Bank Feed tak menulis kolom rekonsiliasi lama)", akhir["msl"] == 0 and dasar["msl"] == 0)
    n = len(HASIL)
    print(f"HASIL {sum(HASIL)}/{n} lulus", flush=True)
    return 0 if all(HASIL) and n else 1


async def hitung_ulang(pool, tid, rid, rid_b):
    async with pool.acquire() as c:
        return await hitung(c, tid, rid, rid_b)


async def tautan_dok(pool, tid, mid):
    """ID dokumen modul (source_id jurnal) dari tautan aktif baris mutasi."""
    async with pool.acquire() as c:
        return UUID(str(await c.fetchval("SELECT je.source_id FROM mutasi_bank_tautan t JOIN bank_transactions bt ON bt.id = t.bank_transaction_id AND bt.tenant_id = t.tenant_id "
                                         "JOIN journal_entries je ON je.id = bt.journal_id AND je.tenant_id = bt.tenant_id WHERE t.tenant_id = $1 AND t.mutasi_bank_id = $2 AND t.dilepas_pada IS NULL "
                                         "ORDER BY t.dibuat_pada LIMIT 1", tid, mid)))


sys.exit(asyncio.run(utama()))
