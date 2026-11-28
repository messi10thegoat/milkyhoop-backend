#!/usr/bin/env python3
"""Penegak: kueri pada tabel ber-`tenant_id` yang mencari lewat id TANPA menyaring tenant.

KENAPA BERKAS INI ADA
Sapuan 3 Sep 2026 menemukan bahwa hampir setiap tempat aman karena RANTAI --
baris di atasnya memeriksa kepemilikan, lalu kueri berikutnya berjalan tanpa
saringan. Rantai itu benar hari ini, tapi putus TANPA SUARA kalau seseorang
memindahkan pemeriksaannya, menambah cabang lebih awal, atau memakai ulang
fungsinya dari pemanggil baru. Tak ada constraint yang bisa menegakkannya, dan
sampai berkas ini ada, tak ada tes yang menjaganya.

Konteks yang membuatnya penting: RLS dekoratif dan seluruh koneksi gateway
BYPASSRLS. Penyaringan tenant SEPENUHNYA di kode -- tak ada jaring di bawahnya.

APA YANG DILAKUKANNYA, DAN APA YANG TIDAK
Ia memakai AST, bukan regex. Python menyambung literal bersebelahan saat parse,
jadi SQL yang dipecah beberapa baris tetap terbaca UTUH -- persis kelas
kesalahan yang membuat sebuah tes sebelumnya melaporkan pelanggaran palsu.

Ia TIDAK bisa melihat rantai. Sebagian besar temuan memang aman karena
pemeriksaan di baris atasnya, dan itu SENGAJA tidak dianggap lulus: yang
dijaga adalah PERTUMBUHAN. Garis dasar mencatat keadaan hari ini; penegak
gagal hanya kalau jumlahnya BERTAMBAH. Sama dengan pola cek_guc_lepas.py.

Kueri f-string dihitung TERPISAH sebagai "dinamis" dan TIDAK pernah dianggap
aman -- isinya tak bisa dinilai, dan menganggapnya aman akan membuat penegak
ini hijau justru pada bentuk yang paling sulit dibaca manusia.

Pakai:  python3 scripts/cek_pagar_tenant.py
        python3 scripts/cek_pagar_tenant.py --kontrol   # buktikan bisa MERAH
        python3 scripts/cek_pagar_tenant.py --daftar    # cetak semua temuan
"""

import ast
import re
import sys
from pathlib import Path

AKAR = Path(__file__).resolve().parents[1] / "backend" / "api_gateway" / "app"

# Diambil dari katalog 3 Sep 2026: tabel yang PUNYA kolom tenant_id.
# Diperluas 28 Nov 2026 (sensus WORKSPACE, rantai/SENSUS-PAGAR-TENANT-20261128.md Lampiran A): 262 tabel ber-tenant_id.
TABEL = set(
    "account_balances_daily account_roles accounting_outbox accounting_settings accounts_payable "
    "accounts_receivable aging_brackets aging_snapshots ap_payment_applications approval_delegates "
    "approval_requests approval_workflows ar_payment_applications ar_reconciliation_exemptions "
    "ar_reconciliation_pins asisten_aksi asset_categories aturan_bank audit_logs audit_retention_policies "
    "bank_accounts bank_reconciliation_sequences bank_reconciliations bank_statement_imports "
    "bank_statement_lines_v2 bank_transactions bank_transfer_sequences bank_transfers "
    "batch_warehouse_stock bill_inventory_reconciliation_exemptions bill_items bill_number_sequences "
    "bill_of_materials bill_payment_sequences bill_payments bill_payments_v2 bills bin_stock bpjs_config "
    "branch_permissions branch_transfer_sequences branch_transfers branches budgets bukti_potong "
    "chart_of_accounts chat_attachments chat_events chat_messages chat_session_state chat_sessions "
    "chat_workflow_state cheque_sequences cheques consolidation_groups consolidation_runs "
    "conversation_actions cost_centers cost_pools cost_variances credit_note_applications "
    "credit_note_deferral_lines credit_note_items credit_note_refunds credit_note_sequences credit_notes "
    "currencies customer_activities customer_addresses customer_categories customer_deposit_applications "
    "customer_deposit_refunds customer_deposit_sequences customer_deposits customer_price_lists customers "
    "dashboard_task_state deferred_revenue_reconciliation_exemptions document_attachments "
    "document_batches document_intake_log document_shares documents efaktur_sequences "
    "employee_advance_movements employee_advance_sequences employee_advances employee_salary_config "
    "exchange_rates expense_attachments expense_items expense_sequences expenses fiscal_periods "
    "fiscal_years fixed_asset_sequences fixed_assets forex_gain_loss goods_receipts "
    "health_check_exemptions idempotency_keys immutable_transactions intent_decision_log "
    "intercompany_balances intercompany_sequences intercompany_settlements intercompany_transactions "
    "inventory_cost_layers inventory_layer_moves inventory_ledger inventory_ledger_serials "
    "inventory_wac_reconciliation_exemptions invoice_fulfillments item_activities item_aliases "
    "item_batches item_categories item_code_sequences item_pricing item_serials items "
    "journal_chain_exemptions journal_entries journal_lines journal_number_sequences kds_alerts "
    "kds_item_history kds_orders kds_stations login_history mapping_templates menu_categories menu_items "
    "metode_serah mfa_audit_log milkybook_v2_percakapan milkybook_v2_tafsir_log mutasi_bank "
    "mutasi_bank_impor mutasi_bank_tautan opening_balance_records order_code_counters order_code_events "
    "order_code_settings overhead_allocations pay_groups payment_requests payroll_payments "
    "payroll_run_employees payroll_run_inputs payroll_run_piece_lines payroll_slip_lines pending_actions "
    "pengiriman_preferensi pengiriman_resi pengiriman_serah policy_audit_log price_list_items price_lists "
    "product_unit_aliases product_units production_orders production_sequences production_subcontracts "
    "products proforma_sequences proformas purchase_order_items purchase_order_sequences purchase_orders "
    "purchase_settings quote_items quote_sequences quotes ratio_alerts ratio_snapshots "
    "receive_payment_allocations receive_payment_sequences receive_payments recipes "
    "recon_category_patterns reconciliation_adjustments reconciliation_matches reconciliation_sessions "
    "recurring_bill_items recurring_bills recurring_invoices refresh_tokens report_balance_cache "
    "reservation_sequences restaurant_tables roles salary_components sales_invoice_attachments "
    "sales_invoice_item_batches sales_invoice_items sales_invoice_sequences sales_invoices "
    "sales_order_items sales_order_sequences sales_order_shipments sales_orders sales_receipt_sequences "
    "sales_receipts sensitive_data_access serial_movements shipment_sequences sj_faktur_alokasi "
    "standard_costs stock_adjustment_items stock_adjustment_sequences stock_adjustments "
    "stock_opname_lines stock_opname_sequences stock_opnames stock_transfer_items "
    "stock_transfer_sequences stock_transfers stok_minimum_gudang storage_locations table_areas "
    "table_reservations table_sessions table_waitlist tax_codes tax_invoice_sources tax_invoices "
    "team_invitations tenant_config tenant_features tenant_members tenant_roles tenant_rules "
    "trial_balance_snapshots unit_conversions uploaded_documents user_devices user_explicit_preferences "
    "user_favorites user_pay_group_access user_permission_overrides user_tenant_roles "
    "user_tutorial_progress user_warehouse_access userguide_query_log vendor_activities vendor_addresses "
    "vendor_contacts vendor_credit_applications vendor_credit_items vendor_credit_refunds "
    "vendor_credit_sequences vendor_credits vendor_deposit_sequences vendor_deposits vendors "
    "warehouse_bins warehouse_stock warehouses withholding_tax_records work_centers work_orders".split()
)

VERBA = re.compile(r"\b(SELECT|UPDATE|DELETE\s+FROM)\b", re.I)
PUNYA_WHERE = re.compile(r"\bWHERE\b", re.I)
PUNYA_TENANT = re.compile(r"\btenant_id\b", re.I)
# "mencari lewat id": ada perbandingan ke kolom ber-akhiran _id atau `id`
CARI_LEWAT_ID = re.compile(r"\b(\w*\.)?(\w*_id|id)\s*(=|IN|=\s*ANY)", re.I)
TABEL_DIPAKAI = re.compile(r"\b(?:FROM|UPDATE|JOIN|INTO)\s+([a-z_][a-z0-9_]*)", re.I)


def _sql_dari_konstanta(node):
    """String literal yang tampak seperti SQL. Literal bersebelahan sudah
    disambung oleh parser, jadi tak perlu menyambungnya sendiri."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        t = node.value
        if VERBA.search(t) and TABEL_DIPAKAI.search(t):
            return t, False
    if isinstance(node, ast.JoinedStr):  # f-string: isinya tak bisa dinilai
        potongan = "".join(
            v.value for v in node.values if isinstance(v, ast.Constant) and isinstance(v.value, str)
        )
        if VERBA.search(potongan) and TABEL_DIPAKAI.search(potongan):
            return potongan, True
    return None, False


def pindai(akar: Path):
    temuan, dinamis = [], []
    for berkas in sorted(akar.rglob("*.py")):
        if "/libs/" in str(berkas) or "milkyhoop_prisma" in str(berkas):
            continue
        try:
            pohon = ast.parse(berkas.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        # ⚠️ Literal di DALAM f-string juga node Constant, jadi ast.walk akan
        # menghitungnya DUA KALI -- sekali sebagai statis, sekali sebagai
        # dinamis. Garis dasar yang menggandakan tak layak dipercaya, jadi
        # anak-anak JoinedStr dikeluarkan lebih dulu.
        anak_fstring = {
            id(v)
            for n in ast.walk(pohon)
            if isinstance(n, ast.JoinedStr)
            for v in n.values
        }
        for node in ast.walk(pohon):
            if id(node) in anak_fstring:
                continue
            sql, adalah_fstring = _sql_dari_konstanta(node)
            if not sql:
                continue
            dipakai = {m.group(1).lower() for m in TABEL_DIPAKAI.finditer(sql)}
            kena = dipakai & TABEL
            if not kena:
                continue
            if not PUNYA_WHERE.search(sql) or not CARI_LEWAT_ID.search(sql):
                continue
            if PUNYA_TENANT.search(sql):
                continue
            rec = (str(berkas.relative_to(akar)), node.lineno, sorted(kena)[0],
                   " ".join(sql.split())[:90])
            (dinamis if adalah_fstring else temuan).append(rec)
    return temuan, dinamis


def _muat_pengecualian(p: Path):
    """Setiap baris: <path>:<baris> # <ALASAN>. Tanpa alasan = ditolak."""
    if not p.exists():
        return set(), []
    kunci, rusak = set(), []
    for i, baris in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        b = baris.strip()
        if not b or b.startswith("#"):
            continue
        if "#" not in b:
            rusak.append(f"baris {i}: pengecualian TANPA ALASAN -> ditolak: {b}")
            continue
        lokasi, alasan = b.split("#", 1)
        if len(alasan.strip()) < 10:
            rusak.append(f"baris {i}: alasan terlalu pendek untuk dipercaya: {b}")
            continue
        kunci.add(lokasi.strip())
    return kunci, rusak


def utama(argv) -> int:
    dasar_p = Path(__file__).with_name("pagar_tenant_baseline.txt")
    kecuali_p = Path(__file__).with_name("pagar_tenant_kecuali.txt")

    if "--kontrol" in argv:
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            umpan = Path(d) / "umpan.py"
            umpan.write_text(
                'q = "SELECT id FROM sales_invoices WHERE id = $1"\n', encoding="utf-8"
            )
            t, _ = pindai(Path(d))
        if t:
            print(f"KONTROL: kueri tanpa pagar DITEMUKAN ({t[0][2]}) -> pemindai bekerja")
            return 0
        print("KONTROL GAGAL: kueri tanpa pagar TIDAK ditemukan -> pemindai buta",
              file=sys.stderr)
        return 1

    temuan, dinamis = pindai(AKAR)
    kecuali, rusak = _muat_pengecualian(kecuali_p)
    if rusak:
        print("PENGECUALIAN DITOLAK:", file=sys.stderr)
        for r in rusak:
            print("  - " + r, file=sys.stderr)
        return 2

    tersisa = [t for t in temuan if f"{t[0]}:{t[1]}" not in kecuali]

    if "--daftar" in argv:
        for f, l, tab, sql in tersisa:
            print(f"{f}:{l}  [{tab}]  {sql}")
        print(f"\n-- dinamis (f-string, tak bisa dinilai): {len(dinamis)}")
        for f, l, tab, sql in dinamis[:20]:
            print(f"   {f}:{l}  [{tab}]  {sql}")
        return 0

    jumlah = len(tersisa)
    if not dasar_p.exists():
        dasar_p.write_text(f"{jumlah}\n{len(dinamis)}\n", encoding="utf-8")
        print(f"Garis dasar ditulis: {jumlah} statis, {len(dinamis)} dinamis.")
        return 0

    baris = dasar_p.read_text(encoding="utf-8").split()
    dasar, dasar_din = int(baris[0]), int(baris[1])
    print(f"tanpa pagar (statis) : {jumlah}   dasar {dasar}")
    print(f"dinamis (f-string)   : {len(dinamis)}   dasar {dasar_din}")

    if jumlah > dasar or len(dinamis) > dasar_din:
        print(
            "\nGAGAL: jumlah kueri tanpa saringan tenant BERTAMBAH.\n"
            "Tambahkan `AND tenant_id = $N`, atau daftarkan di\n"
            f"{kecuali_p.name} DENGAN ALASAN (mis. 'aman karena rantai di :303').\n"
            "Jalankan dengan --daftar untuk melihat semuanya.",
            file=sys.stderr,
        )
        return 1
    if jumlah < dasar or len(dinamis) < dasar_din:
        print("\nOK, dan jumlahnya TURUN. Perbarui garis dasar supaya tak bisa naik lagi.")
        return 0
    print("\nOK: tidak bertambah.")
    return 0


if __name__ == "__main__":
    sys.exit(utama(sys.argv[1:]))
