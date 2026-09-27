"""Law 32 pool bersarang (28 Sep 2026): satu panggilan nyata per titik yang diperbaiki (di salinan) + burst paralel 12
(jurnal & ubah periode) dengan batas waktu — deadlock harus MERAH, bukan menggantung.
Titik: journals create/reverse (get_journal), periods update (tanpa & dengan perubahan)/close/reopen (get_period),
fiscal_years create/close (get_fiscal_year). stock_transfers (fitur diparkir _TAHAN) & permissions.py (router tak
terpasang) & document_intake (butuh dokumen unggahan) = bukti statis (penjaga AST)."""
import asyncio

from journey_lib import OWNER, T

BEBAN, MODAL = "18e8f8fb-4984-40ab-9f31-30e3fe82e407", "af9cdeb6-6187-4eb9-9ea4-d80bb012f647"


def _d(r):
    return (r or {}).get("data") if isinstance((r or {}).get("data"), (dict, list)) else r


async def jalankan(J):
    h = J.hari
    # jurnal: buat (POSTED) lalu balik
    _, r = await J.langkah("jurnal_buat", "POST", "/api/journals", {
        "entry_date": h.isoformat(), "description": "TES E2E pool jurnal",
        "lines": [{"account_id": BEBAN, "debit": "100"}, {"account_id": MODAL, "credit": "100"}]}, harap=(201,))
    jid = (_d(r) or {}).get("id")
    if (_d(r) or {}).get("journal_number") is None:
        J.gagal("jurnal_respons_lengkap", f"respons buat tanpa data jurnal: {str(r)[:160]}")
    await J.langkah("jurnal_balik", "POST", f"/api/journals/{jid}/reverse", {"reason": "TES E2E pool", "reversal_date": h.isoformat()})
    # tahun fiskal baru (tanpa transaksi) -> buat lalu tutup
    _, fy = await J.langkah("fy_buat", "POST", "/api/fiscal-years", {"name": "TES E2E FY 2091", "year": 2091}, harap=(201,))
    fyid = (_d(fy) or {}).get("id")
    if not (_d(fy) or {}).get("name"):
        J.gagal("fy_respons_lengkap", str(fy)[:160])
    await J.langkah("fy_tutup", "POST", f"/api/fiscal-years/{fyid}/close", {}, harap=(200, 400, 409, 422))
    # periode FY 2091: ubah (tanpa perubahan & dengan), tutup, buka kembali
    async with J.pool.acquire() as c:
        pid = await c.fetchval("""SELECT id FROM fiscal_periods WHERE tenant_id=$1 AND start_date >= '2091-01-01'
                                  AND status='OPEN' ORDER BY start_date LIMIT 1""", T)
    if not pid:
        return J.gagal("periode_2091", "tak ada periode OPEN 2091")
    _, p0 = await J.langkah("periode_ubah_kosong", "PUT", f"/api/periods/{pid}", {})
    _, p1 = await J.langkah("periode_ubah_nama", "PUT", f"/api/periods/{pid}", {"name": "TES E2E 2091-01"})
    if "TES E2E 2091-01" not in str(p1):
        J.gagal("periode_nama_terbaca_sesudah_commit", str(p1)[:200])
    await J.langkah("periode_tutup", "POST", f"/api/periods/{pid}/close", {"closing_notes": "TES E2E pool", "force": True})
    _, rb = await J.langkah("periode_buka", "POST", f"/api/periods/{pid}/reopen", {"reason": "TES E2E pool buka kembali"})
    if "OPEN" not in str(rb).upper():
        J.gagal("periode_buka_terbaca", str(rb)[:200])

    # burst 12 paralel: jurnal + ubah periode (dulu: pool 10 habis -> beku)
    async def jurnal(i):
        x = await J.cl.post("/api/journals", headers=J._hdr(OWNER), json={
            "entry_date": h.isoformat(), "description": f"TES E2E pool paralel {i}",
            "lines": [{"account_id": BEBAN, "debit": "10"}, {"account_id": MODAL, "credit": "10"}]})
        return ("jurnal", x.status_code)

    async def periode(i):
        x = await J.cl.put(f"/api/periods/{pid}", headers=J._hdr(OWNER), json={"name": f"TES E2E 2091-01 v{i}"})
        return ("periode", x.status_code)
    try:
        hasil = await asyncio.wait_for(asyncio.gather(*[jurnal(i) for i in range(12)], *[periode(i) for i in range(12)]),
                                       timeout=120)
    except asyncio.TimeoutError:
        return J.gagal("burst_24", "24 permintaan paralel TAK SELESAI dalam 120 dtk (deadlock pool?)")
    buruk = [x for x in hasil if x[1] >= 400]
    if buruk:
        J.gagal("burst_24", f"{len(buruk)} gagal: {buruk[:6]}")
    async with J.pool.acquire() as c:
        n = await c.fetchval("SELECT count(*) FROM journal_entries WHERE tenant_id=$1 AND description LIKE 'TES E2E pool paralel%' AND status='POSTED'", T)
        g = await c.fetchval("""SELECT count(*) FROM (SELECT chain_sequence FROM journal_entries WHERE tenant_id=$1 AND status='POSTED'
                                GROUP BY 1 HAVING count(*)>1) x""", T)
    if n != 12 or g != 0:
        J.gagal("burst_jurnal_posted", f"POSTED {n}/12, ganda {g}")
