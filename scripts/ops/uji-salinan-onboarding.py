"""Onboarding lengkap -- uji di SALINAN (klon milkydb). create_tenant_and_user ASLI dijalankan end-to-end di salinan (Prisma/Redis/JWT ditiru
agar tak menyentuh prod); tulis HANYA di salinan.
T1 tenant baru: rekening "Kas" (cash, 1-10100, DEFAULT) + "Bank" (bank, 1-10200), saldo 0, nol jurnal.
T2 kode pajak: handler daftar+detail ASLI mengirim coa_id/coa_name (8 dari 9 tertaut, NONE null); paritas dengan DB untuk kaos+nyawit.
T3 satuan: GET /api/units tenant baru == dropdown (14 bawaan), bukan 0.
T4 tanpa backfill: rekening tenant lain TIDAK berubah; seed ulang idempoten (tetap 2).
T5 pemilih rekening bayar bawaan biaya memilih "Kas" (utama)."""
import asyncio
import inspect
import sys
import time
import types
from types import SimpleNamespace

from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")

# ---- peniru agar create_tenant_and_user tak menyentuh prod (Prisma/Redis); keduanya memang 'non-blocking' di kode asli
sys.path.insert(0, "/app")


class _Prisma:
    def __init__(self, *a, **k):
        raise RuntimeError("Prisma ditiadakan di uji salinan")


try:  # ganti HANYA kelas Prisma di modul asli (induk paket tak disentuh); bila modul tak bisa diimpor, kode asli sudah menelan galatnya
    import backend.api_gateway.libs.milkyhoop_prisma as _mp
    _mp.Prisma = _Prisma
except Exception as _e:  # noqa: BLE001
    print(f"[info] modul Prisma tak diimpor ({type(_e).__name__}); kode asli menelan galat registrasi perangkat", flush=True)
_sm = types.ModuleType("app.services.session_manager")
_sm.session_manager = SimpleNamespace(activate_mobile_device=lambda **k: None)
sys.modules["app.services.session_manager"] = _sm

from app.routers import tax_codes as TC, units as UN  # noqa: E402
from app.services import auth_instance, onboarding_service as OB, rekening_awal as RA, rekening_bawaan as RB  # noqa: E402
from app.services.db_pool import get_db_pool  # noqa: E402


async def _token(**k):
    return SimpleNamespace(access_token="uji", refresh_token="uji")

auth_instance.auth_client._generate_tokens_locally = _token

HASIL = []


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


def req(tid):
    r = Request({"type": "http", "method": "GET", "path": "/x", "headers": []})
    r.state.user = {"tenant_id": tid, "user_id": "00000000-0000-0000-0000-000000000001", "username": "uji-onboarding"}
    return r


async def panggil(fn, tid, **ganti):
    """Panggil rute langsung: default Query(...) diganti nilai bawaannya."""
    kw = {}
    for nama, p in inspect.signature(fn).parameters.items():
        if nama == "request":
            continue
        kw[nama] = p.default.default if hasattr(p.default, "default") else p.default
    kw.update(ganti)
    return await fn(req(tid), **kw)


async def utama():
    pool = await get_db_pool()
    async with pool.acquire() as c:
        db = await c.fetchval("SELECT current_database()")
        if not db.startswith("scratch_onb"):
            print(f"TOLAK: bukan salinan ({db})")
            return 2
        sebelum = {r["tenant_id"]: r["n"] for r in await c.fetch("SELECT tenant_id, count(*) AS n FROM bank_accounts WHERE tenant_id <> $1 GROUP BY tenant_id", "-")}
    stamp = int(time.time())
    hasil = await OB.create_tenant_and_user(f"uji-onb-{stamp}@example.invalid", "UjiOnboarding#1", f"UJI Onboarding {stamp}")
    tid, uid = hasil["tenant_id"], hasil["user_id"]
    print(f"[info] tenant baru di salinan: {tid}", flush=True)
    async with pool.acquire() as c:
        rek = await c.fetch("""SELECT ba.account_name, ba.account_type, ba.is_default, ba.is_active, ba.opening_balance, ba.created_by::text AS cb, coa.account_code
                               FROM bank_accounts ba JOIN chart_of_accounts coa ON coa.id = ba.coa_id AND coa.tenant_id = ba.tenant_id
                               WHERE ba.tenant_id = $1 ORDER BY ba.account_name""", tid)
        ring = [(r["account_name"], r["account_type"], r["account_code"], r["is_default"], float(r["opening_balance"])) for r in rek]
        cek("T1 tenant baru punya Kas (default, cash, 1-10100) + Bank (bank, 1-10200), saldo 0",
            ring == [("Bank", "bank", "1-10200", False, 0.0), ("Kas", "cash", "1-10100", True, 0.0)], str(ring))
        cek("T1 rekening dibuat atas nama pemilik tenant", all(r["cb"] == uid and r["is_active"] for r in rek))
        nj = await c.fetchval("SELECT count(*) FROM journal_entries WHERE tenant_id = $1", tid)
        cek("T1 nol jurnal untuk rekening awal (saldo turunan jurnal)", nj == 0, f"jurnal={nj}")

        daftar = (await panggil(TC.list_tax_codes, tid))
        items = daftar["items"]
        tertaut = [x for x in items if x["coa_id"]]
        cek("T2 daftar kode pajak tenant baru: 8 dari 9 membawa coa_id + coa_name", len(items) == 9 and len(tertaut) == 8
            and all(x["coa_name"] for x in tertaut) and [x["code"] for x in items if not x["coa_id"]] == ["NONE"],
            f"total={len(items)} tertaut={len(tertaut)}")
        ppn = next(x for x in items if x["code"] == "PPN-11-OUT")
        cek("T2 PPN-11-OUT -> PPN Keluaran", ppn["coa_name"] == "PPN Keluaran", ppn["coa_name"])
        det = (await TC.get_tax_code(req(tid), __import__("uuid").UUID(ppn["id"])))["data"]
        cek("T2 detail kode pajak membawa coa_id/coa_name yang sama", det["coa_id"] == ppn["coa_id"] and det["coa_name"] == "PPN Keluaran")
        for t in ("kaos-biru-konveksi", "pt-kita-nyawit-bersama"):
            api = {x["code"]: x["coa_id"] for x in (await panggil(TC.list_tax_codes, t))["items"]}
            db_ = {r["code"]: (str(r["coa_id"]) if r["coa_id"] else None) for r in await c.fetch("SELECT code, coa_id FROM tax_codes WHERE tenant_id = $1", t)}
            cek(f"T2 paritas API == DB untuk {t}", api == db_ and sum(1 for v in api.values() if v) == 8, f"tertaut={sum(1 for v in api.values() if v)}")

        u = await panggil(UN.list_units, tid)
        dd = await panggil(UN.units_dropdown, tid)
        cek("T3 GET /api/units tenant baru = 14 satuan bawaan (bukan 0) dan sama dengan dropdown",
            u["total"] == 14 and [x["abbreviation"] for x in u["items"]] == [x["abbreviation"] for x in dd["items"]], f"total={u['total']}")
        k = await panggil(UN.list_units, "kaos-biru-konveksi")
        cek("T3 tenant lama: bawaan + 20 satuan milik tenant ikut", k["total"] >= 20, f"total={k['total']}")

        sesudah = {r["tenant_id"]: r["n"] for r in await c.fetch("SELECT tenant_id, count(*) AS n FROM bank_accounts WHERE tenant_id <> $1 GROUP BY tenant_id", tid)}
        cek("T4 rekening tenant lain TIDAK berubah (tanpa backfill)", sesudah == sebelum, f"tenant={len(sebelum)}")
        async with c.transaction():
            lagi = await RA.seed_rekening_awal(c, tid, uid)
        n = await c.fetchval("SELECT count(*) FROM bank_accounts WHERE tenant_id = $1", tid)
        cek("T4 seed ulang idempoten (0 baru, tetap 2)", lagi == 0 and n == 2, f"baru={lagi} total={n}")
        pb = await RB.paid_through_bawaan(c, tid)
        cek("T5 rekening bayar bawaan biaya = Kas (utama)", pb and pb["nama"] == "Kas" and pb["sumber"] == "utama", str(pb))
    print(f"HASIL {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    return 0 if HASIL and all(HASIL) else 1


sys.exit(asyncio.run(utama()))
