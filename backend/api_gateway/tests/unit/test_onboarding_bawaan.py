"""Onboarding lengkap (BUG-153/157/162, putusan MASTER 12 Nov 2026; pola Accurate).
A1 (153): daftar + detail kode pajak MENGIRIM coa_id/coa_name (dulu tak dikirim -> FE selalu menampilkan "Belum" padahal DB tertaut).
A2 (157): tenant baru mendapat rekening awal "Kas" (cash, 1-10100, DEFAULT) dan "Bank" (bank, 1-10200), saldo 0 tanpa jurnal, dalam
         transaksi onboarding; tenant lama tidak disentuh (tanpa backfill).
A3 (162): GET /api/units = SATU sumber dengan dropdown (satuan bawaan + milik tenant), bukan hanya product_units."""
import asyncio
import inspect
import re
import uuid
from types import SimpleNamespace

import pytest

from app.routers import tax_codes as TC
from app.routers import units as UN
from app.schemas.tax_codes import TaxCodeDetail, TaxCodeListItem, TaxCodeListResponse
from app.services import onboarding_service as OB
from app.services import rekening_awal as RA

# ---------------------------------------------------------------- A1
def test_skema_daftar_dan_detail_membawa_coa():
    for m in (TaxCodeListItem, TaxCodeDetail):
        assert "coa_id" in m.model_fields and "coa_name" in m.model_fields, m.__name__
    item = dict(id="1", code="PPN-11-OUT", name="PPN 11%", rate=11.0, tax_type="ppn", is_inclusive=False, is_active=True,
                is_default=False, coa_id="c1", coa_name="PPN Keluaran")
    out = TaxCodeListResponse(items=[item], total=1, has_more=False).model_dump()["items"][0]
    assert out["coa_id"] == "c1" and out["coa_name"] == "PPN Keluaran"   # tak dibuang diam-diam oleh response_model


def test_sql_daftar_dan_detail_memilih_coa_bertenant():
    for fn in (TC.list_tax_codes, TC.get_tax_code):
        s = inspect.getsource(fn)
        assert "coa_id" in s and "AS coa_name" in s, fn.__name__
        assert "c.tenant_id = tax_codes.tenant_id" in s, fn.__name__   # nama akun hanya dari CoA tenant yang sama
        assert '"coa_id"' in s and '"coa_name"' in s, fn.__name__


def test_kode_tanpa_akun_dikirim_null_bukan_string_kosong():
    s = inspect.getsource(TC.list_tax_codes)
    assert 'str(row["coa_id"]) if row["coa_id"] else None' in s


# ---------------------------------------------------------------- A2
class _K:
    def __init__(self, coa=None):
        self.coa = coa if coa is not None else {"1-10100": uuid.uuid4(), "1-10200": uuid.uuid4()}
        self.sql, self.tulis = [], []

    async def fetchval(self, sql, *a):
        self.sql.append((" ".join(sql.split()), a))
        return self.coa.get(a[1])

    async def execute(self, sql, *a):
        q = " ".join(sql.split())
        self.tulis.append((q, a))
        return "INSERT 0 1"


UID = str(uuid.uuid4())


def test_rekening_awal_kas_default_dan_bank():
    k = _K()
    n = asyncio.run(RA.seed_rekening_awal(k, "t-baru", UID))
    assert n == 2 and len(k.tulis) == 2
    kas = next(a for q, a in k.tulis if "INSERT INTO bank_accounts" in q and a[1] == "Kas")
    bank = next(a for q, a in k.tulis if "INSERT INTO bank_accounts" in q and a[1] == "Bank")
    assert kas[0] == bank[0] == "t-baru"
    assert kas[2] == k.coa["1-10100"] and bank[2] == k.coa["1-10200"]
    assert (kas[3], bank[3]) == ("cash", "bank") and (kas[4], bank[4]) == (True, False)   # hanya Kas default (putusan MASTER)
    assert kas[5] == bank[5] == UID
    assert all(a[0] == "t-baru" for _, a in k.sql)            # CoA dicari bertenant


def test_rekening_awal_nol_jurnal_idempoten_dan_saldo_nol():
    k = _K()
    asyncio.run(RA.seed_rekening_awal(k, "t", UID))
    for q, _ in k.tulis:
        assert q.startswith("INSERT INTO bank_accounts") and "ON CONFLICT DO NOTHING" in q
        assert not re.search(r"journal", q, re.I)             # saldo awal 0 -> tanpa jurnal (Hukum Besi 1/16)
    assert "opening_balance, current_balance" in RA.SQL_SISIP and "0, 0, $6" in RA.SQL_SISIP   # saldo awal & cache 0 (turunan jurnal)


def test_pencarian_akun_coa_bertenant():
    assert "tenant_id = $1 AND account_code = $2" in RA.SQL_AKUN   # akun CoA hanya milik tenant ybs (set_config bukan pagar)


def test_rekening_awal_lewati_bila_akun_coa_tak_ada():
    k = _K(coa={"1-10200": uuid.uuid4()})
    assert asyncio.run(RA.seed_rekening_awal(k, "t", UID)) == 1
    assert [a[1] for q, a in k.tulis] == ["Bank"]


def test_onboarding_memanggil_penyemai_dalam_transaksi_sesudah_coa():
    s = inspect.getsource(OB.create_tenant_and_user)
    i, j, k, l = (s.index(x) for x in ("async with conn.transaction():", "seed_default_coa", "seed_rekening_awal(", "create_fiscal_year_with_periods"))
    assert i < j < k < l


# ---------------------------------------------------------------- A3
class _KU:
    def __init__(self, tenant_rows=()):
        self.rows = list(tenant_rows)

    async def fetch(self, sql, *a):
        return self.rows


class _Pool:
    def __init__(self, k):
        self.k = k

    def acquire(self):
        k = self.k

        class _C:
            async def __aenter__(self):
                return k

            async def __aexit__(self, *e):
                return False
        return _C()


def _req():
    return SimpleNamespace(state=SimpleNamespace(user={"tenant_id": "t-baru", "user_id": UID}))


def _panggil(monkeypatch, k, **kw):
    async def pool():
        return _Pool(k)
    monkeypatch.setattr(UN, "get_pool", pool)
    a = dict(search=None, is_active=None, limit=100)
    a.update(kw)
    return asyncio.run(UN.list_units(_req(), **a))


def test_daftar_satuan_tenant_baru_memuat_bawaan_bukan_kosong(monkeypatch):
    r = _panggil(monkeypatch, _KU())
    nama = [x["abbreviation"] for x in r["items"]]
    assert r["total"] == 14 and nama[:3] == ["Pcs", "Box", "Karton"]
    assert all(x["is_active"] is True for x in r["items"]) and r["items"][0]["is_system"] is True


def test_daftar_satuan_cari_dan_batas(monkeypatch):
    assert [x["abbreviation"] for x in _panggil(monkeypatch, _KU(), search="kg")["items"]] == ["Kg"]
    r = _panggil(monkeypatch, _KU(), limit=5)
    assert len(r["items"]) == 5 and r["total"] == 14           # total = jumlah penuh, bukan terpotong


def test_daftar_satuan_memuat_satuan_tenant_dan_satu_sumber_dengan_dropdown(monkeypatch):
    k = _KU([{"id": "u1", "name": "Kodi", "abbreviation": "Kodi", "is_system": False}])
    a = _panggil(monkeypatch, k)
    assert "Kodi" in [x["abbreviation"] for x in a["items"]] and a["total"] == 15
    s = inspect.getsource(UN.list_units)
    assert "SM.daftar(" in s and "FROM product_units" not in s


def test_daftar_satuan_nonaktif_tak_ikut_daftar_master(monkeypatch):
    assert _panggil(monkeypatch, _KU(), is_active=False) == {"items": [], "total": 0}
