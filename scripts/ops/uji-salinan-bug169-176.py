"""BUG-169 + BUG-176 uji DB di SALINAN (bukan prod): handler/penentu ASLI atas klon milkydb, tenant nyawit + kaos. Mutasi HANYA di salinan.
A faktur terbit NYATA: neto per akun hasil `ringkas_per_akun` == SQL neto per akun atas jurnal INVOICE/INVOICE_REVENUE/DEPOSIT_APPLICATION faktur itu; akun nol-neto == `akun_nol_netto`;
   Σ debit == Σ kredit; 2-10750 (peran dimuka) nol-neto pada faktur pengakuan segera · B pratinjau terbit atas draf (bila ada): kunci ada, seimbang, rincian journals utuh, nol tulis
C BUG-176: buat biaya baru -> nomor jurnal == nomor dokumen (tanpa awalan ganda), keunikan, jurnal lama JV-EXP-EXP-* TAK berubah, pembalik biaya tak berubah."""
import asyncio
import sys
import uuid
from decimal import Decimal

from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import expenses as EX  # noqa: E402
from app.routers import sales_invoices as SI  # noqa: E402
from app.schemas import expenses as S  # noqa: E402
from app.services import faktur_terbit_rencana as FTR  # noqa: E402

NY, KA = "pt-kita-nyawit-bersama", "kaos-biru-konveksi"
HASIL = []


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


async def req(c, tid, path="/x", method="POST"):
    r = Request({"type": "http", "method": method, "path": path, "headers": []})
    uid = await c.fetchval('SELECT id FROM "User" WHERE "tenantId" = $1 ORDER BY (role::text = \'OWNER\') DESC LIMIT 1', tid)
    r.state.user = {"tenant_id": tid, "user_id": str(uid), "username": "uji-salinan"}
    return r


async def hitung(c, tid):
    return (await c.fetchval("SELECT count(*) FROM journal_entries WHERE tenant_id = $1", tid),
            await c.fetchval("SELECT count(*) FROM sales_invoices WHERE tenant_id = $1", tid),
            await c.fetchval("SELECT count(*) FROM expenses WHERE tenant_id = $1", tid))


async def main():
    pool = await SI.get_pool()
    async with pool.acquire() as c:
        for tid in (NY, KA):
            # ---------------- A: faktur terbit nyata
            faktur = await c.fetch("""SELECT si.id, si.invoice_number FROM sales_invoices si WHERE si.tenant_id = $1 AND si.status NOT IN ('draft', 'void')
                                      AND EXISTS (SELECT 1 FROM journal_entries je WHERE je.tenant_id = $1 AND je.source_id = si.id AND je.source_type = 'INVOICE_REVENUE')
                                      ORDER BY si.created_at DESC LIMIT 12""", tid)
            salah, nol_dimuka, ok_seimbang, total = [], 0, 0, 0
            for f in faktur:
                jurnal = []
                for je in await c.fetch("""SELECT id, source_type FROM journal_entries WHERE tenant_id = $1 AND source_id = $2 AND status = 'POSTED' AND is_effective_journal(id)
                                           AND source_type IN ('INVOICE', 'INVOICE_REVENUE', 'DEPOSIT_APPLICATION') ORDER BY created_at, id""", tid, f["id"]):
                    baris = await c.fetch("""SELECT co.account_code, co.name, jl.debit, jl.credit FROM journal_lines jl JOIN chart_of_accounts co ON co.id = jl.account_id
                                             JOIN journal_entries je ON je.id = jl.journal_id WHERE je.id = $1 AND je.tenant_id = $2 ORDER BY jl.line_number""", je["id"], tid)
                    jurnal.append({"source_type": je["source_type"], "lines": [{"account_code": b["account_code"], "account_name": b["name"], "debit": float(b["debit"] or 0),
                                                                                  "credit": float(b["credit"] or 0)} for b in baris]})
                ringkas, nol = FTR.ringkas_per_akun(jurnal)
                sql_neto = {}
                for j in jurnal:
                    for ln in j["lines"]:
                        sql_neto[ln["account_code"]] = sql_neto.get(ln["account_code"], Decimal(0)) + Decimal(str(ln["debit"])) - Decimal(str(ln["credit"]))
                dari_fungsi = {r["account_code"]: Decimal(str(r["debit"])) - Decimal(str(r["credit"])) for r in ringkas}
                harap = {k: v.quantize(Decimal("0.01")) for k, v in sql_neto.items() if v.quantize(Decimal("0.01")) != 0}
                total += 1
                if dari_fungsi != harap or {n["account_code"] for n in nol} != {k for k in sql_neto if k not in harap}:
                    salah.append((f["invoice_number"], dari_fungsi, harap))
                if round(sum(r["debit"] for r in ringkas) - sum(r["credit"] for r in ringkas), 2) == 0:
                    ok_seimbang += 1
                nol_dimuka += any(n["account_code"] == "2-10750" for n in nol)
            cek(f"A {tid} neto per akun (fungsi) == neto SQL jurnal terbit nyata, nol-neto == akun_nol_netto ({total} faktur)", total > 0 and not salah, str(salah[:2]))
            cek(f"A {tid} Σ debit == Σ kredit ringkasan di semua faktur", ok_seimbang == total, f"{ok_seimbang}/{total}")
            print(f"[ukur] {tid}: faktur berjurnal pengakuan {total}; 2-10750 nol-neto pada {nol_dimuka} faktur", flush=True)
            # ---------------- B: pratinjau atas draf (bila ada)
            d = await c.fetchrow("SELECT id FROM sales_invoices WHERE tenant_id = $1 AND status = 'draft' ORDER BY created_at DESC LIMIT 1", tid)
            if d:
                sebelum = await hitung(c, tid)
                data = (await SI.preview_post_invoice(await req(c, tid), d["id"], []))["data"]
                rk, nl = data["ringkas_per_akun"], data["akun_nol_netto"]
                sisi = round(sum(r["debit"] for r in rk) - sum(r["credit"] for r in rk), 2)
                cek(f"B {tid} pratinjau draf: kunci ada, seimbang, journals rincian utuh, nol tulis",
                    isinstance(rk, list) and isinstance(nl, list) and sisi == 0 and len(data["journals"]) >= 1 and await hitung(c, tid) == sebelum, str(data)[:240])
                kode_ringkas = {r["account_code"] for r in rk}
                kode_nol = {n["account_code"] for n in nl}
                kode_jurnal = {ln["account_code"] for j in data["journals"] + data["deposit_application_journals"] for ln in j["lines"]}
                cek(f"B {tid} ringkas ∪ nol-neto == semua akun di jurnal, tanpa irisan", kode_ringkas | kode_nol == kode_jurnal and not (kode_ringkas & kode_nol))
            else:
                print(f"[info] B {tid}: tak ada faktur draf di salinan, pratinjau draf dilewati", flush=True)

            # ---------------- C: BUG-176
            e = await c.fetchrow("""SELECT * FROM expenses WHERE tenant_id = $1 AND status = 'posted' AND journal_id IS NOT NULL AND NOT is_itemized
                                    ORDER BY created_at DESC LIMIT 1""", tid)
            cek(f"prasyarat C {tid}: ada biaya terbit sebagai templat", e is not None)
            if e is None:
                continue
            lama = await c.fetchval("SELECT count(*) FROM journal_entries WHERE tenant_id = $1 AND journal_number LIKE 'JV-EXP-EXP-%'", tid)
            hari = await c.fetchval("SELECT CURRENT_DATE")
            b = S.CreateExpenseRequest(expense_date=hari, paid_through_id=e["paid_through_id"], account_id=e["account_id"], amount=100000, status="posted")
            hasil = await EX.create_expense(await req(c, tid), b)
            nomor = hasil["data"]["expense_number"]
            jn = await c.fetchval("SELECT je.journal_number FROM journal_entries je JOIN expenses ex ON ex.journal_id = je.id AND ex.tenant_id = je.tenant_id WHERE ex.id = $1 AND ex.tenant_id = $2",
                                  str(hasil["data"]["id"]), tid)
            cek(f"C {tid} jurnal biaya baru: nomor jurnal == nomor dokumen ({nomor}), tanpa awalan ganda", jn == nomor and "EXP-EXP" not in jn, f"{jn!r} vs {nomor!r}")
            dup = await c.fetchval("SELECT count(*) FROM (SELECT journal_number FROM journal_entries WHERE tenant_id = $1 GROUP BY journal_number HAVING count(*) > 1) x", tid)
            cek(f"C {tid} keunikan nomor jurnal tenant (tanpa duplikat baru)", dup == 0, f"{dup} nomor ganda")
            cek(f"C {tid} jurnal lama JV-EXP-EXP-* tak berubah (cacah sama)", await c.fetchval("SELECT count(*) FROM journal_entries WHERE tenant_id = $1 AND journal_number LIKE 'JV-EXP-EXP-%'", tid) == lama,
                f"{lama} lama")
            await EX.void_expense(await req(c, tid), uuid.UUID(str(hasil["data"]["id"])), S.VoidExpenseRequest(reason="uji salinan bug176"))
            pembalik = await c.fetchval("SELECT journal_number FROM journal_entries WHERE tenant_id = $1 AND reversal_of_id = (SELECT id FROM journal_entries WHERE tenant_id = $1 AND journal_number = $2)",
                                        tid, jn)
            cek(f"C {tid} pembalik biaya tetap terbit (nomor tak bentrok)", pembalik is not None and pembalik != jn, f"{pembalik!r}")

    print(f"HASIL: {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    sys.exit(0 if all(HASIL) else 1)


asyncio.run(main())
