"""BUG-055 uji DB di SALINAN (bukan prod): resolver services/rekening_bawaan + handler GET /api/expenses/summary ASLI atas klon milkydb,
tenant nyawit (biaya posted terakhir = Kas Kecil, tanpa is_default). Mutasi rekening HANYA di salinan.
T1 nyata: terakhir = rekening biaya posted terbaru · T2 rekening itu nonaktif -> dilewati -> kas aktif tertua · T3 + is_default -> utama ·
T4 rekening terakhir aktif lagi -> terakhir menang atas utama · T5 semua nonaktif -> null · T6 handler summary + response_model
menyampaikan paid_through_bawaan (+ kontrol merah: skema tanpa field membuangnya)."""
import asyncio
import sys

from pydantic import create_model
from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import expenses as EX  # noqa: E402
from app.schemas import expenses as S  # noqa: E402
from app.services.rekening_bawaan import paid_through_bawaan  # noqa: E402

TID = "pt-kita-nyawit-bersama"
HASIL = []


def cek(nama, ok, info=""):
    HASIL.append(ok)
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


async def main():
    pool = await EX.get_pool()
    async with pool.acquire() as c:
        akhir = await c.fetchrow("""SELECT e.paid_through_id, e.paid_through_name FROM expenses e WHERE e.tenant_id = $1 AND e.status = 'posted'
                                    ORDER BY e.created_at DESC, e.id DESC LIMIT 1""", TID)
        kas = await c.fetch("""SELECT id, account_name FROM bank_accounts WHERE tenant_id = $1 AND is_active AND account_type IN ('cash', 'petty_cash')
                               ORDER BY created_at, account_name""", TID)
        bank = await c.fetchrow("SELECT id, account_name FROM bank_accounts WHERE tenant_id = $1 AND is_active AND account_type = 'bank' ORDER BY account_name LIMIT 1", TID)
        cek("prasyarat: nyawit punya biaya posted + rekening kas + bank", bool(akhir and kas and bank))
        print(f"terakhir={akhir['paid_through_name']} kas={[k['account_name'] for k in kas]} bank={bank['account_name']}")

        h = await paid_through_bawaan(c, TID)
        cek("T1 terakhir = rekening biaya posted terbaru", h is not None and (h["id"], h["sumber"]) == (str(akhir["paid_through_id"]), "terakhir"), str(h))

        await c.execute("UPDATE bank_accounts SET is_active = false WHERE id = $1", akhir["paid_through_id"])
        h = await paid_through_bawaan(c, TID)
        sisa_kas = [k for k in kas if k["id"] != akhir["paid_through_id"]]
        cek("T2 rekening terakhir nonaktif -> dilewati -> kas aktif tertua",
            h is not None and h["sumber"] == "kas" and h["id"] == str(sisa_kas[0]["id"]), str(h))

        await c.execute("UPDATE bank_accounts SET is_default = (id = $2) WHERE tenant_id = $1", TID, bank["id"])
        h = await paid_through_bawaan(c, TID)
        cek("T3 is_default -> utama", h == {"id": str(bank["id"]), "nama": bank["account_name"], "sumber": "utama"}, str(h))

        await c.execute("UPDATE bank_accounts SET is_active = true WHERE id = $1", akhir["paid_through_id"])
        h = await paid_through_bawaan(c, TID)
        cek("T4 rekening terakhir aktif lagi -> terakhir menang atas utama", h and h["sumber"] == "terakhir" and h["id"] == str(akhir["paid_through_id"]), str(h))

        r = Request({"type": "http", "method": "GET", "path": "/api/expenses/summary", "headers": []})
        uid = await c.fetchval('SELECT id FROM "User" WHERE "tenantId" = $1 ORDER BY (role::text = \'OWNER\') DESC LIMIT 1', TID)
        r.state.user = {"tenant_id": TID, "user_id": str(uid), "username": "uji-salinan"}
        mentah = await EX.get_expenses_summary(r, period="month")
        d = S.ExpenseSummaryResponse.model_validate(mentah).model_dump(mode="json")["data"]
        cek("T6 handler summary + response_model menyampaikan paid_through_bawaan",
            (d.get("paid_through_bawaan") or {}).get("id") == str(akhir["paid_through_id"]) and d["paid_through_bawaan"]["sumber"] == "terakhir", str(d.get("paid_through_bawaan")))
        TanpaField = create_model("TanpaField", **{k: (v.annotation, v) for k, v in S.ExpenseSummaryData.model_fields.items() if k != "paid_through_bawaan"})
        cek("T6 kontrol merah: skema tanpa field membuangnya", "paid_through_bawaan" not in TanpaField.model_validate(mentah["data"]).model_dump())

        await c.execute("UPDATE bank_accounts SET is_active = false WHERE tenant_id = $1", TID)
        cek("T5 semua rekening nonaktif -> null", await paid_through_bawaan(c, TID) is None)

    print(f"HASIL: {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    sys.exit(0 if all(HASIL) else 1)


asyncio.run(main())
