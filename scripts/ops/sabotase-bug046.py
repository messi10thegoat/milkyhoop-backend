"""Sabotase BUG-046 (WAJIB merah, terkompilasi). argv: <api_gateway dir> <nama>"""
import sys, pathlib, py_compile
R = pathlib.Path(sys.argv[1]) / "app/routers/sales_invoices.py"
t = R.read_text()
S = {
    "S_tanpa_bayar": ("pembayaran = await _bayar_tunai_faktur(conn, ctx, invoice_id, body)", "pembayaran = None"),
    "S_jumlah": ("total_amount=sisa,", "total_amount=sisa - 1,"),
    "S_rekening": ("WHERE id = $1 AND tenant_id = $2 AND is_active\",", "WHERE id = $1\","),
    "S_pelanggan": ("customer_id=str(pelanggan),", "customer_id=str(body.customer_id),"),
    "S_idem": ("if body.bayar_tunai and not _kunci_klien:", "if False:"),
    "S_kunci": ('f"INVOICE_PAYMENT:{invoice_id}"', 'f"INVOICE_PAYMENT:x"'),
}
old, new = S[sys.argv[2]]
assert t.count(old) == 1, sys.argv[2]
R.write_text(t.replace(old, new))
py_compile.compile(str(R), doraise=True)
print("sabotase", sys.argv[2], "terpasang + terkompilasi")
