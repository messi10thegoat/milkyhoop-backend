"""GERBANG V253 Law 19 baris. Satu transaksi luar, ROLLBACK. Hasil di memori.
argv: baru (badan dipasang) | lama | hidup [ | sabotase_col ]
- schema: 3 trigger trg_law19_baris_beku ada.
- perilaku (baris dgn induk BERJURNAL): UPDATE kolom NOMINAL -> ditolak (baru) / LOLOS (lama); UPDATE kolom PROGRES
  (fulfilled_qty/recognized_amount/allocated_amount) -> LOLOS; UPDATE nominal pada induk DRAF -> LOLOS.
- daftar beku == daftar independen dua arah (tiap kolom nominal ditolak; tiap non-nominal lolos).
- sabotase_col: satu kolom (unit_price) dibuang dari daftar sales_invoice_items -> UPDATE unit_price posted LOLOS = MERAH.
"""
import asyncio
import os
import sys

sys.path.insert(0, "/app/backend/api_gateway")
import asyncpg  # noqa: E402

MODE = sys.argv[1]
BADAN = "/tmp/V253_badan.sql"
T = "kaos-biru-konveksi"
hasil = []
NOMINAL = {"sales_invoice_items": ["quantity", "unit_price", "discount_percent", "discount_amount", "tax_rate", "tax_amount", "subtotal", "total", "dpp"],
           "bill_items": ["quantity", "unit_price", "discount_percent", "discount_amount", "tax_rate", "tax_amount", "subtotal", "total", "dpp"]}
PROGRES = {"sales_invoice_items": ["fulfilled_qty", "recognized_amount", "allocated_amount"]}
INDUK = {"sales_invoice_items": ("sales_invoices", "invoice_id"), "bill_items": ("bills", "bill_id")}


def catat(s, u, ok, k=""):
    hasil.append((s, u, bool(ok), str(k)[:150]))


async def main():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    tr = conn.transaction(); await tr.start()
    try:
        if MODE.startswith("baru") or MODE == "sabotase_col":
            badan = open(BADAN, encoding="utf-8").read()
            if MODE == "sabotase_col":
                badan = badan.replace("'quantity', 'unit_price', 'discount_percent', 'discount_amount', 'tax_rate', 'tax_amount', 'subtotal', 'total', 'dpp');\n\nCREATE TRIGGER trg_law19_baris_beku BEFORE UPDATE ON bill_items",
                                      "'quantity', 'discount_percent', 'discount_amount', 'tax_rate', 'tax_amount', 'subtotal', 'total', 'dpp');\n\nCREATE TRIGGER trg_law19_baris_beku BEFORE UPDATE ON bill_items", 1)
            await conn.execute(badan)

        for t in ("sales_invoice_items", "bill_items", "expense_items"):
            n = await conn.fetchval("SELECT count(*) FROM pg_trigger WHERE tgname='trg_law19_baris_beku' AND tgrelid=$1::regclass", t)
            catat("SKEMA", f"{t}: trigger baris beku ada", n == 1, n)

        for t, cols in NOMINAL.items():
            induk, fk = INDUK[t]
            # baris dgn induk BERJURNAL
            row = await conn.fetchrow(f"SELECT it.id, it.{fk} FROM {t} it JOIN {induk} p ON p.id=it.{fk} WHERE p.tenant_id=$1 AND p.journal_id IS NOT NULL LIMIT 1", T)
            if not row:
                catat("PERILAKU", f"{t}: subjek baris induk-berjurnal", False, "tak ada"); continue
            for col in cols:
                sp = conn.transaction(); await sp.start()
                try:
                    await conn.execute(f"UPDATE {t} SET {col} = COALESCE({col},0) + 1 WHERE id=$1", row["id"])
                    got = "LOLOS"
                except asyncpg.PostgresError as e:
                    got = e.sqlstate
                await sp.rollback()
                if MODE == "lama":
                    catat("LAMA", f"{t}.{col}: UPDATE nominal posted LOLOS (celah)", got == "LOLOS", got)
                else:
                    catat("NOMINAL", f"{t}.{col}: UPDATE posted -> ditolak", got == "23514", got)
            # progres bebas
            for col in PROGRES.get(t, []):
                sp = conn.transaction(); await sp.start()
                try:
                    await conn.execute(f"UPDATE {t} SET {col} = COALESCE({col},0) + 1 WHERE id=$1", row["id"])
                    got = "LOLOS"
                except asyncpg.PostgresError as e:
                    got = e.sqlstate
                await sp.rollback()
                catat("PROGRES", f"{t}.{col}: UPDATE posted -> LOLOS (progres, bukan nominal)", got == "LOLOS", got)
            # non-nominal identitas bebas (line_number)
            sp = conn.transaction(); await sp.start()
            try:
                await conn.execute(f"UPDATE {t} SET line_number = COALESCE(line_number,0) + 100 WHERE id=$1", row["id"])
                got = "LOLOS"
            except asyncpg.PostgresError as e:
                got = e.sqlstate
            await sp.rollback()
            catat("NONNOMINAL", f"{t}.line_number: UPDATE posted -> LOLOS", got == "LOLOS", got)
            # induk DRAF -> nominal bebas
            draf = await conn.fetchrow(f"SELECT it.id FROM {t} it JOIN {induk} p ON p.id=it.{fk} WHERE p.tenant_id=$1 AND p.journal_id IS NULL LIMIT 1", T)
            if draf:
                sp = conn.transaction(); await sp.start()
                try:
                    await conn.execute(f"UPDATE {t} SET unit_price = COALESCE(unit_price,0)+1 WHERE id=$1", draf["id"])
                    got = "LOLOS"
                except asyncpg.PostgresError as e:
                    got = e.sqlstate
                await sp.rollback()
                catat("DRAF", f"{t}: UPDATE nominal pada induk DRAF -> LOLOS", got == "LOLOS", got)
    finally:
        await tr.rollback()
        sisa = await conn.fetchval("SELECT count(*) FROM pg_proc WHERE proname='law19_bekukan_baris'")
        await conn.close()
    if MODE == "baru":
        catat("ROLLBACK", "fungsi V253 tak menetap", sisa == 0, sisa)

    for s, u, ok, k in hasil:
        print(("[H] " if ok else "[X] ") + f"{s:10} {u}  | {k}")
    g = [h for h in hasil if not h[2]]
    print(f"\nMODE={MODE} gagal={len(g)} total={len(hasil)}")
    if MODE in ("baru", "hidup"):
        sys.exit(0 if not g else 1)
    if MODE == "sabotase_col":
        merah = [h[1] for h in g]
        ok = any("unit_price: UPDATE posted -> ditolak" in x for x in merah)  # unit_price dibuang -> tak ditolak = merah
        print("[sabotase_col] ->", "TERTANGKAP" if ok else "LOLOS", merah[:4]); sys.exit(0 if ok else 1)
    merah_lama = [h for h in hasil if h[0] == "LAMA" and not h[2]]
    print("[lama] ->", "CELAH BARIS TERBUKTI" if not merah_lama else "TAK SESUAI", [h[1] for h in merah_lama][:3])
    sys.exit(0 if not merah_lama else 1)


asyncio.run(main())
