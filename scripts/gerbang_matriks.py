"""GERBANG matriks (b): terapkan V251 dalam transaksi, bandingkan tiap (modul,peran) dgn ANALOG-nya; SELISIH terhadap
analog HARUS == himpunan 6 koreksi yang dideklarasi. Lalu ROLLBACK (nol sisa). Mode 'hidup' = tanpa apply (baca DB apa adanya).
argv: <baru|hidup>
"""
import asyncio
import os
import sys

sys.path.insert(0, "/app/backend/api_gateway")
import asyncpg  # noqa: E402

MODE = sys.argv[1]
BADAN = "/tmp/V251_badan.sql"
ANALOG = {
    "EXPENSE": "BILL", "DEBIT_NOTE": "BILL", "QUOTE": "INVOICE", "SALES_ORDER": "INVOICE", "CREDIT_NOTE": "INVOICE",
    "TABLES": "INVOICE", "CUSTOMER_DEPOSIT": "RECEIPT", "VENDOR_DEPOSIT": "PAYMENT", "STOCK_ADJUST": "PRODUCT",
    "WAREHOUSE": "PRODUCT", "UNIT": "PRODUCT", "BOM": "PRODUCT", "WORK_ORDER": "PRODUCT", "WORK_CENTER": "PRODUCT",
    "MATERIAL_ISSUE": "PRODUCT", "FG_RECEIPT": "PRODUCT", "FIXED_ASSET": "JOURNAL", "INTERCOMPANY": "JOURNAL",
    "LEDGER": "JOURNAL", "PERIOD": "JOURNAL", "BUDGET": "REPORT", "AR_AGING": "REPORT", "EMPLOYEE": "PAYROLL",
    "BPJS": "PAYROLL", "PAY_GROUP": "PAYROLL", "SALARY_COMPONENT": "PAYROLL",
}
# himpunan koreksi dideklarasi: (modul, peran) -> aksi baru; 'DEL' = baris dihapus (analog punya, hasil tak punya);
# 'ADD' = baris ditambah (analog TAK punya, hasil punya)
KOREKSI = {}
for r in ("CASHIER", "STORE_STAFF"):
    KOREKSI[("EXPENSE", r)] = ("ADD", frozenset("CR"))
for r in ("ACCOUNTANT", "BENDAHARA", "HR_PAYROLL"):
    KOREKSI[("PERIOD", r)] = ("SET", frozenset("R"))
for m in ("FIXED_ASSET", "LEDGER", "PERIOD", "INTERCOMPANY"):
    KOREKSI[(m, "VIEWER")] = ("ADD", frozenset("R"))
KOREKSI[("BUDGET", "ACCOUNTANT")] = ("SET", frozenset("CRU"))
KOREKSI[("BUDGET", "FINANCE_MGR")] = ("SET", frozenset("CRUA"))
for m in ("EMPLOYEE", "BPJS", "PAY_GROUP", "SALARY_COMPONENT"):
    KOREKSI[(m, "COLLABORATOR")] = ("DEL", None)
KOREKSI[("CREDIT_NOTE", "SALES")] = ("SET", frozenset("CRUE"))
KOREKSI[("CREDIT_NOTE", "STORE_STAFF")] = ("SET", frozenset("CRU"))


async def main():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    hasil = []

    def catat(u, ok, k=""):
        hasil.append((bool(ok), u, str(k)[:200]))

    tr = conn.transaction(); await tr.start()
    try:
        n_awal = await conn.fetchval("SELECT count(*) FROM role_permissions WHERE module = ANY($1::text[])", list(ANALOG))
        if MODE == "baru":
            catat("prasyarat: 26 modul row-less sebelum apply", n_awal == 0, n_awal)
            await conn.execute(open(BADAN, encoding="utf-8").read())

        # analog per (modul,peran)
        analog_rows = {}
        for r in await conn.fetch("SELECT r.code, rp.module, rp.actions FROM role_permissions rp JOIN roles r ON r.id=rp.role_id WHERE rp.module = ANY($1::text[])",
                                  list(set(ANALOG.values()))):
            analog_rows[(r["module"], r["code"])] = frozenset(r["actions"])
        actual = {}
        for r in await conn.fetch("SELECT r.code, rp.module, rp.actions FROM role_permissions rp JOIN roles r ON r.id=rp.role_id WHERE rp.module = ANY($1::text[])",
                                  list(ANALOG)):
            actual[(r["module"], r["code"])] = frozenset(r["actions"])

        # bandingkan: untuk tiap modul baru & tiap peran yang muncul di analog ATAU hasil
        peran_all = {p for (_, p) in analog_rows} | {p for (_, p) in actual}
        selisih_ditemukan = {}
        for modul, analog in ANALOG.items():
            for peran in peran_all:
                proj = analog_rows.get((analog, peran))   # apa yang analog berikan
                got = actual.get((modul, peran))
                if proj == got:
                    continue
                # ada selisih -> harus terdaftar di KOREKSI dan cocok
                selisih_ditemukan[(modul, peran)] = (proj, got)
        # setiap selisih HARUS == koreksi dideklarasi; setiap koreksi HARUS muncul
        tak_terdaftar = []
        for (modul, peran), (proj, got) in selisih_ditemukan.items():
            k = KOREKSI.get((modul, peran))
            if not k:
                tak_terdaftar.append((modul, peran, sorted(proj) if proj else None, sorted(got) if got else None))
                continue
            jenis, aksi = k
            ok = (jenis == "ADD" and proj is None and got == aksi) or \
                 (jenis == "DEL" and proj is not None and got is None) or \
                 (jenis == "SET" and got == aksi)
            if not ok:
                tak_terdaftar.append((modul, peran, "koreksi tak cocok", sorted(got) if got else None, jenis, sorted(aksi) if aksi else None))
        koreksi_hilang = [key for key in KOREKSI if key not in selisih_ditemukan]
        catat("SELISIH terhadap analog == himpunan 6 koreksi (tak ada yang tak terdaftar)", not tak_terdaftar, tak_terdaftar[:8])
        catat("setiap koreksi dideklarasi BENAR-BENAR terjadi", not koreksi_hilang, koreksi_hilang)
        catat("semua sel non-koreksi identik dengan analog", len(selisih_ditemukan) == len(KOREKSI), (len(selisih_ditemukan), len(KOREKSI)))

        # spot akuntansi: sel penting bernilai benar
        def has(modul, peran, aksi):
            return actual.get((modul, peran), frozenset()) >= frozenset(aksi) if aksi else (modul, peran) not in actual
        catat("CASHIER EXPENSE = C,R", actual.get(("EXPENSE", "CASHIER")) == frozenset("CR"), sorted(actual.get(("EXPENSE", "CASHIER"), [])))
        catat("PERIOD hanya ADMIN & FINANCE_MGR punya C/U/P/V",
              all(not (actual.get(("PERIOD", p), frozenset()) & frozenset("CUPV")) for p in peran_all if p not in ("ADMIN", "FINANCE_MGR", "OWNER")), "")
        catat("EMPLOYEE: COLLABORATOR TAK ada baris (kerahasiaan gaji)", ("EMPLOYEE", "COLLABORATOR") not in actual, "")
        catat("CREDIT_NOTE SALES tanpa P/V", not (actual.get(("CREDIT_NOTE", "SALES"), frozenset()) & frozenset("PV")), sorted(actual.get(("CREDIT_NOTE", "SALES"), [])))
        catat("VIEWER R di FIXED_ASSET", actual.get(("FIXED_ASSET", "VIEWER")) == frozenset("R"), sorted(actual.get(("FIXED_ASSET", "VIEWER"), [])))
        catat("BUDGET FINANCE_MGR = C,R,U,A", actual.get(("BUDGET", "FINANCE_MGR")) == frozenset("CRUA"), sorted(actual.get(("BUDGET", "FINANCE_MGR"), [])))
    finally:
        await tr.rollback()
        sisa = await conn.fetchval("SELECT count(*) FROM role_permissions WHERE module = ANY($1::text[])", list(ANALOG))
        await conn.close()
    catat("ROLLBACK: 26 modul kembali row-less" if MODE == "baru" else "hidup: baca saja", sisa == n_awal, sisa)

    for ok, u, k in hasil:
        print(("[H] " if ok else "[X] ") + f"{u}  | {k}")
    g = sum(1 for h in hasil if not h[0])
    print(f"\nMODE={MODE} gagal={g} total={len(hasil)}")
    sys.exit(0 if g == 0 else 1)


asyncio.run(main())
