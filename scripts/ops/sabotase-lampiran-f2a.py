"""Sabotase LAMPIRAN-PEMBELIAN F2a pada SALINAN pohon (argumen: <akar backend/api_gateway> <A1..A5>). Tiap sabotase WAJIB merah."""
import sys

akar, s = sys.argv[1], sys.argv[2]
A = {
    "A1": ("app/routers/lampiran_dokumen.py", "FROM purchase_orders o WHERE o.tenant_id = $2 AND o.id IN", "FROM purchase_orders o WHERE o.id IN"),
    "A2": ("app/routers/lampiran_dokumen.py", "SELECT 'vendor_credit', 'vendor_credit', v.id", "SELECT 'vendor_credit', 'credit_note', v.id"),
    "A3": ("app/routers/lampiran_dokumen.py", "\n           OR bx.purchase_order_id IN (SELECT id FROM po0)", ""),
    "A4": ("app/middleware/permission_middleware.py",                         # izin rute lampiran ND salah modul (aturan ini PEMENANG)
           '    (r"^/api/vendor-credits/[^/]+/attachments$", ["POST"], "debit_note", "U"),\n',
           '    (r"^/api/vendor-credits/[^/]+/attachments$", ["POST"], "purchase_order", "U"),\n'),
    "A5": ("app/routers/lampiran_dokumen.py", "b1x.id IN (SELECT id FROM fb0)", "b1x.id IN (SELECT id FROM fb)"),
}
f, lama, baru = A[s]
p = f"{akar}/{f}"
t = open(p).read()
if t.count(lama) != 1:
    sys.exit(f"pola {s} n={t.count(lama)}")
open(p, "w").write(t.replace(lama, baru))
print(f"sabotase {s} terpasang")
