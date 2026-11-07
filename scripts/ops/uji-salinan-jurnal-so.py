"""Jurnal Umum standar SO -- uji di SALINAN (klon milkydb): handler ASLI list_journals / journals_summary / get_journal /
create_journal / reverse_journal. HANYA kaos + nyawit (grapgrap tidak dibaca). Tulis HANYA di salinan (dijaga: nama DB scratch_).
T1 paritas per kunci: summary {k}_count == daftar ?aksi=k total; {k}_value == daftar sum_debit; pill: id daftar ?aksi=k == id yang
   aksi_semua-nya memuat k (SQL == Python, semua halaman).
T2 P2: Σ debit BARIS (summary daftar total_amount) == Σ header total_debit (SQL langsung) -- header == baris.
T3 kontrol tanpa benih (nyawit): tidak_seimbang = 0, pill_tampil = ["draf"].
T4 benih DRAF baris seimbang 1.000/1.000 tapi header 1.200/1.200 -> tidak_seimbang +1, pill_tampil memuatnya, position_text
   "Tidak seimbang · selisih Rp 200", detail posting tak tersedia.
T5 idempotensi: kunci+isi sama -> id SAMA, jurnal +1; isi beda -> 409; tanpa kunci -> jurnal baru.
T6 dua "Balik" BERSAMAAN atas satu jurnal -> tepat satu sukses, satu 409 berkata-kata (bukan 500); manual tak menghitung pembalik."""
import asyncio
import os
import sys
import uuid
from decimal import Decimal

from fastapi import HTTPException, Response
from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import journals as RJ  # noqa: E402
from app.schemas.journals import CreateJournalRequest, ReverseJournalRequest  # noqa: E402
from app.services import jurnal_daftar as JD  # noqa: E402

HASIL = []
TENANT = tuple(os.environ.get("TENANT", "kaos-biru-konveksi,pt-kita-nyawit-bersama").split(","))
BENIH_TID = "pt-kita-nyawit-bersama"


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


def req(tid, uid, kunci=None, metode="GET"):
    h = [(b"x-idempotency-key", kunci.encode())] if kunci else []
    r = Request({"type": "http", "method": metode, "path": "/api/journals", "headers": h})
    r.state.user = {"tenant_id": tid, "user_id": str(uid)}
    return r


async def daftar(tid, uid, **kw):
    a = dict(period_id=None, start_date=None, end_date=None, status=None, source_type=None, account_id=None, search=None, sort_by=None,
             sort_order="desc", page=1, limit=100, aksi=None, with_totals=True)
    a.update(kw)
    semua, hal = [], 1
    while True:
        a["page"] = hal
        r = await RJ.list_journals(req(tid, uid), **a)
        semua += r.data
        if not r.pagination["has_more"]:
            return r, semua
        hal += 1


async def ringkas(tid, uid, search=None):
    return (await RJ.journals_summary(req(tid, uid), search=search))["data"]


async def periksa(tid, uid):
    ring = await ringkas(tid, uid)
    r0, semua = await daftar(tid, uid)
    for k in JD.SEMUA_KUNCI:
        r, baris = await daftar(tid, uid, aksi=k)
        cek(f"{tid} {k}: summary count == daftar total", ring[f"{k}_count"] == r.summary.total_count == len(baris), f"{ring[f'{k}_count']} vs {r.summary.total_count}")
        cek(f"{tid} {k}: summary value == daftar sum_debit", ring[f"{k}_value"] == r.sum_debit, f"{ring[f'{k}_value']} vs {r.sum_debit}")
        if k in JD.AKSI_JURNAL:
            py = {b.id for b in semua if k in b.aksi_semua}
            cek(f"{tid} {k}: id SQL == id aksi_semua Python", {b.id for b in baris} == py, f"sql={len(baris)} py={len(py)}")
    return ring, r0, semua


async def main():
    pool = await RJ.get_pool()
    async with pool.acquire() as c:
        assert (await c.fetchval("SELECT current_database()")).startswith("scratch_"), "harness hanya di salinan"
        uid = {t: await c.fetchval('SELECT id FROM "User" WHERE "tenantId" = $1 ORDER BY (role::text = \'OWNER\') DESC LIMIT 1', t) for t in TENANT}
    for tid in TENANT:
        ring, r0, semua = await periksa(tid, uid[tid])
        async with pool.acquire() as c:
            header = await c.fetchval("SELECT COALESCE(SUM(total_debit),0) FROM journal_entries WHERE tenant_id=$1", tid)
        cek(f"{tid} P2: Σ debit baris == Σ header", r0.summary.total_amount == header, f"{r0.summary.total_amount} vs {header}")
        cek(f"{tid} kontrol tanpa benih: tidak_seimbang 0, pill_tampil [draf]", ring["tidak_seimbang_count"] == 0 and ring["pill_tampil"] == ["draf"],
            f"{ring['tidak_seimbang_count']} {ring['pill_tampil']}")
        print(f"[angka] {tid}: total={ring['total']} " + " ".join(f"{k}={ring[f'{k}_count']}" for k in JD.SEMUA_KUNCI), flush=True)

    if BENIH_TID not in TENANT:
        return
    tid, u = BENIH_TID, uid[BENIH_TID]
    async with pool.acquire() as c:
        beban = await c.fetchval("""SELECT id FROM chart_of_accounts WHERE tenant_id=$1 AND account_type='EXPENSE' AND NOT is_header
                                    AND account_code NOT LIKE '5-1%' AND name NOT ILIKE '%HPP%' ORDER BY account_code LIMIT 1""", tid)
        modal = await c.fetchval("SELECT id FROM chart_of_accounts WHERE tenant_id=$1 AND account_type='EQUITY' AND NOT is_header ORDER BY account_code LIMIT 1", tid)
        hari = await c.fetchval("SELECT tanggal_bisnis($1)", tid)
        r_awal = await ringkas(tid, u)
        # T4 benih: DRAF baris 1.000/1.000, header 1.200/1.200 (CHECK je_balanced lolos; trigger baris-header hanya untuk POSTED)
        async with c.transaction():
            jid = await c.fetchval("""INSERT INTO journal_entries (tenant_id, journal_number, journal_date, description, source_type, total_debit,
                total_credit, status) VALUES ($1, 'UJI-TIMPANG-1', $2, 'UJI benih timpang', 'MANUAL', 1200, 1200, 'DRAFT') RETURNING id""", tid, hari)
            await c.execute("INSERT INTO journal_lines (journal_id, line_number, account_id, debit, credit) VALUES ($1,1,$2,1000,0),($1,2,$3,0,1000)", jid, beban, modal)
    r1 = await ringkas(tid, u)
    cek("benih: tidak_seimbang = 1 dan pill tampil", r1["tidak_seimbang_count"] == 1 and r1["pill_tampil"] == ["tidak_seimbang", "draf"],
        f"{r1['tidak_seimbang_count']} {r1['pill_tampil']}")
    cek("benih: draf +1", r1["draf_count"] == r_awal["draf_count"] + 1)
    _, timpang = await daftar(tid, u, aksi="tidak_seimbang")
    cek("benih: position_text selisih Rp 200", [b.position_text for b in timpang] == ["Tidak seimbang · selisih Rp 200"], str([b.position_text for b in timpang]))
    d = (await RJ.get_journal(req(tid, u), jid))["data"]
    cek("benih: detail posting tak tersedia + alasan", d.aksi_tersedia["posting"].tersedia is False and "tidak seimbang" in d.aksi_tersedia["posting"].alasan,
        str(d.aksi_tersedia["posting"]))
    await periksa(tid, u)   # paritas per kunci ULANG sesudah benih

    # T5 idempotensi
    badan = dict(entry_date=hari, description="UJI jurnal idem", lines=[{"account_id": str(beban), "debit": 1000}, {"account_id": str(modal), "credit": 1000}])

    async def jumlah():
        async with pool.acquire() as c2:
            return await c2.fetchval("SELECT count(*) FROM journal_entries WHERE tenant_id=$1", tid)
    kunci = f"uji-{uuid.uuid4().hex[:8]}"
    n0 = await jumlah()
    rs = Response()
    a = (await RJ.create_journal(req(tid, u, kunci, "POST"), CreateJournalRequest(**badan), Response()))["data"]
    b = (await RJ.create_journal(req(tid, u, kunci, "POST"), CreateJournalRequest(**badan), rs))["data"]
    n1 = await jumlah()
    cek("idem: kunci+isi sama -> id SAMA, jurnal +1, header replay", a.id == b.id and n1 == n0 + 1 and rs.headers.get("X-Idempotent-Replay") == "true", f"+{n1 - n0}")
    try:
        await RJ.create_journal(req(tid, u, kunci, "POST"), CreateJournalRequest(**{**badan, "description": "UJI beda"}), Response())
        cek("idem: isi beda -> 409", False, "lolos")
    except HTTPException as e:
        cek("idem: isi beda -> 409", e.status_code == 409 and e.detail.get("code") == "IDEMPOTENCY_KEY_REUSED", str(e.detail)[:80])
    await RJ.create_journal(req(tid, u, None, "POST"), CreateJournalRequest(**badan), Response())
    cek("idem opsional: tanpa kunci -> jurnal baru", await jumlah() == n1 + 1)

    # T6 dua Balik bersamaan atas jurnal a
    r_sebelum = await ringkas(tid, u)
    balik = ReverseJournalRequest(reversal_date=hari, reason="UJI balik bersamaan")

    async def coba():
        try:
            return ("ok", (await RJ.reverse_journal(req(tid, u, None, "POST"), uuid.UUID(a.id), balik))["data"].journal_number)
        except HTTPException as e:
            return ("galat", e.status_code, str(e.detail))
    hasil = await asyncio.gather(coba(), coba())
    ok = [h for h in hasil if h[0] == "ok"]
    gal = [h for h in hasil if h[0] == "galat"]
    cek("balik bersamaan: tepat 1 sukses + 1 409 berkata-kata", len(ok) == 1 and len(gal) == 1 and gal[0][1] == 409 and "sudah dibalik oleh" in gal[0][2],
        str(hasil))
    r_sesudah = await ringkas(tid, u)
    cek("manual tak menghitung pembalik: manual tetap, pembalik +1, dibalik +1",
        r_sesudah["manual_count"] == r_sebelum["manual_count"] and r_sesudah["pembalik_count"] == r_sebelum["pembalik_count"] + 1
        and r_sesudah["dibalik_count"] == r_sebelum["dibalik_count"] + 1,
        f"manual {r_sebelum['manual_count']}->{r_sesudah['manual_count']} pembalik {r_sebelum['pembalik_count']}->{r_sesudah['pembalik_count']}")
    await periksa(tid, u)


asyncio.run(main())
print(f"HASIL: {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
sys.exit(0 if all(HASIL) else 1)
