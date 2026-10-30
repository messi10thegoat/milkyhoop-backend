"""Sabotase FPB-DEBIT-PENUH + RECLASS-COGS-CLASS pada SALINAN pohon (argumen: <akar backend/api_gateway> <P1|R1..R4>). Tiap sabotase WAJIB merah."""
import sys

akar, s = sys.argv[1], sys.argv[2]
A = {
    "P1": ("app/services/bills_service.py",                                   # jalur buat-langsung kembali ke satu debit Persediaan
           "for acct_id, amt, is_inv in await TA.hitung_debit(conn, tenant_id, bill_id, subtotal_dec, debit_acct_id):",
           "for acct_id, amt, is_inv in [(debit_acct_id, subtotal_dec, True)]:"),
    "R1": ("app/services/reklas_kelas_beban.py", "            if sudah:\n", "            if False:\n"),                 # idempotensi/penanda 409 dibuang
    "R2": ("app/services/reklas_kelas_beban.py", "        if tgt[sales] + tgt[servis] != pos[sales] + pos[servis]:\n",
           "        if False:\n"),                                                                                         # cek kelas/total dibuang
    "R3": ("app/services/reklas_kelas_beban.py",                                                                       # cek periode TULIS dibuang
           '        if periode and periode["status"] in ("CLOSED", "LOCKED"):\n'
           '            raise HTTPException(status_code=400, detail={"code": "PERIODE_TERTUTUP", "message": f"Periode {periode[\'status\']} untuk {tanggal}."})\n'
           '        nomor = await conn.fetchval("SELECT get_next_journal_number($1, \'JV\', $2)", tid, tanggal)\n'
           '        desk = ', '        nomor = await conn.fetchval("SELECT get_next_journal_number($1, \'JV\', $2)", tid, tanggal)\n        desk = '),
    "R6": ("app/services/reklas_kelas_beban.py",                                                                       # cek periode BALIK dibuang
           '        if periode and periode["status"] in ("CLOSED", "LOCKED"):\n'
           '            raise HTTPException(status_code=400, detail={"code": "PERIODE_TERTUTUP", "message": f"Periode {periode[\'status\']} untuk {tanggal}."})\n'
           '        baris = await conn.fetch(', '        baris = await conn.fetch('),
    "R4": ("app/services/reklas_kelas_beban.py",                                                                       # penanda tak dihitung di posisi
           "       OR (je.source_type = 'ADJUSTMENT' AND starts_with(je.description, 'RECLASS-COGS-CLASS')\n"
           "           AND strpos(jl.memo, '(' || $2::text || ')') > 0))", ")"),
    "R5": ("app/services/reklas_kelas_beban.py",                                                                       # balik tanpa cek penanda
           ' or not (j["description"] or "").startswith(PENANDA)', ""),
}
f, lama, baru = A[s]
p = f"{akar}/{f}"
t = open(p).read()
if t.count(lama) != 1:
    sys.exit(f"pola {s} n={t.count(lama)}")
open(p, "w").write(t.replace(lama, baru))
print(f"sabotase {s} terpasang")
