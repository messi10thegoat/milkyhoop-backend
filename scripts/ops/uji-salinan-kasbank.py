"""KAS & BANK standar SO -- uji di SALINAN (klon milkydb, SATU klon per pohon dari dump yang SAMA): handler ASLI rekening, transfer, uang
masuk/keluar dari pohon LAMA dan BARU. Tulis HANYA di salinan (dihapus trap). JSON per pohon -> banding-kasbank.py.
 A baca: daftar/detail/ringkasan rekening + transfer (baru = lama + medan aditif)
 B BARU: daftar uang masuk/keluar == mutasi manual sah (kueri bebas) + neto == jurnal; ?aksi= == aksi_semua/predikat Python per baris;
         {k}_count/_value == daftar ?aksi=; with_totals == Σ baris; medan baru lolos skema respons; riwayat/dokumen/aksi_tersedia semua baris
 G BARU: sidik tabel kas/bank + jurnal sebelum/sesudah semua pratinjau void (nol tulis)
 C tulis void 3 mutasi manual + 3 transfer posted + 1 draf: efek (jurnal pembalik, cermin, status) IDENTIK lama vs baru;
   BARU: pratinjau == tulis (baris jurnal, saldo sesudah == saldo jurnal sesudah void); void kedua ditolak (status sama)."""
import asyncio
import json
import os
import sys
from decimal import Decimal

from fastapi import HTTPException
from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import bank_accounts as BA, bank_transfers as BT, kasbank_v2 as V2  # noqa: E402
from app.schemas.bank_transfers import VoidBankTransferRequest  # noqa: E402
from app.schemas.kasbank_v2 import VoidTransactionRequest  # noqa: E402

TENANT = tuple(os.environ.get("TENANT", "kaos-biru-konveksi,pt-kita-nyawit-bersama").split(","))
BARU = hasattr(BT, "preview_void_bank_transfer")
if BARU:
    from app.routers import bank_transactions as BTX  # noqa: E402
    from app.services import kasbank_daftar as KD  # noqa: E402
    from app.utils.tanggal_tenant import tanggal_dokumen  # noqa: E402
CEK = []


def cek(nama, ok, info=""):
    CEK.append([nama, bool(ok), "" if ok else str(info)[:400]])


def req(tid, method="GET"):
    r = Request({"type": "http", "method": method, "path": "/", "headers": []})
    r.state.user = {"tenant_id": tid, "user_id": "00000000-0000-0000-0000-000000000001", "username": "uji-salinan-kasbank"}
    return r


def j(v):
    return json.loads(json.dumps(v, default=lambda o: o.model_dump() if hasattr(o, "model_dump") else str(o)))


async def coba(f):
    try:
        return {"ok": True, "hasil": j(await f)}
    except HTTPException as e:
        return {"ok": False, "status": e.status_code, "detail": j(e.detail), "code": getattr(e, "code", None)}


async def sidik(conn, tid):
    return {t: await conn.fetchval(f"SELECT md5(COALESCE(string_agg(x::text, '|' ORDER BY x.id), '')) FROM {t} x WHERE x.tenant_id = $1", tid)
            for t in ("bank_transactions", "bank_transfers", "journal_entries", "bank_accounts")}


async def saldo_coa(conn, tid, coa):
    return await conn.fetchval("SELECT COALESCE(SUM(jl.debit) - SUM(jl.credit), 0) FROM journal_lines jl JOIN journal_entries je ON "
                               "je.id = jl.journal_id WHERE je.tenant_id = $1 AND je.status = 'POSTED' AND jl.account_id = $2", tid, coa)


SQL_MANUAL_BEBAS = """SELECT bt.id, bt.status, je.reversed_by_id FROM bank_transactions bt
JOIN journal_entries je ON je.id = bt.journal_id AND je.tenant_id = bt.tenant_id
WHERE bt.tenant_id = $1 AND bt.origin_type = 'MANUAL' AND bt.reference_type IS NULL AND bt.reference_id IS NULL
  AND je.source_type = 'BANK_TRANSACTION' AND je.source_id = bt.id ORDER BY bt.created_at, bt.id"""

SQL_NETO_MANUAL = """SELECT COALESCE(SUM(jl.debit - jl.credit), 0) FROM journal_lines jl
JOIN journal_entries je ON je.id = jl.journal_id AND je.tenant_id = $1
JOIN bank_accounts ba ON ba.coa_id = jl.account_id AND ba.tenant_id = je.tenant_id
WHERE je.id = ANY($2::uuid[])"""


async def semua(f, **kw):
    """Daftar lengkap lewat halaman 100."""
    items, skip = [], 0
    while True:
        h = await f(skip=skip, limit=100, **kw)
        items += h["items"]
        if not h["has_more"]:
            return items, h
        skip += 100


async def baca(conn, tid, o):
    """A: baca dua pohon (argumen bersama saja)."""
    bas, _ = await semua(lambda **k: BA.list_bank_accounts(req(tid), is_active=None, account_type=None, search=None, sort_by="account_name",
                                                           sort_order="asc", **k))
    o["A_ba"] = {b["id"]: j(b) for b in bas}
    for b in bas:
        o[f"A_bad:{b['id']}"] = j((await BA.get_bank_account(req(tid), b["id"]))["data"])
    o["A_bas"] = j((await BA.ringkasan_kas_bank(req(tid)))["data"])
    trs, _ = await semua(lambda **k: BT.list_bank_transfers(req(tid), status="all", from_bank_id=None, to_bank_id=None, search=None, date_from=None,
                                                            date_to=None, sort_by="created_at", sort_order="desc", **k))
    o["A_trf"] = {t["id"]: j(t) for t in trs}
    for t in trs:
        o[f"A_trfd:{t['id']}"] = j((await BT.get_bank_transfer(req(tid), t["id"]))["data"])
    o["A_trfs"] = j((await BT.get_bank_transfers_summary(req(tid)))["data"])
    return bas, trs


async def baru_saja(conn, tid, o, bas, trs):
    """B + G (pohon BARU)."""
    hari = await tanggal_dokumen(conn, tid)
    bulan = (hari.year, hari.month)
    # ---- B1 uang masuk/keluar
    bebas = await conn.fetch(SQL_MANUAL_BEBAS, tid)
    lst = lambda **k: BTX.list_bank_transactions(req(tid), arah=k.pop("arah", None), bank_account_id=None, status=k.pop("status", "semua"),  # noqa
                                                 date_from=None, date_to=None, search=None, aksi=k.pop("aksi", None),
                                                 with_totals=k.pop("with_totals", False), sort_by="tanggal", sort_order="desc", **k)
    m_semua, _ = await semua(lst, status="semua")
    cek(f"{tid} B1 daftar semua == mutasi manual sah", sorted(x["id"] for x in m_semua) == sorted(str(r["id"]) for r in bebas),
        (len(m_semua), len(bebas)))
    tercatat = [r for r in bebas if r["reversed_by_id"] is None and r["status"] != "VOIDED"]
    m_ter, h_ter = await semua(lst, status="tercatat", with_totals=True)
    cek(f"{tid} B1 bawaan tercatat == tanpa batal", sorted(x["id"] for x in m_ter) == sorted(str(r["id"]) for r in tercatat))
    jids = [x["journal_id"] for x in m_ter]
    neto = await conn.fetchval(SQL_NETO_MANUAL, tid, jids) if jids else Decimal(0)
    cek(f"{tid} B1 sum_neto == jurnal CoA rekening", Decimal(h_ter["sum_neto"]) == Decimal(neto), (h_ter["sum_neto"], neto))
    cek(f"{tid} B1 sum_masuk - sum_keluar == Σ baris", Decimal(h_ter["sum_masuk"]) == sum(
        (Decimal(x["amount"]) for x in m_ter if x["arah"] == "masuk"), Decimal(0)) and Decimal(h_ter["sum_keluar"]) == sum(
        (Decimal(x["amount"]) for x in m_ter if x["arah"] == "keluar"), Decimal(0)))
    ring = (await BTX.bank_transactions_summary(req(tid), search=None))["data"]
    for k, arah in (("masuk_bulan_ini", "masuk"), ("keluar_bulan_ini", "keluar")):
        py = {x["id"] for x in m_semua if x["status"] == "tercatat" and x["arah"] == arah
              and tuple(map(int, x["transaction_date"].split("-")[:2])) == bulan}
        sq, _ = await semua(lst, status="semua", aksi=k)
        cek(f"{tid} B1 ?aksi={k} == predikat Python", {x["id"] for x in sq} == py, (len(sq), len(py)))
        cek(f"{tid} B1 {k}_count/_value == daftar", ring[f"{k}_count"] == len(py) and Decimal(ring[f"{k}_value"]) == sum(
            (Decimal(x["amount"]) for x in m_semua if x["id"] in py), Decimal(0)), (ring[f"{k}_count"], len(py)))
    cek(f"{tid} B1 total_count == tercatat", ring["total_count"] == len(tercatat))
    for x in m_semua:
        d = (await BTX.get_bank_transaction(req(tid), x["id"]))["data"]
        h = (await BTX.get_bank_transaction_history(req(tid), x["id"], limit=200))["data"]
        dk = (await BTX.get_bank_transaction_documents(req(tid), x["id"]))["data"]
        cek(f"{tid} B1 detail/riwayat/dokumen {x['nomor']}", list(d["aksi_tersedia"]) == ["batalkan", "ubah"]
            and h["events"] and dk["anchor"]["position_text"] == x["position_text"]
            and (d["aksi_tersedia"]["batalkan"]["tersedia"] is False) == (x["status"] == "dibatalkan" or bool(
                d["aksi_tersedia"]["batalkan"]["alasan"])), (d["aksi_tersedia"], h["total"]))
    o["B1_n"] = [len(m_semua), len(m_ter)]
    # ---- B2 transfer
    tl = lambda **k: BT.list_bank_transfers(req(tid), status="all", from_bank_id=None, to_bank_id=None, search=None, date_from=None,  # noqa
                                            date_to=None, sort_by="created_at", sort_order="desc", aksi=k.pop("aksi", None),
                                            with_totals=k.pop("with_totals", False), **k)
    t_all, h_all = await semua(tl, with_totals=True)
    tring = (await BT.get_bank_transfers_summary(req(tid), search=None))["data"]
    for k, py in (("draf", {t["id"] for t in t_all if "draf" in t["aksi_semua"]}),
                  ("bulan_ini", {t["id"] for t in t_all if t["status"] != "void" and tuple(map(int, t["transfer_date"].split("-")[:2])) == bulan})):
        sq, _ = await semua(tl, aksi=k)
        cek(f"{tid} B2 ?aksi={k} == Python", {t["id"] for t in sq} == py, (len(sq), len(py)))
        cek(f"{tid} B2 {k}_count/_value == daftar", tring[f"{k}_count"] == len(py) and Decimal(tring[f"{k}_value"]) == sum(
            (Decimal(t["amount"]) for t in t_all if t["id"] in py), Decimal(0)))
    cek(f"{tid} B2 sum_total == Σ non-void", Decimal(h_all["sum_total"]) == sum((Decimal(t["amount"]) for t in t_all if t["status"] != "void"),
                                                                                 Decimal(0)))
    cek(f"{tid} B2 skema daftar memuat medan baru", all({"position_text", "aksi_semua", "documents"} <= set(
        BT.BankTransferListResponse.model_validate(h_all).model_dump()["items"][i]) for i in range(len(t_all))))
    for t in t_all:
        d = await BT.get_bank_transfer(req(tid), t["id"])
        dd = BT.BankTransferDetailResponse.model_validate(d).model_dump()["data"]
        h = (await BT.get_bank_transfer_history(req(tid), t["id"], limit=200))["data"]
        dk = (await BT.get_bank_transfer_documents(req(tid), t["id"]))["data"]
        cek(f"{tid} B2 detail/riwayat/dokumen {t['transfer_number']}", list(dd["aksi_tersedia"]) == ["posting", "ubah", "hapus", "batalkan"]
            and dd["position_text"] == t["position_text"] == dk["anchor"]["position_text"] and h["events"]
            and len([x for g in dk["groups"] for x in g["docs"]]) == t["doc_count"], (dd["aksi_tersedia"], t["doc_count"]))
    # ---- B3 rekening
    al = lambda **k: BA.list_bank_accounts(req(tid), is_active=None, account_type=None, search=None, sort_by="account_name", sort_order="asc",  # noqa
                                           aksi=k.pop("aksi", None), with_totals=k.pop("with_totals", False), **k)
    a_all, ha = await semua(al, with_totals=True)
    aring = (await BA.ringkasan_kas_bank(req(tid)))["data"]
    for k, py in (("saldo_minus", {a["id"] for a in a_all if "saldo_minus" in a["aksi_semua"]}),
                  ("nonaktif", {a["id"] for a in a_all if not a["is_active"]})):
        sq, _ = await semua(al, aksi=k)
        cek(f"{tid} B3 ?aksi={k} == Python", {a["id"] for a in sq} == py)
        cek(f"{tid} B3 {k}_count == daftar", aring[f"{k}_count"] == len(py))
    cek(f"{tid} B3 sum_saldo == Σ non-kartu", Decimal(ha["sum_saldo"]) == sum(
        (Decimal(a["saldo"]) for a in a_all if a["account_type"] != "credit_card"), Decimal(0)))
    cek(f"{tid} B3 saldo == ledger_balance", all(Decimal(a["saldo"]) == Decimal(str(a["ledger_balance"])).quantize(Decimal("0.01")) for a in a_all))
    cek(f"{tid} B3 skema daftar memuat medan baru", all({"saldo", "position_text", "aksi_semua"} <= set(x) for x in
                                                       BA.BankAccountListResponse.model_validate(ha).model_dump()["items"]))
    for a in a_all:
        dd = BA.BankAccountDetailResponse.model_validate(await BA.get_bank_account(req(tid), a["id"])).model_dump()["data"]
        h = (await BA.get_bank_account_history(req(tid), a["id"], limit=200))["data"]
        cek(f"{tid} B3 detail/riwayat {a['account_name']}", len(dd["aksi_tersedia"]) == 8 and dd["position_text"] == a["position_text"]
            and h["events"], dd["aksi_tersedia"])
    # ---- G pratinjau void semua baris: nol tulis
    s0 = await sidik(conn, tid)
    for x in m_ter:
        await BTX.preview_void_bank_transaction(req(tid, "POST"), x["id"], VoidTransactionRequest(reason="UJI salinan pratinjau"))
    for t in t_all:
        await BT.preview_void_bank_transfer(req(tid, "POST"), t["id"], VoidBankTransferRequest(reason="UJI salinan pratinjau"))
    cek(f"{tid} G pratinjau void nol tulis", s0 == await sidik(conn, tid))


async def efek_manual(conn, tid, tx_id, je_id):
    rv = await conn.fetchval("SELECT reversed_by_id FROM journal_entries WHERE id = $1 AND tenant_id = $2", je_id, tid)
    return j({
        "jurnal": [dict(r) for r in await conn.fetch(
            "SELECT jl.account_id, jl.debit, jl.credit, jl.memo FROM journal_lines jl JOIN journal_entries je ON je.id = jl.journal_id "
            "WHERE je.tenant_id = $1 AND je.id = $2 ORDER BY jl.line_number", tid, rv)] if rv else None,
        "header": dict(await conn.fetchrow("SELECT journal_number, journal_date, total_debit, source_type, status, description FROM journal_entries "
                                           "WHERE id = $1 AND tenant_id = $2", rv, tid)) if rv else None,
        "cermin": [dict(r) for r in await conn.fetch(
            "SELECT bank_account_id, transaction_type, amount, reference_type, reference_number, description, status, origin_type "
            "FROM bank_transactions WHERE tenant_id = $1 AND reference_id = $2 AND reference_type = 'manual_void' ORDER BY amount", tid, tx_id)],
        "asal": dict(await conn.fetchrow("SELECT status, void_reason FROM bank_transactions WHERE id = $1 AND tenant_id = $2", tx_id, tid)),
    })


async def efek_transfer(conn, tid, t_id):
    t = await conn.fetchrow("SELECT status, journal_id, voided_reason FROM bank_transfers WHERE id = $1 AND tenant_id = $2", t_id, tid)
    if not t:
        return {"dihapus": True}
    rv = await conn.fetchval("SELECT reversed_by_id FROM journal_entries WHERE id = $1 AND tenant_id = $2", t["journal_id"], tid) \
        if t["journal_id"] else None
    return j({
        "status": t["status"], "alasan": t["voided_reason"],
        "jurnal": [dict(r) for r in await conn.fetch(
            "SELECT jl.account_id, jl.debit, jl.credit, jl.memo FROM journal_lines jl JOIN journal_entries je ON je.id = jl.journal_id "
            "WHERE je.tenant_id = $1 AND je.id = $2 ORDER BY jl.line_number", tid, rv)] if rv else None,
        "cermin": [dict(r) for r in await conn.fetch(
            "SELECT bank_account_id, transaction_type, amount, running_balance, reference_type, reference_number, description "
            "FROM bank_transactions WHERE tenant_id = $1 AND reference_id = $2 AND reference_type = 'transfer_void' ORDER BY amount", tid, t_id)],
        "baris_asal": [dict(r) for r in await conn.fetch(
            "SELECT status, void_reason FROM bank_transactions WHERE tenant_id = $1 AND reference_id = $2 AND reference_type = 'transfer' "
            "ORDER BY amount", tid, t_id)],
    })


def ringkas(h):
    """Respons tulis tanpa id/waktu acak: sukses + pesan + status (kedua pohon)."""
    if not h["ok"]:
        return {"ok": False, "status": h["status"]}
    d = h["hasil"].get("data") or {}
    return {"ok": True, "message": h["hasil"].get("message"), "status": d.get("status")}


async def tulis(conn, tid, o):
    """C: void (dua pohon); BARU: pratinjau dulu."""
    man = [r for r in await conn.fetch(
        """SELECT bt.id, bt.journal_id, bt.bank_account_id, ba.coa_id FROM bank_transactions bt
           JOIN journal_entries je ON je.id = bt.journal_id AND je.tenant_id = bt.tenant_id
           JOIN bank_accounts ba ON ba.id = bt.bank_account_id AND ba.tenant_id = bt.tenant_id
           WHERE bt.tenant_id = $1 AND bt.origin_type = 'MANUAL' AND bt.reference_type IS NULL AND je.source_type = 'BANK_TRANSACTION'
             AND je.source_id = bt.id AND je.reversed_by_id IS NULL AND bt.status <> 'VOIDED' ORDER BY bt.created_at, bt.id LIMIT 3""", tid)]
    for i, m in enumerate(man):
        k = f"C_m{i}"
        if BARU:
            p = (await BTX.preview_void_bank_transaction(req(tid, "POST"), m["id"], VoidTransactionRequest(reason=f"UJI salinan {i}")))["data"]
        h = await coba(V2.void_transaction(req(tid, "POST"), m["id"], VoidTransactionRequest(reason=f"UJI salinan {i}")))
        o[k] = {"respons": ringkas(h), "efek": await efek_manual(conn, tid, m["id"], m["journal_id"])}
        o[k + "_ulang"] = ringkas(await coba(V2.void_transaction(req(tid, "POST"), m["id"], VoidTransactionRequest(reason="UJI ulang"))))
        if BARU and h["ok"]:
            jur = [(x["account_id"], Decimal(x["debit"]), Decimal(x["credit"])) for x in p["jurnal"]]
            nyata = [(x["account_id"], Decimal(x["debit"]), Decimal(x["credit"])) for x in o[k]["efek"]["jurnal"]]
            cek(f"{tid} {k} pratinjau == jurnal tulis", p["ok"] and jur == nyata, (jur, nyata))
            cek(f"{tid} {k} saldo sesudah == jurnal", Decimal(p["saldo"][0]["sesudah"]) == await saldo_coa(conn, tid, m["coa_id"]))
    trf = await conn.fetch("""SELECT t.id, fb.coa_id AS dari, tb.coa_id AS ke, t.status FROM bank_transfers t
                              JOIN bank_accounts fb ON fb.id = t.from_bank_id AND fb.tenant_id = t.tenant_id
                              JOIN bank_accounts tb ON tb.id = t.to_bank_id AND tb.tenant_id = t.tenant_id
                              WHERE t.tenant_id = $1 AND t.status IN ('posted', 'draft') ORDER BY t.status DESC, t.created_at, t.id""", tid)
    pilih = [t for t in trf if t["status"] == "posted"][:3] + [t for t in trf if t["status"] == "draft"][:1]
    for i, t in enumerate(pilih):
        k = f"C_t{i}_{t['status']}"
        if BARU:
            p = (await BT.preview_void_bank_transfer(req(tid, "POST"), t["id"], VoidBankTransferRequest(reason=f"UJI salinan {i}")))["data"]
        h = await coba(BT.void_bank_transfer(req(tid, "POST"), t["id"], VoidBankTransferRequest(reason=f"UJI salinan {i}")))
        o[k] = {"respons": ringkas(h), "efek": await efek_transfer(conn, tid, t["id"])}
        o[k + "_ulang"] = ringkas(await coba(BT.void_bank_transfer(req(tid, "POST"), t["id"], VoidBankTransferRequest(reason="UJI ulang"))))
        if BARU and h["ok"] and t["status"] == "posted":
            jur = [(x["account_id"], Decimal(x["debit"]), Decimal(x["credit"])) for x in p["jurnal"]]
            nyata = [(x["account_id"], Decimal(x["debit"]), Decimal(x["credit"])) for x in o[k]["efek"]["jurnal"]]
            cek(f"{tid} {k} pratinjau == jurnal tulis", p["ok"] and jur == nyata, (jur, nyata))
            cek(f"{tid} {k} saldo sesudah == jurnal", [Decimal(s["sesudah"]) for s in p["saldo"]]
                == [await saldo_coa(conn, tid, t["dari"]), await saldo_coa(conn, tid, t["ke"])])
            cek(f"{tid} {k} saldo cermin == pratinjau", sorted(Decimal(c["running_balance"]) for c in o[k]["efek"]["cermin"])
                == sorted(Decimal(s["sesudah"]) for s in p["saldo"]))
        if BARU and t["status"] == "draft":
            cek(f"{tid} {k} pratinjau draf == dihapus", p.get("catatan") and o[k]["efek"] == {"dihapus": True})


async def utama():
    pool = await BA.get_pool()
    out = {"baru": BARU}
    async with pool.acquire() as conn:
        db = await conn.fetchval("SELECT current_database()")
        if not db.startswith("scratch_kasbank_"):
            sys.exit(f"BUKAN salinan: {db}")
        for tid in TENANT:
            o = out[tid] = {}
            bas, trs = await baca(conn, tid, o)
            if BARU:
                await baru_saja(conn, tid, o, bas, trs)
            await tulis(conn, tid, o)
    out["CEK"] = CEK
    json.dump(out, open(os.environ["KELUAR"], "w"), sort_keys=True, indent=1)
    print("[info] " + ", ".join(f"{t}: {len(out[t]['A_ba'])} rekening, {len(out[t]['A_trf'])} transfer, mutasi manual "
                                f"{out[t].get('B1_n', '-')}" for t in TENANT), flush=True)

asyncio.run(utama())
