"""Sabotase SALDO-AWAL-VENDOR-A pada SALINAN pohon. argv: <akar api_gateway salinan> <nama>. Tiap ganti TEPAT sekali.
merah_debit : hitung_debit tanpa cabang tagihan pembuka -> debit ke beban (harness (2) WAJIB merah)
U1 : blok_posting kembali ke peran Persediaan untuk tagihan pembuka -> tes unit merah
U2 : endpoint memposting di transaksi SENDIRI (tanpa conn_luar) -> tes unit merah
U3 : V458 tanpa indeks unik satu-tagihan-pembuka -> tes unit merah"""
import sys
from pathlib import Path

A = Path(sys.argv[1])
SAB = {
    "merah_debit": [("app/services/tagihan_aksi.py",
                     "    if await tagihan_pembuka(conn, tid, bill_id):  # SALDO-AWAL-VENDOR-A: default_inv_acct_id = Modal Saldo Awal (blok_posting)\n"
                     "        return [(default_inv_acct_id, subtotal_dec, SALDO_AWAL)] if subtotal_dec != NOL else []\n", "")],
    "U1": [("app/services/tagihan_aksi.py",
            "debit_role = AccountRole.EQUITY_OPENING_BALANCE if awal else (AccountRole.WIP_SUBCONTRACT if sc else AccountRole.INVENTORY_MERCHANDISE)",
            "debit_role = AccountRole.WIP_SUBCONTRACT if sc else AccountRole.INVENTORY_MERCHANDISE")],
    "U2": [("app/routers/vendors.py", "UUID(str(ctx[\"user_id\"])), conn_luar=conn)", "UUID(str(ctx[\"user_id\"])))")],
    "U3": [("../migrations/V458__bill_saldo_awal_vendor.sql",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_bill_saldo_awal_vendor ON bills (tenant_id, vendor_id) WHERE is_opening_balance AND status_v2 <> 'void';\n", "")],
}
for berkas, lama, baru in SAB[sys.argv[2]]:
    p = A / berkas
    s = p.read_text()
    assert s.count(lama) == 1, (sys.argv[2], lama[:60], s.count(lama))
    p.write_text(s.replace(lama, baru))
print(f"sabotase SALDO-AWAL-VENDOR-A {sys.argv[2]} terpasang")
