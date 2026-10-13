"""Field form pemasok yang dulu dibuang diam-diam kini tersimpan (13 Okt 2026, W-20261013-VENDORFIELD).

mobile->mobile_phone, nik (16 digit, hanya ORANG_PRIBADI), nitku/negara (V420), tax_address objek -> kolom tax_* terstruktur
(dulu 422 string_type), shipping_address -> vendor_addresses, contacts[] -> vendor_contacts (satu transaksi), default_* disimpan.
Aturan anak: kunci absen / null = baris TAK tersentuh; objek/daftar = ganti seluruhnya."""
import asyncio
from contextlib import asynccontextmanager
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.routers import vendors as V
from app.schemas.vendors import CreateVendorRequest as C, UpdateVendorRequest as U

NITKU = "1234567890123456000000"


# ---------------------------------------------------------------- skema
def test_mobile_alias_ke_mobile_phone():
    assert C(name="V", mobile="0812").mobile_phone == "0812"
    assert C(name="V", mobile_phone="0813").mobile_phone == "0813"
    assert U(mobile="0812").model_dump(exclude_unset=True) == {"mobile_phone": "0812"}


def test_tax_address_objek_dipecah_terstruktur():
    m = C(name="V", tax_address={"street": "Jl. A 1", "city": "Manado", "province": "Sulut", "postal_code": "95111"})
    assert (m.tax_address, m.tax_city, m.tax_province, m.tax_postal_code) == ("Jl. A 1", "Manado", "Sulut", "95111")
    u = U(tax_address={"street": "Jl. B", "city": "", "postcode": "95112"})
    assert u.model_dump(exclude_unset=True) == {"tax_address": "Jl. B", "tax_city": None, "tax_province": None, "tax_postal_code": "95112"}


def test_tax_address_string_tetap_diterima_dan_null_tak_mengubah():
    assert C(name="V", tax_address="Jl. C").tax_address == "Jl. C"
    assert "tax_address" not in U(tax_address=None, name="X").model_dump(exclude_unset=True)


def test_nik_16_digit_hanya_orang_pribadi():
    assert C(name="V", vendor_type="ORANG_PRIBADI", nik="3271 0112 3456 7890").nik == "3271011234567890"
    with pytest.raises(ValidationError):
        C(name="V", vendor_type="ORANG_PRIBADI", nik="123")
    with pytest.raises(ValidationError):
        C(name="V", vendor_type="BADAN", nik="3271011234567890")
    with pytest.raises(ValidationError):
        C(name="V", nik="3271011234567890")  # default BADAN
    with pytest.raises(ValidationError):
        U(vendor_type="BADAN", nik="3271011234567890")


def test_nitku_negara_pakai_identitas_pajak_dasar():
    m = C(name="V", nitku=NITKU, negara="idn")
    assert (m.nitku, m.negara) == (NITKU, "IDN")
    with pytest.raises(ValidationError):
        C(name="V", nitku="123")
    with pytest.raises(ValidationError):
        U(negara="ID")


def test_default_pajak_diterima():
    m = C(name="V", default_tax_code="PPN11", default_pph_type="PPH23", default_pph_rate=2)
    assert (m.default_tax_code, m.default_pph_type, m.default_pph_rate) == ("PPN11", "PPH23", Decimal("2"))
    with pytest.raises(ValidationError):
        C(name="V", default_pph_rate=101)


def test_kontak_nama_wajib_kecuali_baris_kosong():
    m = C(name="V", contacts=[{"name": "Budi", "email": "b@x.id"}, {"name": "", "email": ""}])
    assert [k.kosong() for k in m.contacts] == [False, True]
    with pytest.raises(ValidationError):
        C(name="V", contacts=[{"name": "", "phone": "0812"}])


def test_payload_fe_persis_lolos_create_dan_update():
    fe = {
        "name": "PT Uji", "company_name": "PT Uji", "display_name": "PT Uji", "email": "a@b.id", "phone": "0431",
        "mobile": "0812", "website": "x.id", "vendor_type": "ORANG_PRIBADI", "tax_id": "123", "nik": "3271011234567890",
        "is_pkp": False, "default_tax_code": "PPN11", "default_pph_type": "PPH23", "default_pph_rate": 2, "currency": "IDR",
        "payment_terms_days": 30, "address": "Jl", "city": "Manado", "province": "Sulut", "postal_code": "95",
        "notes": "n", "account_number": "A-1", "credit_limit": 1000, "bank_name": "BCA",
        "bank_account_number": "1", "bank_account_holder": "Budi", "nitku": NITKU, "negara": "IDN",
        "shipping_address": {"street": "S", "city": "K", "province": "P", "postal_code": "9"},
        "tax_address": {"street": "T", "city": "K", "province": "P", "postal_code": "9"},
        "contacts": [{"name": "Budi", "position": "", "email": "", "phone": ""}],
    }
    for kls in (C, U):
        m = kls(**fe)
        assert m.mobile_phone == "0812" and m.nik and m.nitku == NITKU and m.tax_city == "K"
        assert m.shipping_address.street == "S" and m.contacts[0].name == "Budi"


# ---------------------------------------------------------------- handler (fake pool)
class FakeConn:
    def __init__(self, existing=None):
        self.sql = []
        self.existing = existing

    @asynccontextmanager
    async def transaction(self):
        yield

    async def fetchrow(self, q, *a):
        self.sql.append((" ".join(q.split()), a))
        return self.existing

    async def fetchval(self, q, *a):
        self.sql.append((" ".join(q.split()), a))
        return None if "SELECT id FROM vendors" in q else uuid4()

    async def execute(self, q, *a):
        self.sql.append((" ".join(q.split()), a))

    def kena(self, fragmen):
        return [s for s, _ in self.sql if fragmen in s]


class FakePool:
    def __init__(self, conn):
        self.conn = conn

    @asynccontextmanager
    async def acquire(self):
        yield self.conn


def _req():
    return SimpleNamespace(state=SimpleNamespace(user={"tenant_id": "t1", "user_id": str(uuid4()), "username": "u"}))


def _pasang(monkeypatch, conn):
    async def gp():
        return FakePool(conn)

    async def ok(*a, **k):
        return None

    monkeypatch.setattr(V, "get_pool", gp)
    monkeypatch.setattr(V, "get_user_context", lambda r: {"tenant_id": "t1", "user_id": str(uuid4())})
    import app.services.optimistic_concurrency as oc
    monkeypatch.setattr(oc, "assert_if_match_row", ok)


EXISTING = {"id": uuid4(), "name": "Lama", "code": None, "vendor_type": "BADAN", "is_active": True}


class Baris(dict):
    pass


def _existing():
    return Baris(EXISTING)


def test_create_menyimpan_semua_kolom_dan_anak_satu_transaksi(monkeypatch):
    conn = FakeConn()
    _pasang(monkeypatch, conn)
    body = C(name="PT Uji", mobile="0812", nitku=NITKU, negara="IDN", account_number="A-1",
             tax_address={"street": "T", "city": "K"}, default_pph_type="PPH23", default_pph_rate=2,
             shipping_address={"street": "S", "city": "K2"}, contacts=[{"name": "Budi", "phone": "0812"}])
    asyncio.run(V.create_vendor(_req(), body))
    ins = conn.kena("INSERT INTO vendors")[0]
    for kolom in ("nik", "account_number", "tax_address", "tax_city", "tax_province", "tax_postal_code", "nitku", "negara",
                  "default_tax_code", "default_pph_type", "default_pph_rate"):
        assert kolom in ins
    args = [a for s, a in conn.sql if "INSERT INTO vendors" in s][0]
    assert "0812" in args and NITKU in args and "A-1" in args and "K" in args
    assert len(conn.kena("INSERT INTO vendor_addresses")) == 1
    assert len(conn.kena("INSERT INTO vendor_contacts")) == 1


def test_update_kunci_absen_tak_menyentuh_anak(monkeypatch):
    conn = FakeConn(existing=_existing())
    _pasang(monkeypatch, conn)
    asyncio.run(V.update_vendor(_req(), uuid4(), U(notes="catatan baru")))
    assert not conn.kena("vendor_contacts") and not conn.kena("vendor_addresses")
    assert conn.kena("UPDATE vendors")


def test_update_null_tak_menyentuh_anak(monkeypatch):
    conn = FakeConn(existing=_existing())
    _pasang(monkeypatch, conn)
    asyncio.run(V.update_vendor(_req(), uuid4(), U(notes="x", shipping_address=None, contacts=None, tax_address=None)))
    assert not conn.kena("vendor_contacts") and not conn.kena("vendor_addresses")


def test_update_contacts_dikirim_ganti_seluruhnya_dengan_filter_tenant(monkeypatch):
    conn = FakeConn(existing=_existing())
    _pasang(monkeypatch, conn)
    asyncio.run(V.update_vendor(_req(), uuid4(), U(contacts=[{"name": "A"}, {"name": ""}, {"name": "B", "mobile": "08"}])))
    hapus = conn.kena("DELETE FROM vendor_contacts")
    assert len(hapus) == 1 and "tenant_id = $2" in hapus[0]
    assert len(conn.kena("INSERT INTO vendor_contacts")) == 2  # baris kosong dilewati
    assert not conn.kena("vendor_addresses")


def test_update_shipping_objek_upsert_dan_kosong_menghapus(monkeypatch):
    conn = FakeConn(existing=_existing())
    _pasang(monkeypatch, conn)
    asyncio.run(V.update_vendor(_req(), uuid4(), U(shipping_address={"street": "S", "city": "K"})))
    assert len(conn.kena("DELETE FROM vendor_addresses")) == 1 and len(conn.kena("INSERT INTO vendor_addresses")) == 1
    conn2 = FakeConn(existing=_existing())
    _pasang(monkeypatch, conn2)
    asyncio.run(V.update_vendor(_req(), uuid4(), U(shipping_address={"street": "", "city": ""})))
    assert len(conn2.kena("DELETE FROM vendor_addresses")) == 1 and not conn2.kena("INSERT INTO vendor_addresses")
    assert not conn2.kena("vendor_contacts")


def test_update_nik_ke_pemasok_badan_422(monkeypatch):
    from fastapi import HTTPException
    conn = FakeConn(existing=_existing())  # vendor_type BADAN
    _pasang(monkeypatch, conn)
    with pytest.raises(HTTPException) as e:
        asyncio.run(V.update_vendor(_req(), uuid4(), U(nik="3271011234567890")))
    assert e.value.status_code == 422


# ---------------------------------------------------------------- GET: kontrak respons (response_model menyaring kunci!)
class FakeConnGet(FakeConn):
    VENDOR = {
        "id": uuid4(), "code": "V-1", "name": "PT Uji", "company_name": None, "display_name": "PT Uji", "contact_person": None,
        "phone": None, "email": None, "address": None, "city": None, "province": None, "postal_code": None, "tax_id": None,
        "payment_terms_days": 30, "credit_limit": None, "notes": None, "vendor_type": "ORANG_PRIBADI",
        "nik": "3271011234567890", "is_pkp": False, "nitku": NITKU, "negara": "IDN", "account_number": "A-1",
        "tax_address": "Jl. T", "tax_city": "Manado", "tax_province": None, "tax_postal_code": "95",
        "default_tax_code": "PPN11", "default_pph_type": "PPH23", "default_pph_rate": Decimal("2"),
        "mobile_phone": "0812", "website": None, "currency": "IDR", "opening_balance": 0, "opening_balance_date": None,
        "bank_name": None, "bank_account_number": None, "bank_account_holder": None, "is_active": True,
        "created_at": __import__("datetime").datetime(2026, 10, 13), "updated_at": __import__("datetime").datetime(2026, 10, 13),
    }

    async def fetchrow(self, q, *a):
        self.sql.append((" ".join(q.split()), a))
        if "FROM vendor_addresses" in q:
            return {"street": "Gudang", "city": "Bitung", "province": None, "postal_code": None}
        return self.VENDOR

    async def fetchval(self, q, *a):
        self.sql.append((" ".join(q.split()), a))
        return 0

    async def fetch(self, q, *a):
        self.sql.append((" ".join(q.split()), a))
        return [{"id": uuid4(), "name": "Budi", "position": None, "email": None, "phone": "0431", "mobile": None,
                 "is_primary": True}]


def test_get_detail_mengembalikan_semua_kunci_yang_dibaca_fe(monkeypatch):
    from app.schemas.vendors import VendorDetailResponse
    conn = FakeConnGet()
    _pasang(monkeypatch, conn)
    hasil = asyncio.run(V.get_vendor(_req(), uuid4()))
    d = VendorDetailResponse.model_validate(hasil).model_dump()["data"]  # lewat response_model seperti HTTP
    assert d["mobile"] == "0812" and d["mobile_phone"] == "0812"
    assert d["nik"] == "3271011234567890" and d["nitku"] == NITKU and d["negara"] == "IDN" and d["account_number"] == "A-1"
    assert d["tax_address"] == {"street": "Jl. T", "city": "Manado", "province": None, "postal_code": "95"}
    assert d["shipping_address"] == {"street": "Gudang", "city": "Bitung", "province": None, "postal_code": None}
    assert d["contacts"][0]["name"] == "Budi" and d["contacts"][0]["phone"] == "0431"
    assert d["default_tax_code"] == "PPN11" and d["default_pph_rate"] == 2.0
    # filter tenant EKSPLISIT di pembacaan anak (BYPASSRLS)
    assert all("tenant_id = $2" in s for s in conn.kena("FROM vendor_addresses") + conn.kena("FROM vendor_contacts"))
