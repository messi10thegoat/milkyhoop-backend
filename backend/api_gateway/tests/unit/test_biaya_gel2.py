"""Biaya BE gelombang 2 (B2): pratinjau BUAT (POST /expenses/preview) dan UBAH (POST /{id}/edit/preview) = jalur tulis YANG SAMA (buat_biaya_inti / ubah_biaya_inti) di transaksi
yang SELALU di-ROLLBACK; SATU kalkulator (_hitung_total) untuk /calculate, buat, ubah, kedua pratinjau; 200 selalu (blok berkalimat). Paritas pratinjau == tulis nyata = uji salinan."""
import asyncio
import datetime as dt
import inspect
import uuid
from decimal import Decimal as D
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import app.routers.expenses as EX
from app.schemas import expenses as S
from app.services.galat_berkode import GalatBerkode

T = "kaos-biru-konveksi"
EID, ACC, BANK = (uuid.uuid4() for _ in range(3))


def req():
    r = Request({"type": "http", "method": "POST", "path": "/", "headers": []})
    r.state.user = {"tenant_id": T, "user_id": "00000000-0000-0000-0000-000000000001"}
    return r


def jalan(aw):
    return asyncio.run(aw)


def baris(sub=100000, pajak=11000, pph=2000, tot=109000, status="draft", **kw):
    return {"id": EID, "subtotal": sub, "tax_amount": pajak, "pph_amount": pph, "total_amount": tot, "expense_date": dt.date(2026, 11, 12),
            "paid_through_name": "BCA", "account_name": "Listrik", "vendor_name": "PLN", "is_itemized": False, "status": status, **kw}


def badan(**kw):
    d = dict(expense_date=dt.date(2026, 11, 12), paid_through_id=BANK, account_id=ACC, amount=100000, tax_rate=11, pph_rate=2, status="posted")
    d.update(kw)
    return S.CreateExpenseRequest(**d)


# ---------------- SATU kalkulator
@pytest.mark.parametrize("sub,tr,pr", [(100000, 11, 2), (1050, 11, 0), (105, 10, 0), (999999, D("11"), D("2.5")), (1, 0, 0), (7, 12, 3)])
def test_hitung_total_sama_dengan_rumus_lama(sub, tr, pr):
    s = D(str(sub))
    tax = (s * D(str(tr)) / D("100")).quantize(D("1"))
    pph = (s * D(str(pr)) / D("100")).quantize(D("1"))
    assert EX._hitung_total(sub, tr, pr) == (tax, pph, s + tax - pph)         # rumus lama yang tersebar di tiga tempat, perilaku identik


def test_kalkulator_dipakai_bersama_dan_tak_ada_salinan():
    for fn in (EX.calculate_expense_totals, EX.buat_biaya_inti, EX.ubah_biaya_inti, EX.preview_create_expense):
        assert "_hitung_total(" in inspect.getsource(fn), fn.__name__
    for fn in (EX.calculate_expense_totals, EX.buat_biaya_inti, EX.ubah_biaya_inti):
        assert '.quantize(Decimal("1"))' not in inspect.getsource(fn)         # tak ada rumus pembulatan tersalin


# ---------------- pratinjau BUAT / UBAH (penyambungan)
class Tr:
    def __init__(self, kat):
        self.kat = kat

    async def start(self):
        self.kat.mulai = True

    async def rollback(self):
        self.kat.rollback = True

    async def commit(self):
        self.kat.commit = True


class K:
    def __init__(self, kat):
        self.kat = kat

    def transaction(self, readonly=False):
        return Tr(self.kat)

    async def execute(self, sql, *a):
        self.kat.exe.append(sql)

    async def fetchrow(self, sql, *a):
        assert "tenant_id = $1" in sql and a[0] == T          # tersaring tenant
        return self.kat.baris

    def acquire(self):
        k = self

        class Ac:
            async def __aenter__(s):
                return k

            async def __aexit__(s, *e):
                return False
        return Ac()


@pytest.fixture
def pasang(monkeypatch):
    kat = SimpleNamespace(mulai=False, rollback=False, commit=False, exe=[], baris=baris(), inti=None, efek=([{"source_type": "EXPENSE", "lines": []}], [{"amount": -109000.0}]))

    async def gp():
        return K(kat)

    async def pre(pool, tid=None):
        return None

    async def buat(conn, ctx, b):
        if kat.inti:
            raise kat.inti
        return {"success": True}

    async def ubah(conn, ctx, eid, b):
        if kat.inti:
            raise kat.inti
        return baris(), baris(sub=50000, pajak=5500, pph=1000, tot=54500)   # (sebelum, sesudah) seperti ubah_biaya_inti

    async def efek(conn, tid, eid, aksi):
        kat.efek_aksi = aksi
        return kat.efek
    monkeypatch.setattr(EX, "get_pool", gp)
    monkeypatch.setattr(EX, "_ensure_role_preconditions", pre)
    monkeypatch.setattr(EX, "buat_biaya_inti", buat)
    monkeypatch.setattr(EX, "ubah_biaya_inti", ubah)
    monkeypatch.setattr(EX, "_efek_jurnal", efek)
    return kat


def test_preview_buat_ok_rollback_selalu_dan_bentuk(pasang):
    d = jalan(EX.preview_create_expense(req(), badan()))["data"]
    assert (d["action"], d["can_proceed"], d["preview"], d["blocks"]) == ("buat", True, True, [])
    assert d["journals"] == pasang.efek[0] and d["bank_transactions"] == pasang.efek[1] and pasang.efek_aksi == "posting"
    assert d["calculation"] == {"subtotal": 100000, "tax_amount": 11000, "pph_amount": 2000, "total_amount": 109000}      # dari baris DB yang sama dgn tulis
    assert d["payload"]["amount"] == 100000 and d["payload"]["status"] == "posted"
    assert pasang.mulai and pasang.rollback and not pasang.commit                                                          # SELALU rollback


def test_preview_buat_ditolak_200_blok_berkode_dan_angka_dari_kalkulator_sama(pasang):
    pasang.inti = GalatBerkode(409, "KAS_MINUS", "Saldo kas tidak cukup.")
    d = jalan(EX.preview_create_expense(req(), badan()))["data"]
    assert d["can_proceed"] is False and d["blocks"] == [{"code": "KAS_MINUS", "message": "Saldo kas tidak cukup."}] and d["journals"] == []
    assert d["calculation"] == {"subtotal": 100000, "tax_amount": D("11000"), "pph_amount": D("2000"), "total_amount": D("109000")}  # kalkulator sama
    assert pasang.rollback and not pasang.commit
    pasang.inti = HTTPException(status_code=400, detail="Invalid paid_through account")
    d = jalan(EX.preview_create_expense(req(), badan()))["data"]
    assert d["blocks"] == [{"code": "HTTP_400", "message": "Invalid paid_through account"}] and d["can_proceed"] is False


def test_preview_buat_itemized_ditolak_angka_dari_baris(pasang):
    pasang.inti = HTTPException(status_code=400, detail="x")
    b = badan(is_itemized=True, amount=1, line_items=[S.ExpenseItemRequest(account_id=ACC, amount=30000), S.ExpenseItemRequest(account_id=ACC, amount=20000)])
    d = jalan(EX.preview_create_expense(req(), b))["data"]
    assert d["calculation"]["subtotal"] == 50000 and d["calculation"]["total_amount"] == D("50000") + D("5500") - D("1000")



def test_preview_ubah_sebelum_sesudah_tanpa_jurnal(pasang):
    d = jalan(EX.preview_update_expense(req(), EID, S.UpdateExpenseRequest(amount=50000)))["data"]
    assert (d["action"], d["can_proceed"], d["preview"]) == ("ubah", True, True)
    assert d["sebelum"]["total_amount"] == 109000 and d["sesudah"]["total_amount"] == 54500 and d["calculation"]["subtotal"] == 50000
    assert d["payload"] == {"amount": 50000}                                      # parsial: hanya yang dikirim
    assert pasang.rollback and not pasang.commit


def test_preview_ubah_ditolak_blok_berkode(pasang):
    pasang.inti = GalatBerkode(400, "BIAYA_BUKAN_DRAF", "Hanya biaya draf yang bisa diubah; batalkan biaya yang sudah diterbitkan.")
    d = jalan(EX.preview_update_expense(req(), EID, S.UpdateExpenseRequest(amount=1)))["data"]
    assert d["can_proceed"] is False and [b["code"] for b in d["blocks"]] == ["BIAYA_BUKAN_DRAF"] and d["calculation"] is None and d["sebelum"] is None


# ---------------- ubah_biaya_inti: penulis bersama (galat berkode)
class KonUbah:
    def __init__(self, existing):
        self.existing = existing

    async def execute(self, *a):
        return None

    async def fetchrow(self, sql, *a):
        return self.existing


@pytest.mark.parametrize("existing,kode,status", [(None, "EXPENSE_TIDAK_DITEMUKAN", 404), ({"status": "posted"}, "BIAYA_BUKAN_DRAF", 400), ({"status": "void"}, "BIAYA_BUKAN_DRAF", 400)])
def test_ubah_inti_berkode(existing, kode, status):
    with pytest.raises(GalatBerkode) as e:
        jalan(EX.ubah_biaya_inti(KonUbah(existing), {"tenant_id": T, "user_id": "u"}, EID, S.UpdateExpenseRequest(amount=1)))
    assert (e.value.status_code, e.value.code) == (status, kode)


def test_patch_dan_pratinjau_memakai_penulis_yang_sama():
    assert "ubah_biaya_inti(" in inspect.getsource(EX.update_expense) and "ubah_biaya_inti(" in inspect.getsource(EX.preview_update_expense)
    assert "buat_biaya_inti(" in inspect.getsource(EX.create_expense) and "buat_biaya_inti(" in inspect.getsource(EX.preview_create_expense)
    assert "INSERT INTO expenses" not in inspect.getsource(EX.update_expense) and "UPDATE expenses SET" not in inspect.getsource(EX.update_expense)  # tak ada salinan tulis


def test_rute_izin_dan_inventaris_pratinjau_baru():
    from pathlib import Path
    import app.middleware.permission_middleware as PM
    jalur = {getattr(x, "path", "") for x in EX.router.routes}
    assert {"/preview", "/{expense_id}/edit/preview"} <= jalur
    src = Path(PM.__file__).read_text()
    assert 'r"^/api/expenses/preview$", ["POST"], "expense", "C"' in src and 'r"^/api/expenses/[^/]+/edit/preview$", ["POST"], "expense", "U"' in src
    inv = Path(__file__).with_name("inventaris_rute_tulis.txt").read_text()
    assert "POST /api/expenses/preview\n" in inv and "POST /api/expenses/{expense_id}/edit/preview" in inv
