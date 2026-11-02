"""Milkybook v2: catat biaya sederhana (03 §3, 02-ALUR §F; putusan pemilik 2 Nov 2026 "Belajar dari riwayat").

Inti tulis = expenses.buat_biaya_inti (jalur POST /expenses yang sama) diganti perekam: di sini dibuktikan urutan
(idem -> akun sah -> inti posted -> simpan respons) dalam SATU transaksi, kunci wajib, izin posting, dan pilihan Dari/Akun.
SQL pilihan diuji terhadap Postgres sungguhan di test_v2_biaya_pg.py; tulis nyata = gerbang kaos."""
from contextlib import asynccontextmanager
from datetime import date

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.milkybook.v2 import biaya as B
from app.milkybook.v2.router import buat_router_v2
from app.routers import expenses as RE
from app.services import idem_buat

USER = {"tenant_id": "kaos-biru-konveksi", "user_id": "33333333-3333-4333-8333-333333333333"}
KAS = "aaaaaaaa-0000-4000-8000-00000000000a"
AKUN = "bbbbbbbb-0000-4000-8000-00000000000b"
HARI = date(2026, 11, 2)


def _r(id_, nama, dipakai=0, jenis="bank", serupa=0, peran=None):
    return {"id": id_, "nama": nama, "dipakai": dipakai, "jenis": jenis, "serupa": serupa, "peran": peran}


# ---------------------------------------------------------------- pilihan Dari / Akun (murni; baris sudah terurut SQL)

def test_dari_riwayat_lalu_peran():
    assert B.pilih_dari([_r("k1", "Kas A", 30, "petty_cash"), _r("k2", "Kas B", 1)])["id"] == "k1"
    assert B.pilih_dari([_r("k1", "Kas A", 0, "petty_cash", serupa=2)])["id"] == "k1"
    assert B.pilih_dari([_r("b1", "Bank", 0, "bank", peran=2), _r("k1", "Kas", 0, "cash")])["id"] == "b1"


def test_dari_tanpa_riwayat_tanpa_peran_satu_kas_dipakai_lebih_dari_satu_kosong():
    satu = [_r("b1", "Bank", 0, "bank"), _r("k1", "Kas", 0, "petty_cash")]
    assert B.pilih_dari(satu) == {"id": "k1", "nama": "Kas"}
    assert B.pilih_dari(satu + [_r("k2", "Kas 2", 0, "cash")]) is None
    assert B.pilih_dari([]) is None


def test_akun_serupa_lalu_peran_lalu_kosong_bukan_paling_sering():
    assert B.pilih_akun([_r("a1", "Beban X", 34, serupa=3), _r("a2", "Beban Y", 21)])["id"] == "a1"
    assert B.pilih_akun([_r("a2", "Beban Y", 0, peran=True), _r("a1", "Beban X", 34)])["id"] == "a2"
    assert B.pilih_akun([_r("a1", "Beban X", 34), _r("a2", "Beban Y", 21)]) is None  # paling sering BUKAN tebakan
    assert B.pilih_akun([]) is None


def test_nol_nama_akun_tenant_di_kode():
    import inspect
    src = inspect.getsource(B)
    for nama in ("Kas Operasional", "Kas Kecil", "Lain-lain", "Transportasi", "Toko Manado", "BCA"):
        assert nama not in src, nama


def test_pola_kata_tiga_huruf_huruf_kecil():
    assert B._pola("Bensin  motor ke JNE") == ["%bensin%", "%motor%", "%jne%"]
    assert B._pola("ok") == []


def test_pesan_tercatat_format_rupiah():
    assert B.pesan_tercatat("bensin", 50000, "Kas Operasional") == "Biaya bensin Rp50.000 tercatat dari Kas Operasional."


def test_badan_ditolak():
    for salah in ({"untuk": "  ", "jumlah": 1}, {"untuk": "x", "jumlah": 0}, {"untuk": "x", "jumlah": B.MAKS_JUMLAH + 1},
                  {"untuk": "x" * 101, "jumlah": 1}):
        with pytest.raises(ValidationError):
            B.CatatBiaya(dari_id=KAS, akun_id=AKUN, **salah)
    assert B.CatatBiaya(untuk=" bensin\n motor ", jumlah=5, dari_id=KAS, akun_id=AKUN).untuk == "bensin motor"


class _ConnBaca:
    def __init__(self, dari, akun):
        self.dari, self.akun, self.args = dari, akun, []

    async def fetch(self, sql, *args):
        self.args.append(args)
        return self.dari if sql is B.SQL_DARI else self.akun


@pytest.mark.asyncio
async def test_pratinjau_siap_dan_kurang():
    c = _ConnBaca([_r(KAS, "Kas Operasional", 5, "petty_cash")], [_r(AKUN, "Beban Lain-lain", 2, serupa=1)])
    p = await B.pratinjau(c, "t", " bensin ", 50000, HARI)
    assert p["siap"] and p["kurang"] == [] and p["untuk"] == "bensin" and p["jumlah"] == 50000
    assert p["dari"]["nama"] == "Kas Operasional" and p["akun"]["nama"] == "Beban Lain-lain" and p["tanggal"] == "2026-11-02"
    assert c.args == [("t", B.HARI_RIWAYAT, ["%bensin%"], ["CASH_PETTY", "CASH_GENERAL"]),
                      ("t", B.HARI_RIWAYAT, ["%bensin%"], "EXPENSE_OTHER")]
    kosong = await B.pratinjau(_ConnBaca([], []), "t", "", None, HARI)
    assert kosong["kurang"] == ["untuk", "jumlah", "dari", "akun"] and not kosong["siap"]


# ---------------------------------------------------------------- catat: urutan satu transaksi

class _Tx:
    def __init__(self, log):
        self.log = log

    async def __aenter__(self):
        self.log.append("BEGIN")

    async def __aexit__(self, t, e, tb):
        self.log.append("ROLLBACK" if t else "COMMIT")
        return False


class _Conn:
    def __init__(self):
        self.log, self.akun = [], {"id": AKUN, "name": "Beban Lain-lain"}

    def transaction(self):
        return _Tx(self.log)

    async def fetchrow(self, sql, *args):
        self.log.append(("AKUN_SAH", str(args[0]), args[1]))
        return self.akun


@pytest.fixture
def rekam(monkeypatch):
    st = {"conn": _Conn(), "lama": None, "izin": True, "izin_dipanggil": [], "inti": []}

    async def mulai(c, ctx, kunci, prefix, body, tabel, nama, response=None):
        c.log.append(("IDEM", prefix, kunci, tabel))
        return "kp", "sd", st["lama"]

    async def simpan(c, ctx, kp, sd, source_type, resp, result_id):
        c.log.append(("SIMPAN", source_type, str(result_id)))
        return resp

    async def inti(c, ctx, req):
        st["inti"].append((ctx, req))
        c.log.append(("INTI", req.status, req.amount, str(req.paid_through_id), str(req.account_id)))
        return {"success": True, "data": {"id": "eeeeeeee-0000-4000-8000-00000000000e", "expense_number": "EXP-UJI-1",
                                          "expense_date": req.expense_date, "total_amount": "50000.00",
                                          "paid_through_name": "Kas Operasional", "account_name": req.account_name}}

    async def izin(user, jalur, metode):
        st["izin_dipanggil"].append((tuple(jalur), metode))
        return {j: st["izin"] for j in jalur}

    monkeypatch.setattr(idem_buat, "mulai", mulai)
    monkeypatch.setattr(idem_buat, "simpan", simpan)
    monkeypatch.setattr(RE, "buat_biaya_inti", inti)
    st["izin_fn"] = izin
    return st


def _badan(**kw):
    return B.CatatBiaya(**{"untuk": "bensin", "jumlah": 50000, "dari_id": KAS, "akun_id": AKUN, **kw})


async def _catat(st, kunci="kartu-1", **kw):
    return await B.catat(st["conn"], USER, _badan(**kw), kunci, HARI, izin_fn=st["izin_fn"])


@pytest.mark.asyncio
async def test_catat_urutan_satu_transaksi_posted(rekam):
    h = await _catat(rekam)
    assert rekam["conn"].log == [
        "BEGIN",
        ("IDEM", "MB2_EXPENSE_CREATE", "kartu-1", "expenses"),
        ("AKUN_SAH", AKUN, USER["tenant_id"]),
        ("INTI", "posted", 50000, KAS, AKUN),
        ("SIMPAN", "MB2_EXPENSE_CREATE", "eeeeeeee-0000-4000-8000-00000000000e"),
        "COMMIT"]
    ctx, req = rekam["inti"][0]
    assert ctx == {"tenant_id": USER["tenant_id"], "user_id": USER["user_id"]}
    assert req.expense_date == HARI and req.notes == "bensin" and req.account_name == "Beban Lain-lain" and not req.is_itemized
    assert h["message"] == "Biaya bensin Rp50.000 tercatat dari Kas Operasional."
    assert h["data"]["total_amount"] == 50000 and h["data"]["expense_number"] == "EXP-UJI-1"
    assert rekam["izin_dipanggil"] == [(("/api/expenses/_/post",), "POST")]


@pytest.mark.asyncio
async def test_tanggal_dari_kartu_menang(rekam):
    await _catat(rekam, tanggal=date(2026, 10, 31))
    assert rekam["inti"][0][1].expense_date == date(2026, 10, 31)


@pytest.mark.asyncio
async def test_replay_tanpa_inti(rekam):
    rekam["lama"] = {"success": True, "data": {"id": "lama"}}
    assert (await _catat(rekam))["data"]["id"] == "lama"
    assert rekam["inti"] == [] and rekam["conn"].log[-1] == "COMMIT"


@pytest.mark.asyncio
async def test_tanpa_kunci_422_tanpa_transaksi(rekam):
    with pytest.raises(HTTPException) as e:
        await _catat(rekam, kunci=None)
    assert e.value.status_code == 422 and e.value.detail["code"] == "IDEMPOTENCY_KEY_WAJIB"
    assert rekam["conn"].log == []


@pytest.mark.asyncio
@pytest.mark.parametrize("kosong", [["dari"], ["akun"], ["dari", "akun"]])
async def test_dari_atau_akun_kosong_422_berkode_tanpa_transaksi(rekam, kosong):
    with pytest.raises(HTTPException) as e:
        await _catat(rekam, **{f"{n}_id": None for n in kosong})
    assert e.value.status_code == 422 and e.value.detail["code"] == "PILIH_DARI_AKUN_DULU" and e.value.detail["kurang"] == kosong
    assert rekam["conn"].log == [] and rekam["inti"] == [] and rekam["izin_dipanggil"] == []


@pytest.mark.asyncio
async def test_tanpa_izin_posting_403_tanpa_transaksi(rekam):
    rekam["izin"] = False
    with pytest.raises(HTTPException) as e:
        await _catat(rekam)
    assert e.value.status_code == 403 and e.value.detail["code"] == "IZIN_POSTING_BIAYA"
    assert rekam["conn"].log == []


@pytest.mark.asyncio
async def test_akun_bukan_beban_tenant_400_batal(rekam):
    rekam["conn"].akun = None
    with pytest.raises(HTTPException) as e:
        await _catat(rekam)
    assert e.value.status_code == 400 and e.value.detail["code"] == "AKUN_BEBAN_TIDAK_SAH"
    assert rekam["inti"] == [] and rekam["conn"].log[-1] == "ROLLBACK"


def test_sql_akun_sah_berpagar_tenant_dan_jenis_beban():
    assert "tenant_id = $2" in B.SQL_AKUN_SAH and "'EXPENSE', 'OTHER_EXPENSE'" in B.SQL_AKUN_SAH
    for sql in (B.SQL_DARI, B.SQL_AKUN):
        assert sql.count("tenant_id = $1") == 3  # rekening/akun + riwayat biaya + account_roles


def test_idem_buat_mengenal_tabel_expenses():
    assert idem_buat._TABEL["expenses"] == "expense_number"


# ---------------------------------------------------------------- rute: flag, kunci dari header

def _klien(monkeypatch, flag=True):
    tangkap = {}

    @asynccontextmanager
    async def conn_fn():
        yield object()

    async def tanggal(conn, tenant):
        return HARI

    async def pratinjau(conn, tenant, untuk, jumlah, hari):
        tangkap["pratinjau"] = (tenant, untuk, jumlah, hari)
        return {"siap": True}

    async def catat(conn, user, body, kunci, hari, *, izin_fn, response=None):
        tangkap["catat"] = (user["tenant_id"], body.untuk, kunci, hari)
        return {"success": True}

    import app.utils.tanggal_tenant as TT
    monkeypatch.setattr(TT, "tanggal_dokumen", tanggal)
    monkeypatch.setattr(B, "pratinjau", pratinjau)
    monkeypatch.setattr(B, "catat", catat)

    async def flag_fn(t):
        return flag

    app = FastAPI()

    @app.middleware("http")
    async def pasang_user(request: Request, call_next):
        request.state.user = USER
        return await call_next(request)

    async def izin(*a):
        return {}

    app.include_router(buat_router_v2(flag_fn=flag_fn, conn_fn=conn_fn, izin_fn=izin), prefix="/v2")
    return TestClient(app), tangkap


def test_rute_pratinjau_dan_catat(monkeypatch):
    c, t = _klien(monkeypatch)
    assert c.get("/v2/biaya/pratinjau", params={"untuk": "bensin", "jumlah": 50000}).json() == {"siap": True}
    assert t["pratinjau"] == (USER["tenant_id"], "bensin", 50000, HARI)
    r = c.post("/v2/biaya", json={"untuk": "bensin", "jumlah": 50000, "dari_id": KAS, "akun_id": AKUN},
               headers={"X-Idempotency-Key": "kartu-9"})
    assert r.status_code == 200 and t["catat"] == (USER["tenant_id"], "bensin", "kartu-9", HARI)
    assert c.get("/v2/biaya/pratinjau", params={"jumlah": 0}).status_code == 422


def test_rute_flag_mati_404(monkeypatch):
    c, t = _klien(monkeypatch, flag=False)
    assert c.get("/v2/biaya/pratinjau").status_code == 404
    assert c.post("/v2/biaya", json={"untuk": "x", "jumlah": 1, "dari_id": KAS, "akun_id": AKUN}).status_code == 404
    assert t == {}


def test_peran_beban_lain_dari_katalog_dan_disemai_v471():
    from app.services import role_resolver as RR
    assert B.PERAN_BEBAN_LAIN == RR.AccountRole.EXPENSE_OTHER == "EXPENSE_OTHER"
    assert "EXPENSE_OTHER" in RR._CATALOG and "EXPENSE_OTHER" in RR.PERAN_SEMAI
