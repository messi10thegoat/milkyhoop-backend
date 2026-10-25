import sys
A, s = sys.argv[1] + "/app", sys.argv[2]
P = {
 "S_tenant": ("services/cetak_vendor_deposit.py", "LEFT JOIN bank_accounts ba ON ba.id = vd.bank_account_id AND ba.tenant_id = vd.tenant_id", "LEFT JOIN bank_accounts ba ON ba.id = vd.bank_account_id"),
 "S_cache": ("services/cetak_vendor_deposit.py", '"remaining": sisa,', '"remaining": vd["remaining_amount"],'),
 "S_abu": ("templates/pdf/goods_receipt.html", "body { font-family: 'Inter', 'DejaVu Sans', sans-serif; font-size: 12px; color: #1a1a1a; }", "body { font-family: 'Inter', 'DejaVu Sans', sans-serif; font-size: 12px; color: #666; }"),
 "S_izin": ("middleware/permission_middleware.py", 'goods_receipt/[^/]+/(html|pdf)$", ["GET"], "purchase_order"', 'goods_receipt/[^/]+/(html|pdf)$", ["GET"], "goods_receipt"'),
 "S_stempel": ("services/cetak_vendor_deposit.py", '"stamp_label": "DIBAYAR",', ''),
 "S_retur": ("services/cetak_goods_receipt.py", '"ada_retur": any(b["quantity_returned"] for b in baris),', '"ada_retur": True,'),
}
f, lama, baru = P[s]; p = f"{A}/{f}"; t = open(p).read()
assert t.count(lama) == 1, (s, "tak terpasang"); open(p, "w").write(t.replace(lama, baru)); print("terpasang", s)
