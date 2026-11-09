"""Kelola rekening Kas&Bank standar SO + CW -- uji di SALINAN: SATU dump, DUA klon, handler ASLI pohon LAMA (master) dan BARU, urutan
langkah IDENTIK di kedua klon (tulis HANYA di klon). Syarat MASTER 10 Nov:
 C  buat: CoA tertulis IDENTIK lama vs baru (kode, nama, induk, tipe, saldo normal, is_cash, is_header, is_active, deskripsi) untuk
    bank/cash/petty_cash/e_wallet/credit_card, dengan & tanpa saldo awal; jurnal saldo awal + mutasi opening identik; respons identik
    (selain id); galat buat (nama dipakai, CoA tertaut salah tipe/sudah tertaut) status+kalimat identik.
 V  (baru) pratinjau == tulis: CoA rencana == CoA tertulis; jurnal pratinjau == baris jurnal tertulis; saldo sesudah == saldo jurnal;
    blok pertama pratinjau == galat penulis; NOL tulisan (hitungan baris tenant 6 tabel sebelum == sesudah pratinjau).
 U  ubah: status+kalimat identik lama vs baru; baris rekening sesudah identik; (baru) perubahan pratinjau == selisih baris nyata.
 H  hapus SEMUA rekening kaos/nyawit + rekening uji: akibat DB identik (ada? aktif? CoA ada?) lama vs baru; yang lama 500 -> baru 400
    REKENING_BERSALDO_AWAL; (baru) akibat pratinjau == akibat nyata; aksi_tersedia.hapus == pratinjau.
 I  (baru) X-Idempotency-Key: tap kedua = jawaban sama, rekening tak berganda."""
import asyncio
import inspect
import json
import os
import sys
import uuid

from fastapi import HTTPException
from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import bank_accounts as R  # noqa: E402
from app.schemas.bank_accounts import CreateBankAccountRequest, UpdateBankAccountRequest  # noqa: E402

TENANT = tuple(os.environ.get("TENANT", "kaos-biru-konveksi,pt-kita-nyawit-bersama").split(","))
BARU = hasattr(R, "preview_create_bank_account")
USER = "00000000-0000-0000-0000-0000000000a1"
CEK, OUT = [], {"buat": {}, "ubah": {}, "hapus": {}}
TABEL = ("bank_accounts", "chart_of_accounts", "journal_entries", "bank_transactions", "reconciliation_sessions")


def cek(nama, ok, info=""):
    CEK.append([nama, bool(ok), "" if ok else str(info)[:600]])


def req(tid, kunci=None):
    h = [(b"x-idempotency-key", kunci.encode())] if kunci else []
    r = Request({"type": "http", "method": "POST", "path": "/", "headers": h, "query_string": b""})
    r.state.user = {"tenant_id": tid, "user_id": USER, "role": "OWNER"}
    return r


def j(v):
    return json.loads(json.dumps(v, default=str))


async def panggil(fn, *a, **kw):
    if "response" in inspect.signature(fn).parameters:
        from starlette.responses import Response
        kw["response"] = Response()
    try:
        return 200, j(await fn(*a, **kw))
    except HTTPException as e:
        return e.status_code, j(e.detail)
    except Exception as e:  # noqa: BLE001 -- 500 lama (galat DB) dicatat sebagai kelas
        return 500, type(e).__name__


async def hitung(conn, tid):
    return {t: await conn.fetchval(f"SELECT count(*) FROM {t} WHERE tenant_id = $1", tid) for t in TABEL} | {
        "journal_lines": await conn.fetchval("SELECT count(*) FROM journal_lines jl JOIN journal_entries je ON je.id = jl.journal_id "
                                             "WHERE je.tenant_id = $1", tid)}


async def potret_rekening(conn, tid, nama):
    ba = await conn.fetchrow("SELECT * FROM bank_accounts WHERE tenant_id = $1 AND account_name = $2", tid, nama)
    if not ba:
        return None
    coa = await conn.fetchrow("""SELECT account_code, name, parent_code, account_type, normal_balance, is_cash, is_header, is_active, description
                                 FROM chart_of_accounts WHERE id = $1 AND tenant_id = $2""", ba["coa_id"], tid)
    jl = await conn.fetch("""SELECT c.account_code, jl.debit, jl.credit, jl.memo, je.source_type, je.status, je.journal_date
                             FROM journal_entries je JOIN journal_lines jl ON jl.journal_id = je.id JOIN chart_of_accounts c ON c.id = jl.account_id
                             WHERE je.tenant_id = $1 AND je.source_id = $2 ORDER BY jl.line_number""", tid, ba["id"])
    bt = await conn.fetch("""SELECT transaction_type, amount, reference_type, description FROM bank_transactions
                             WHERE tenant_id = $1 AND bank_account_id = $2 ORDER BY created_at""", tid, ba["id"])
    saldo = await conn.fetchval("""SELECT COALESCE(SUM(jl.debit) - SUM(jl.credit), 0) FROM journal_lines jl JOIN journal_entries je ON je.id = jl.journal_id
                                   WHERE jl.account_id = $1 AND je.tenant_id = $2 AND je.status = 'POSTED'""", ba["coa_id"], tid)
    lepas = {"id", "coa_id", "created_at", "updated_at", "created_by"}
    return j({"rekening": {k: v for k, v in dict(ba).items() if k not in lepas}, "coa": dict(coa) if coa else None,
              "jurnal": [dict(x) for x in jl], "mutasi": [dict(x) for x in bt], "saldo": saldo, "_id": ba["id"], "_coa_id": ba["coa_id"]})


def kasus_buat(tid):
    s = tid[:4]
    k = [(f"UJI-REK-{t}-{ob}-{s}", {"account_type": t, "opening_balance": ob}) for t in ("bank", "cash", "petty_cash", "e_wallet", "credit_card")
         for ob in (0, 1250000)]
    return k


async def langkah_buat(pool, tid):
    for nama, isi in kasus_buat(tid) + [("UJI-REK-bank-0-" + tid[:4], {"account_type": "bank"})]:  # terakhir = nama dipakai
        body = CreateBankAccountRequest(account_name=nama, **isi)
        k = f"{tid}|{nama}|{isi}"
        if BARU:
            async with pool.acquire() as conn:
                sebelum = await hitung(conn, tid)
            pv = (await R.preview_create_bank_account(req(tid), body))["data"]
            async with pool.acquire() as conn:
                cek(f"V {k} nol tulisan", await hitung(conn, tid) == sebelum, "hitungan berubah")
        st, resp = await panggil(R.create_bank_account, req(tid), body)
        async with pool.acquire() as conn:
            p = await potret_rekening(conn, tid, nama) if st in (200, 201) else None
        OUT["buat"][k] = {"status": st, "resp": {kk: v for kk, v in (resp.get("data") or {}).items() if kk not in ("id", "coa_id", "journal_id")}
                          if isinstance(resp, dict) else resp, "potret": {kk: v for kk, v in (p or {}).items() if not kk.startswith("_")}}
        if BARU:
            if st >= 400:
                pesan = resp if isinstance(resp, str) else (resp.get("message") if isinstance(resp, dict) and "message" in resp else resp)
                cek(f"V {k} blok pertama == galat penulis", pv["blocks"] and pv["blocks"][0]["message"] == pesan, (pv["blocks"], resp))
            else:
                cek(f"V {k} pratinjau ok", pv["ok"], pv["blocks"])
                c = p["coa"]
                cek(f"V {k} CoA rencana == tertulis", (pv["coa"]["account_code"], pv["coa"]["name"], pv["coa"]["account_type"]) ==
                    (c["account_code"], c["name"], c["account_type"]), (pv["coa"], c))
                cek(f"V {k} jurnal pratinjau == tertulis", [(x["debit"], x["credit"], x["memo"]) for x in pv["jurnal"]] ==
                    [(f"{float(x['debit']):.2f}", f"{float(x['credit']):.2f}", x["memo"]) for x in p["jurnal"]], (pv["jurnal"], p["jurnal"]))
                cek(f"V {k} saldo sesudah == jurnal", float(pv["saldo"][0]["sesudah"]) == float(p["saldo"]), (pv["saldo"], p["saldo"]))
    if BARU:  # I: idempotensi
        body = CreateBankAccountRequest(account_name=f"UJI-REK-IDEM-{tid[:4]}", account_type="bank", opening_balance=5000)
        kunci = str(uuid.uuid4())
        a = await panggil(R.create_bank_account, req(tid, kunci), body)
        b = await panggil(R.create_bank_account, req(tid, kunci), body)
        async with pool.acquire() as conn:
            n = await conn.fetchval("SELECT count(*) FROM bank_accounts WHERE tenant_id = $1 AND account_name = $2", tid, body.account_name)
        cek(f"I {tid} tap kedua = jawaban sama, satu rekening", a == b and n == 1, (a, b, n))


async def langkah_ubah(pool, tid):
    async with pool.acquire() as conn:
        ba = await conn.fetchrow("SELECT id FROM bank_accounts WHERE tenant_id = $1 AND account_name = $2", tid, f"UJI-REK-bank-1250000-{tid[:4]}")
    ubah = [("ganti nama", {"account_name": f"UJI-REK-bank-ubah-{tid[:4]}", "bank_branch": "Cabang Uji", "notes": ""}),
            ("nama dipakai", {"account_name": f"UJI-REK-cash-0-{tid[:4]}"}), ("nama kosong", {"account_name": " "}),
            ("ke kartu kredit", {"account_type": "credit_card"}), ("jadikan utama", {"is_default": True}),
            ("nonaktifkan utama bersaldo", {"is_active": False}), ("aktifkan", {"is_active": True}), ("kosong", {})]
    for label, isi in ubah:
        body = UpdateBankAccountRequest(**isi)
        k = f"{tid}|{label}"
        async with pool.acquire() as conn:
            sebelum = dict(await conn.fetchrow("SELECT * FROM bank_accounts WHERE id = $1 AND tenant_id = $2", ba["id"], tid))
            n0 = await hitung(conn, tid)
        pv = None
        if BARU:
            pv = (await R.preview_update_bank_account(req(tid), ba["id"], body))["data"]
            async with pool.acquire() as conn:
                cek(f"V {k} nol tulisan", await hitung(conn, tid) == n0, "hitungan berubah")
        st, resp = await panggil(R.update_bank_account, req(tid), ba["id"], body)
        async with pool.acquire() as conn:
            sesudah = dict(await conn.fetchrow("SELECT * FROM bank_accounts WHERE id = $1 AND tenant_id = $2", ba["id"], tid))
        OUT["ubah"][k] = {"status": st, "resp": resp, "baris": {kk: v for kk, v in j(sesudah).items() if kk not in ("id", "coa_id", "updated_at", "created_at")}}
        if BARU:
            if st >= 400:
                pesan = resp.get("message") if isinstance(resp, dict) else resp
                cek(f"V {k} blok pertama == galat penulis", pv["blocks"] and pv["blocks"][0]["message"] == pesan, (pv["blocks"], resp))
            else:
                nyata = sorted(m for m in sesudah if m not in ("updated_at",) and sesudah[m] != sebelum[m])
                cek(f"V {k} perubahan pratinjau == selisih nyata", sorted(p["medan"] for p in pv["perubahan"]) == nyata, (pv["perubahan"], nyata))
                if label == "nonaktifkan utama bersaldo":
                    cek(f"V {k} peringatan utama + bersaldo", len(pv["peringatan"]) == 2, pv["peringatan"])


async def langkah_hapus(pool, tid):
    async with pool.acquire() as conn:
        semua = await conn.fetch("SELECT id, account_name FROM bank_accounts WHERE tenant_id = $1 ORDER BY account_name", tid)
    for ba in semua:
        k = f"{tid}|{ba['account_name']}"
        pv = aks = None
        if BARU:
            async with pool.acquire() as conn:
                n0 = await hitung(conn, tid)
            pv = (await R.preview_delete_bank_account(req(tid), ba["id"]))["data"]
            aks = (await R.get_bank_account(req(tid), ba["id"]))
            aks = j(aks)["data"]["aksi_tersedia"]["hapus"] if isinstance(aks, dict) else j(aks.model_dump())["data"]["aksi_tersedia"]["hapus"]
            async with pool.acquire() as conn:
                cek(f"V {k} nol tulisan", await hitung(conn, tid) == n0, "hitungan berubah")
        async with pool.acquire() as conn:
            coa = await conn.fetchval("SELECT coa_id FROM bank_accounts WHERE id = $1 AND tenant_id = $2", ba["id"], tid)
        st, resp = await panggil(R.delete_bank_account, req(tid), ba["id"])
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT is_active FROM bank_accounts WHERE id = $1 AND tenant_id = $2", ba["id"], tid)
            ada_coa = await conn.fetchval("SELECT count(*) FROM chart_of_accounts WHERE id = $1 AND tenant_id = $2", coa, tid)
        akibat = "permanen" if row is None else ("nonaktifkan" if not row["is_active"] else "tetap")
        OUT["hapus"][k] = {"status": st, "akibat_db": akibat, "coa_ada": ada_coa, "resp": resp if st >= 400 else None}
        if BARU:
            if st >= 400:
                pesan = resp.get("message") if isinstance(resp, dict) else resp
                cek(f"V {k} blok pratinjau == galat penulis", pv["blocks"] and pv["blocks"][0]["message"] == pesan and pv["akibat"] is None, (pv, resp))
                cek(f"V {k} aksi_tersedia.hapus == pratinjau", aks == {"tersedia": False, "alasan": pv["blocks"][0]["message"]}, aks)
            else:
                cek(f"V {k} akibat pratinjau == nyata", pv["akibat"] == akibat, (pv["akibat"], akibat))
                cek(f"V {k} aksi_tersedia.hapus tersedia", aks["tersedia"] is True, aks)


async def main():
    pool = await R.get_pool()
    for tid in TENANT:
        await langkah_buat(pool, tid)
        await langkah_ubah(pool, tid)
        await langkah_hapus(pool, tid)
    OUT["pohon"] = "baru" if BARU else "lama"
    OUT["cek"] = CEK
    with open(os.environ["KELUAR"], "w") as fo:
        json.dump(OUT, fo, default=str)
    gagal = [c for c in CEK if not c[1]]
    for c in gagal[:40]:
        print("[GAGAL]", c[0], c[2], flush=True)
    print(f"[{OUT['pohon']}] cek {len(CEK) - len(gagal)}/{len(CEK)} lulus; buat {len(OUT['buat'])} ubah {len(OUT['ubah'])} hapus {len(OUT['hapus'])}", flush=True)


asyncio.run(main())
