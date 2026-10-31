"""Sabotase LAMPIRAN-PEMBELIAN F2b pada SALINAN pohon (argumen: <akar backend/api_gateway> <B1..B6>). Tiap sabotase WAJIB merah.
B6 menyunting migrasi lewat symlink salinan -> berkas ASLI worktree; karena itu B6 menulis berkas BARU di salinan (symlink diputus dulu)."""
import os
import shutil
import sys

akar, s = sys.argv[1], sys.argv[2]
A = {
    "B1": ("app/routers/lampiran_dokumen.py", "FROM goods_receipts g WHERE g.tenant_id = $2 AND g.id IN", "FROM goods_receipts g WHERE g.id IN"),
    "B2": ("app/routers/lampiran_dokumen.py", "SELECT 'vendor_deposit', 'vendor_deposit', u.id", "SELECT 'vendor_deposit', 'customer_deposit', u.id"),
    "B3": ("app/routers/lampiran_dokumen.py", "WHERE l0x.status = 'active' AND gi0x.goods_receipt_id", "WHERE gi0x.goods_receipt_id"),
    "B4": ("app/middleware/permission_middleware.py", '    (r"^/api/goods-receipts/[^/]+/attachments$", ["POST"], "purchase_order", "U"),\n',
           '    (r"^/api/goods-receipts/[^/]+/attachments$", ["POST"], "vendor_deposit", "U"),\n'),
    "B5": ("app/routers/lampiran_dokumen.py", "bi1x.bill_id IN (SELECT id FROM fb0)", "bi1x.bill_id IN (SELECT id FROM fb)"),
    "B6": ("../migrations/V466__chk_da_entity_grn_ump_ROLLBACK.sql", "    RAISE EXCEPTION 'V466 rollback: masih ada lampiran goods_receipt/vendor_deposit';\n", ""),
}
f, lama, baru = A[s]
if s == "B6":   # salinan memakai symlink backend/migrations -> worktree: ganti dgn salinan nyata agar berkas asli tak tersentuh
    mig = os.path.normpath(os.path.join(akar, "..", "migrations"))
    if os.path.islink(mig):
        asli = os.path.realpath(mig)
        os.unlink(mig)
        shutil.copytree(asli, mig)
p = os.path.normpath(os.path.join(akar, f))
t = open(p).read()
if t.count(lama) != 1:
    sys.exit(f"pola {s} n={t.count(lama)}")
open(p, "w").write(t.replace(lama, baru))
print(f"sabotase {s} terpasang")
