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
TABEL = set(
    "account_balances_daily account_roles accounting_outbox accounting_settings "
    "accounts_payable accounts_receivable aging_brackets aging_snapshots "
    "ap_payment_applications approval_delegates approval_requests approval_workflows "
    "ar_payment_applications bank_accounts bank_statement_lines_v2 bank_transactions "
    "batch_warehouse_stock bill_items bill_payments_v2 bills chat_events chat_messages "
    "chat_session_state chat_sessions chat_workflow_state consolidation_runs "
    "credit_note_items credit_notes customer_deposit_applications customer_deposits "
    "customer_price_lists customers document_attachments document_intake_log documents "
    "expense_items expenses intent_decision_log inventory_ledger invoice_fulfillments "
    "item_batches item_pricing item_serials items journal_entries journal_lines "
    "pending_actions price_list_items products purchase_order_items purchase_orders "
    "quote_items quotes reconciliation_adjustments reconciliation_matches "
    "reconciliation_sessions recurring_bill_items recurring_bills sales_invoice_items "
    "sales_invoices sales_order_items sales_orders stock_adjustment_items "
    "stock_adjustments stock_transfer_items stock_transfers team_invitations "
    "user_tenant_roles userguide_query_log vendor_credit_items vendor_credits vendors "
    "warehouse_stock warehouses work_orders".split()
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
