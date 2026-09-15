"""(3) filename PDF telanjang = nomor dokumen (disanitasi) + filename* UTF-8, via helper bersama.
8 endpoint dokumen + laporan. Jangkar tepat; NOL bila tak cocok."""
import io, sys

ROOT = "/root/mh-law2/backend/api_gateway/app/routers/"
IMP = "        from ..utils.content_disposition import pdf_content_disposition, sanitize_filename\n"


def patch(fname, edits):
    p = ROOT + fname
    t = io.open(p, encoding="utf-8").read()
    for old, new, n in edits:
        c = t.count(old)
        if c != n:
            print(f"GAGAL {fname}: jangkar cacah {c}!={n}\n---\n{old[:80]}"); sys.exit(1)
        t = t.replace(old, new)
    io.open(p, "w", encoding="utf-8").write(t)
    print(f"OK {fname}")


# sales_invoices
patch("sales_invoices.py", [
    ('        invoice_num = invoice["invoice_number"] or str(invoice_id)[:8]\n        filename = f"Faktur-{invoice_num}.pdf"',
     '        invoice_num = invoice["invoice_number"] or str(invoice_id)[:8]\n'
     + IMP + '        filename = sanitize_filename(invoice_num) + ".pdf"', 1),
    ('''                    "Content-Disposition": f'inline; filename="{filename}"',''',
     '''                    "Content-Disposition": pdf_content_disposition(invoice_num),''', 1),
])

# bills (dua header + JSON filename; satu import)
patch("bills.py", [
    ('        invoice_num = bill.get("invoice_number") or str(bill_id)[:8]\n        filename = f"Faktur-{invoice_num}.pdf"',
     '        invoice_num = bill.get("invoice_number") or str(bill_id)[:8]\n'
     + IMP + '        filename = sanitize_filename(invoice_num) + ".pdf"', 1),
    ('''                    "Content-Disposition": f'inline; filename="{filename}"',
                    "Cache-Control": "no-store",  # FIX_LOGO_CACHEBUST 2026-06-16''',
     '''                    "Content-Disposition": pdf_content_disposition(invoice_num),
                    "Cache-Control": "no-store",  # FIX_LOGO_CACHEBUST 2026-06-16''', 2),
])

# quotes
patch("quotes.py", [
    ('        quote_num = quote["quote_number"] or str(quote_id)[:8]\n        filename = f"Penawaran-{quote_num}.pdf"',
     '        quote_num = quote["quote_number"] or str(quote_id)[:8]\n'
     + IMP + '        filename = sanitize_filename(quote_num) + ".pdf"', 1),
    ('''                    "Content-Disposition": f'inline; filename="{filename}"',''',
     '''                    "Content-Disposition": pdf_content_disposition(quote_num),''', 1),
])

# proformas (nomor inline -> ekstrak)
patch("proformas.py", [
    ('''        filename = f"Proforma-{row['proforma_number'] or str(proforma_id)[:8]}.pdf"''',
     "        proforma_num = row['proforma_number'] or str(proforma_id)[:8]\n"
     + IMP + '        filename = sanitize_filename(proforma_num) + ".pdf"', 1),
    ('''                "Content-Disposition": f'inline; filename="{filename}"',''',
     '''                "Content-Disposition": pdf_content_disposition(proforma_num),''', 1),
])

# deliveries
patch("deliveries.py", [
    ('    delivery_num = row["delivery_number"] or delivery_id[:8]\n    filename = f"SuratJalan-{delivery_num}.pdf"',
     '    delivery_num = row["delivery_number"] or delivery_id[:8]\n'
     '    from ..utils.content_disposition import pdf_content_disposition, sanitize_filename\n'
     '    filename = sanitize_filename(delivery_num) + ".pdf"', 1),
    ('''            "Content-Disposition": f'inline; filename="{filename}"',''',
     '''            "Content-Disposition": pdf_content_disposition(delivery_num),''', 1),
])

# receive_payments
patch("receive_payments.py", [
    ('        num = receipt_number or str(payment_id)[:8]\n        filename = f"Kwitansi-{num}.pdf"',
     '        num = receipt_number or str(payment_id)[:8]\n'
     + IMP + '        filename = sanitize_filename(num) + ".pdf"', 1),
    ('''                "Content-Disposition": f'inline; filename="{filename}"',''',
     '''                "Content-Disposition": pdf_content_disposition(num),''', 1),
])

# customer_deposits
patch("customer_deposits.py", [
    ('        num = dep["deposit_number"] or str(deposit_id)[:8]\n        filename = f"Kwitansi-{num}.pdf"',
     '        num = dep["deposit_number"] or str(deposit_id)[:8]\n'
     + IMP + '        filename = sanitize_filename(num) + ".pdf"', 1),
    ('''                "Content-Disposition": f'inline; filename="{filename}"',''',
     '''                "Content-Disposition": pdf_content_disposition(num),''', 1),
])

# psak_reports (laporan: deskriptif + display_name)
patch("psak_reports.py", [
    ('        company_name = _dn or tenant_id.replace("-", " ").title()',
     '        company_name = _dn or tenant_id.replace("-", " ").title()\n'
     '        from ..utils.content_disposition import pdf_content_disposition, sanitize_filename',
     1),
    ('            filename = f"Laporan-Laba-Rugi-{period_start}-{as_of}.pdf"',
     '            report_base = sanitize_filename(f"Laba-Rugi_{company_name}_{period_start}-{as_of}")', 1),
    ('            filename = f"Laporan-Posisi-Keuangan-{as_of}.pdf"',
     '            report_base = sanitize_filename(f"Posisi-Keuangan_{company_name}_{as_of}")', 1),
    ('            filename = f"Laporan-Arus-Kas-{period_start}-{as_of}.pdf"',
     '            report_base = sanitize_filename(f"Arus-Kas_{company_name}_{period_start}-{as_of}")', 1),
    ('''            headers={"Content-Disposition": f'inline; filename="{filename}"'},''',
     '''            headers={"Content-Disposition": pdf_content_disposition(report_base)},''', 1),
])

print("SEMUA OK")
