"""Sabotase FPB-DEBIT-SATU pada SALINAN pohon (argumen: <akar backend/api_gateway> <S1..S3>). Tiap sabotase WAJIB merah."""
import sys

akar, s = sys.argv[1], sys.argv[2]
A = {
    "S1": ("app/services/bills_service.py",                                                     # jalur buat-langsung kembali ke Persediaan
           "_accumulate(await TA.akun_beban_default(conn, tenant_id, None), net_dec, False)", "_accumulate(debit_acct_id, net_dec, True)"),
    "S2": ("app/services/tagihan_aksi.py",                                                      # penentu salah peran
           'role = AccountRole.COGS_SERVICE if item_type == "service" else AccountRole.COGS_SALES', "role = AccountRole.COGS_SERVICE"),
    "S3": ("app/services/tagihan_aksi.py",                                                      # hitung_debit berhenti memakai penentu (salinan)
           '        _acc(await akun_beban_default(conn, tid, dr["item_type"]), net, False)',
           '        _acc(await conn.fetchval("SELECT account_id FROM account_roles WHERE tenant_id = $1 AND role_key = $2", tid, "COGS_SALES"), net, False)'),
}
f, lama, baru = A[s]
p = f"{akar}/{f}"
t = open(p).read()
if t.count(lama) != 1:
    sys.exit(f"pola {s} n={t.count(lama)}")
open(p, "w").write(t.replace(lama, baru))
print(f"sabotase {s} terpasang")
