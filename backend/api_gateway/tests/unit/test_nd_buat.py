"""ND-BUAT (29 Okt 2026, MASTER): buat Nota Debit setara NK -- (1) uang permintaan Decimal (Law 9/25; int lama diterima, JSON bulat = int);
(2) /vendor-credits/preview bentuk "dari faktur pembelian" (nol hitung FE) + PAGAR sementara tiket ND-PAJAK di penyusun TUNGGAL:
faktor DPP != 1 (dibaca dari tax_codes tenant, bukan angka 12) dan diskon baris DITOLAK di create, pratinjau, dari-FPB, DAN PATCH."""
import asyncio
import inspect
import json
import os
import uuid
from datetime import date
from decimal import Decimal

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from app.routers import vendor_credits as VC  # noqa: E402
from app.schemas import vendor_credits as VS  # noqa: E402
from app.services import nota_debit_rencana as NDR  # noqa: E402
from app.utils.idempotency import hash_payload  # noqa: E402

T = "kaos-biru-konveksi"
UID = uuid.UUID("99999999-8888-7777-6666-555555555555")
VID = uuid.UUID("11111111-0000-0000-0000-000000000001")
BID = uuid.UUID("22222222-0000-0000-0000-000000000002")
NDID = uuid.UUID("33333333-0000-0000-0000-000000000003")
L1, L2 = uuid.UUID(int=101), uuid.UUID(int=102)
D = Decimal
FAKTOR = {D("11"): (1, 1), D("12"): (11, 12)}  # tax_codes tenant uji: PPN-11 DPP penuh, PPN-12-IN DPP 11/12


def _n(sql):
    return " ".join(sql.split())


def _jalan(c):
    return asyncio.run(c)


# ---------------- (1) uang permintaan ----------------
@pytest.mark.parametrize("masuk,dalam,json_", [(1000, D("1000"), 1000), ("1000.00", D("1000.00"), 1000), (999.9, D("999.9"), 999.9),
                                                 ("1500.50", D("1500.50"), 1500.5)])
def test_uang_masuk_decimal_int_lama_diterima_json_bulat_int(masuk, dalam, json_):
    b = VS.CreateVendorCreditRequest(vendor_name="V", vendor_credit_date="2026-10-29", reason="return",
                                     items=[{"description": "x", "quantity": 1, "unit_price": masuk}], discount_amount=masuk)
    assert b.items[0].unit_price == dalam and isinstance(b.items[0].unit_price, Decimal)
    j = b.model_dump(mode="json")
    assert j["items"][0]["unit_price"] == json_ and type(j["items"][0]["unit_price"]) is type(json_)
    assert j["discount_amount"] == json_


def test_uang_masuk_tolak_lebih_dua_desimal_dan_negatif():
    for v in (1.005, "-1"):
        with pytest.raises(ValidationError):
            VS.VendorCreditItemCreate(description="x", quantity=1, unit_price=v)


def test_sidik_idem_sama_untuk_maksud_sama():
    """Law 14: 1000 / "1000" / "1000.00" = maksud SAMA -> sidik SAMA."""
    sidik = {hash_payload(VS.CreateVendorCreditRequest(vendor_name="V", vendor_credit_date="2026-10-29", reason="return",
                                                       items=[{"description": "x", "quantity": 1, "unit_price": v}]).model_dump(mode="json"))
             for v in (1000, "1000", "1000.00")}
    assert len(sidik) == 1


# ---------------- tiruan DB ----------------
class _Tx:
    def __init__(self, kon, ro=False): self.kon, self.ro = kon, ro
    async def start(self): self.kon.log.append(("tx", "BEGIN RO" if self.ro else "BEGIN", ()))
    async def rollback(self): self.kon.log.append(("tx", "ROLLBACK", ()))
    async def __aenter__(self):
        await self.start()
        return self
    async def __aexit__(self, et, *a):
        self.kon.log.append(("tx", "ROLLBACK" if et else "COMMIT", ()))
        return False


def _tagihan(**x):
    return {"id": BID, "invoice_number": "PB-2610-0042", "vendor_id": VID, "vendor_name": "Vendor A", "status_v2": "posted", "status": "unpaid",
            "tax_rate": 11, "tax_code_id": None, "tax_inclusive": False, "dpp_manual": None, "invoice_discount_total": D("1000.00"),
            "cash_discount_total": D("0"), "amount": D("9990.00"), **x}


def _baris(**x):
    a = {"id": L1, "product_id": uuid.UUID(int=7), "product_code": "KN-1", "product_name": "Kain", "description": "Kain katun",
         "quantity": D("3"), "unit": "m", "unit_price": D("2000"), "discount_percent": D("0"), "discount_amount": D("0"),
         "tax_rate": D("0"), "tax_code_id": None}
    b = {**a, "id": L2, "product_code": "BN-1", "product_name": "Benang", "description": "Benang", "quantity": D("2"), "unit_price": D("2000")}
    return [a, b] if not x else [{**a, **x}, {**b, **x}]


class Kon:
    def __init__(self, bill=None, items=None, vendor=True, vc=None, nd_lain=()):
        self.bill, self.items, self.vendor, self.vc, self.nd_lain = bill, items or [], vendor, vc, list(nd_lain)
        self.log = []

    def transaction(self, readonly=False, **kw): return _Tx(self, readonly)

    def _c(self, j, sql, a):
        q = _n(sql)
        self.log.append((j, q, a))
        return q

    async def execute(self, sql, *a):
        self._c("exec", sql, a)
        return "OK"

    async def fetchval(self, sql, *a):
        q = self._c("fetchval", sql, a)
        if "FROM vendors" in q:
            return 1 if self.vendor else None
        if "generate_vendor_credit_number" in q:
            return "VC-2610-0099"
        if q.startswith("INSERT INTO vendor_credits"):
            return NDID
        return None

    async def fetchrow(self, sql, *a):
        q = self._c("fetchrow", sql, a)
        if "FROM bills" in q:
            return self.bill if self.bill and a[0] == self.bill["id"] else None
        if "FROM vendor_credits" in q:
            return self.vc
        return None

    async def fetch(self, sql, *a):
        q = self._c("fetch", sql, a)
        if "FROM tax_codes" in q:
            f = FAKTOR.get(D(str(a[2])))
            return [{"dpp_factor_num": f[0], "dpp_factor_den": f[1]}] if f else []
        if "FROM bill_items" in q:
            return self.items
        if "FROM vendor_credits" in q:
            return [{"credit_number": n} for n in self.nd_lain]
        return []

    def tulis(self):
        return [q for j, q, a in self.log if q.startswith(("INSERT", "UPDATE", "DELETE"))]


class Pool:
    def __init__(self, kon): self.kon = kon

    def acquire(self):
        kon = self.kon

        class CM:
            async def __aenter__(s): return kon
            async def __aexit__(s, *a): return False
        return CM()


class _Req:
    def __init__(self):
        self.headers = {}
        self.state = type("S", (), {"user": {"tenant_id": T, "user_id": str(UID)}})()


def _pasang(mp, kon):
    async def gp(): return Pool(kon)
    mp.setattr(VC, "get_pool", gp)

    async def prasyarat(pool, tid=None): return None
    mp.setattr(VC, "_ensure_role_preconditions", prasyarat)

    async def nomor(conn, tid): return "VC-2610-0099"
    mp.setattr(NDR, "perkiraan_nomor_nd", nomor)

    async def tgl(conn, tid): return date(2026, 10, 29)
    mp.setattr(VC, "tanggal_dokumen", tgl)

    async def rencana(conn, ctx, vc, pratinjau=False, items=None):
        return {"blocks": NDR.Blok(True), "journals": [{"total": float(vc["total_amount"]), "tax": float(vc["tax_amount"])}],
                "stock": [], "bills": []}
    mp.setattr(NDR, "rencana_posting", rencana)


def _badan(**x):
    b = {"vendor_id": str(VID), "vendor_name": "Vendor A", "vendor_credit_date": "2026-10-29", "reason": "return",
         "items": [{"description": "Kain", "quantity": 3, "unit_price": 1000}]}
    b.update(x)
    return b


def _pv(kon, mp, **badan):
    _pasang(mp, kon)
    return _jalan(VC.preview_create_vendor_credit(_Req(), VC.VendorCreditPreviewRequest(**badan)))["data"]


# ---------------- (2) pagar: faktor DPP dari tax_codes, diskon baris ----------------
@pytest.mark.parametrize("letak", ["baris", "kepala"])
def test_pagar_dpp_12_ditolak_create_tanpa_tulis(monkeypatch, letak):
    kon = Kon()
    _pasang(monkeypatch, kon)
    b = _badan(items=[{"description": "Kain", "quantity": 3, "unit_price": 1000, "tax_rate": 12 if letak == "baris" else 0}],
               tax_rate=12 if letak == "kepala" else 0)
    with pytest.raises(HTTPException) as e:
        _jalan(VC.create_vendor_credit(_Req(), VS.CreateVendorCreditRequest(**b)))
    assert e.value.status_code == 400 and e.value.detail["code"] == "ND_PAJAK_DPP_BELUM_DIDUKUNG"
    assert e.value.detail["message"] == "PPN 12% (DPP 11/12) belum didukung di nota debit." and kon.tulis() == []


def test_pagar_dpp_membaca_tax_codes_bukan_angka_12(monkeypatch):
    """Tenant yang kode 12%-nya DPP PENUH -> 12% diizinkan; kode 11% ber-faktor -> 11% ditolak. Faktor = data tenant."""
    monkeypatch.setitem(FAKTOR, D("12"), (1, 1))
    monkeypatch.setitem(FAKTOR, D("11"), (10, 11))
    d = _pv(Kon(), monkeypatch, **_badan(items=[{"description": "K", "quantity": 1, "unit_price": 1000, "tax_rate": 12}]))
    assert d["can_proceed"] is True
    d = _pv(Kon(), monkeypatch, **_badan(items=[{"description": "K", "quantity": 1, "unit_price": 1000, "tax_rate": 11}]))
    assert [b["code"] for b in d["blocks"]] == ["ND_PAJAK_DPP_BELUM_DIDUKUNG"]
    assert "12" not in inspect.getsource(VC._pagar_nd).replace("2026", "")  # tak ada tarif dipatok


def test_pagar_dpp_11_dpp_penuh_tetap_boleh(monkeypatch):
    d = _pv(Kon(), monkeypatch, **_badan(items=[{"description": "K", "quantity": 3, "unit_price": 1000, "tax_rate": 11}]))
    assert d["can_proceed"] is True and d["tax_amount"] == 330.0


@pytest.mark.parametrize("diskon", [{"discount_percent": 10}, {"discount_amount": 100}])
def test_pagar_diskon_baris_ditolak_create_dan_pratinjau(monkeypatch, diskon):
    b = _badan(items=[{"description": "Kain", "quantity": 3, "unit_price": 1000, **diskon}])
    d = _pv(Kon(), monkeypatch, **b)
    assert [x["code"] for x in d["blocks"]] == ["ND_DISKON_BARIS_BELUM_DIDUKUNG"]
    kon = Kon()
    _pasang(monkeypatch, kon)
    with pytest.raises(HTTPException) as e:
        _jalan(VC.create_vendor_credit(_Req(), VS.CreateVendorCreditRequest(**b)))
    assert e.value.detail == {"code": "ND_DISKON_BARIS_BELUM_DIDUKUNG", "message": "Diskon per baris belum didukung di nota debit."}
    assert kon.tulis() == []


@pytest.mark.parametrize("ubah,kode", [({"items": [{"description": "K", "quantity": 1, "unit_price": 1000, "tax_rate": 12}]},
                                         "ND_PAJAK_DPP_BELUM_DIDUKUNG"),
                                        ({"tax_rate": 12}, "ND_PAJAK_DPP_BELUM_DIDUKUNG"),
                                        ({"items": [{"description": "K", "quantity": 1, "unit_price": 1000, "discount_percent": 5}]},
                                         "ND_DISKON_BARIS_BELUM_DIDUKUNG")])
def test_pagar_juga_di_patch_sebelum_tulis(monkeypatch, ubah, kode):
    kon = Kon(vc={"id": NDID, "status": "draft", "credit_number": "VC-1", "updated_at": None})
    _pasang(monkeypatch, kon)
    with pytest.raises(HTTPException) as e:
        _jalan(VC.update_vendor_credit(_Req(), NDID, VS.UpdateVendorCreditRequest(**ubah)))
    assert e.value.detail["code"] == kode and kon.tulis() == []


def test_satu_penentu_create_pratinjau_patch_memakai_pagar_yang_sama():
    assert "_pagar_nd(conn" in inspect.getsource(VC._susun_nd)
    assert "_susun_nd(conn, ctx" in inspect.getsource(VC.create_vendor_credit)
    assert "_susun_nd(conn, ctx, badan" in inspect.getsource(VC.preview_create_vendor_credit)
    assert "_pagar_nd(conn" in inspect.getsource(VC.update_vendor_credit)
    assert VC.resolve_dpp_factor.__module__.endswith("services.tax_factor")


# ---------------- (2) dari faktur pembelian ----------------
def test_dari_fpb_penuh_mode_kepala_angka_dari_tagihan_dan_total_sama_persis(monkeypatch):
    kon = Kon(bill=_tagihan(), items=_baris())
    d = _pv(kon, monkeypatch, original_bill_id=str(BID), reason="return")
    # baris 3x2000 + 2x2000 = 10.000; diskon dokumen tagihan 1.000 penuh; PPN kepala 11% atas 9.000 = 990; total 9.990 == tagihan
    assert d["can_proceed"] is True and d["blocks"] == []
    assert (d["subtotal"], d["discount_amount"], d["tax_amount"], d["total_amount"]) == (10000.0, 1000.0, 990.0, 9990.0)
    p = d["payload"]
    assert p["vendor_id"] == str(VID) and p["vendor_name"] == "Vendor A" and p["original_bill_id"] == str(BID)
    assert [(i["description"], D(str(i["quantity"])), i["unit_price"], i["tax_rate"]) for i in p["items"]] == [
        ("Kain katun", D("3"), 2000, 0), ("Benang", D("2"), 2000, 0)]
    assert p["tax_rate"] == 11 and p["discount_amount"] == 1000 and p["discount_percent"] == 0
    assert {"lines", "notes"} <= set(d) and [x["original_bill_item_id"] for x in d["lines"]] == [str(L1), str(L2)]
    assert "ND_SISA_BARIS_BELUM_DILACAK" in [n["code"] for n in d["notes"]]
    assert kon.tulis() == [] and ("tx", "BEGIN RO", ()) in kon.log


def test_dari_fpb_sebagian_diskon_dokumen_pro_rata(monkeypatch):
    d = _pv(Kon(bill=_tagihan(), items=_baris()), monkeypatch, original_bill_id=str(BID), reason="return",
            lines=[{"original_bill_item_id": str(L1), "quantity": 1}])
    # neto terpilih 2.000 dari 10.000 -> diskon dokumen 200; PPN 11% atas 1.800 = 198; total 1.998
    assert (d["subtotal"], d["discount_amount"], d["tax_amount"], d["total_amount"]) == (2000.0, 200.0, 198.0, 1998.0)
    assert [D(str(i["quantity"])) for i in d["payload"]["items"]] == [D("1")]


def test_dari_fpb_penuh_total_beda_tagihan_diblok(monkeypatch):
    d = _pv(Kon(bill=_tagihan(amount=D("10001.00")), items=_baris()), monkeypatch, original_bill_id=str(BID), reason="return")
    assert [b["code"] for b in d["blocks"]] == ["ND_TOTAL_BEDA_TAGIHAN"] and d["payload"] is None and d["journals"] == []


@pytest.mark.parametrize("tagihan,baris,lines,kode", [
    ({"status_v2": "draft"}, {}, None, "ND_TAGIHAN_TAK_AKTIF"),
    ({"status_v2": "void"}, {}, None, "ND_TAGIHAN_TAK_AKTIF"),
    ({"vendor_id": uuid.UUID(int=5)}, {}, None, "ND_TAGIHAN_VENDOR_LAIN"),
    ({"tax_inclusive": True}, {}, None, "ND_PAJAK_INKLUSIF_BELUM_DIDUKUNG"),
    ({"dpp_manual": 9000}, {}, None, "ND_DPP_MANUAL_BELUM_DIDUKUNG"),
    ({}, {"tax_rate": D("11")}, None, "ND_DISKON_DOKUMEN_BER_PPN_BELUM"),
    ({"tax_rate": 12, "invoice_discount_total": D("0"), "amount": D("11000")}, {}, None, "ND_PAJAK_DPP_BELUM_DIDUKUNG"),
    ({}, {}, [{"original_bill_item_id": str(uuid.UUID(int=999)), "quantity": 1}], "ND_BARIS_BUKAN_TAGIHAN"),
    ({}, {}, [{"original_bill_item_id": str(L1), "quantity": 4}], "ND_QTY_MELEBIHI_TAGIHAN"),
    ({}, {"discount_percent": D("10")}, None, "ND_DISKON_BARIS_BELUM_DIDUKUNG"),
])
def test_dari_fpb_blok_berkode_stabil(monkeypatch, tagihan, baris, lines, kode):
    b = {"original_bill_id": str(BID), "reason": "return", "vendor_id": str(VID)}
    if lines is not None:
        b["lines"] = lines
    d = _pv(Kon(bill=_tagihan(**tagihan), items=_baris(**baris)), monkeypatch, **b)
    assert d["can_proceed"] is False and kode in [x["code"] for x in d["blocks"]], d["blocks"]


def test_dari_fpb_tagihan_tak_ada_atau_tenant_lain(monkeypatch):
    for bill_id in (str(uuid.UUID(int=12345)), "bukan-uuid"):
        d = _pv(Kon(bill=_tagihan(), items=_baris()), monkeypatch, original_bill_id=bill_id, reason="return")
        assert [x["code"] for x in d["blocks"]] == ["ND_TAGIHAN_TAK_ADA"] and d["payload"] is None


def test_dari_fpb_ppn_per_baris_tanpa_diskon_dokumen(monkeypatch):
    d = _pv(Kon(bill=_tagihan(tax_rate=0, invoice_discount_total=D("0"), amount=D("11100")), items=_baris(tax_rate=D("11"))), monkeypatch,
            original_bill_id=str(BID), reason="return")
    assert d["can_proceed"] is True and d["tax_amount"] == 1100.0 and d["total_amount"] == 11100.0
    assert [i["tax_rate"] for i in d["payload"]["items"]] == [11, 11] and d["payload"]["tax_rate"] == 0


def test_bentuk_lama_tanpa_kunci_tambahan(monkeypatch):
    d = _pv(Kon(), monkeypatch, **_badan())
    assert "lines" not in d and "notes" not in d


def test_payload_dari_fpb_diterima_create_utuh(monkeypatch):
    d = _pv(Kon(bill=_tagihan(), items=_baris()), monkeypatch, original_bill_id=str(BID), reason="return")
    kon = Kon(bill=_tagihan(), items=_baris())
    _pasang(monkeypatch, kon)
    r = _jalan(VC.create_vendor_credit(_Req(), VS.CreateVendorCreditRequest(**json.loads(json.dumps(d["payload"])))))
    assert r["data"]["total_amount"] == 9990


# ---------------- (3) cacat produksi terukur ND-BUAT: nomor jurnal HPP per baris bertabrakan ----------------
class KonHpp:
    """Satu transaksi posting: mencatat INSERT jurnal; count(*) jurnal HPP sumber yang sama = jumlah yang sudah disisipkan."""
    def __init__(self): self.nomor = []

    async def fetchval(self, sql, *a):
        q = _n(sql)
        if q.startswith("SELECT count(*) FROM journal_entries"):
            return len(self.nomor)
        if "SUM(quantity_in - quantity_out)" in q:
            return D("10")
        if q.startswith("INSERT"):
            return uuid.uuid4()
        return None

    async def fetchrow(self, sql, *a): return None
    async def fetch(self, sql, *a): return []

    async def execute(self, sql, *a):
        q = _n(sql)
        if q.startswith("INSERT INTO journal_entries"):
            self.nomor.append(a[2])
        return "OK"


def test_hpp_per_baris_nomor_unik_pertama_tetap_lama(monkeypatch):
    from app.services import inventory_helpers as IH

    async def biaya(conn, tid, pid): return {"unit_cost": D("1000"), "cost_source": "wac"}
    async def akun(conn, tid, pid): return {"cogs_account_id": uuid.uuid4(), "inventory_account_id": uuid.uuid4()}
    monkeypatch.setattr(IH, "get_cost_for_sale", biaya)
    monkeypatch.setattr(IH, "resolve_inventory_accounts", akun)
    kon = KonHpp()
    for _ in range(3):
        _jalan(IH.record_inventory_outbound(conn=kon, tenant_id=T, product_id=uuid.uuid4(), product_code="K", product_name="Kain",
                                            warehouse_id=uuid.uuid4(), quantity=1.0, source_type="VENDOR_CREDIT", source_id=NDID,
                                            source_number="VC-2610-0009", user_id=UID, notes="x", receipt_date=date(2026, 10, 29)))
    assert kon.nomor == ["COGS-VC-2610-0009", "COGS-VC-2610-0009-2", "COGS-VC-2610-0009-3"]


class KonVoidHpp:
    """Dua jurnal HPP ND (COGS-X, COGS-X-2) terposting; menghormati LIMIT seperti DB (sabotase 'balik yang pertama saja' terlihat)."""
    def __init__(self):
        self.balik = []
        self.j = [{"id": uuid.UUID(int=201), "journal_number": "COGS-VC-1", "total_debit": D("1000")},
                  {"id": uuid.UUID(int=202), "journal_number": "COGS-VC-1-2", "total_debit": D("500")}]

    async def fetch(self, sql, *a):
        q = _n(sql)
        if "source_type = 'VENDOR_CREDIT_COGS'" in q:
            return self.j[:1] if "LIMIT 1" in q else list(self.j)
        if "FROM journal_lines" in q:
            return [{"account_id": uuid.uuid4(), "debit": D("1"), "credit": D("0"), "memo": "x"}]
        return []

    async def fetchval(self, sql, *a): return None

    async def execute(self, sql, *a):
        q = _n(sql)
        if q.startswith("UPDATE journal_entries SET reversed_by_id"):
            self.balik.append(a[0])
        return "OK"


def test_void_nd_membalik_semua_jurnal_hpp_per_sumber_bukan_per_nomor(monkeypatch):
    """COGS-NOMOR-PER-BARIS: pembaca jurnal HPP ND (void) mencari per source_type+source_id -> jurnal berakhiran -2 ikut dibalik + kartu stoknya."""
    from app.services import inventory_helpers as IH
    from app.services import nota_debit as NDS
    stok = []

    async def balik_stok(**k): stok.append(k["journal_id"])
    monkeypatch.setattr(IH, "record_inventory_reversal", balik_stok)
    kon = KonVoidHpp()
    n = _jalan(NDS.balik_hpp_stok_pajak(kon, {"tenant_id": T, "user_id": UID}, {"id": NDID, "credit_number": "VC-1"}, date(2026, 10, 29)))
    assert n == 2 and kon.balik == [uuid.UUID(int=201), uuid.UUID(int=202)] and stok == kon.balik
    src = inspect.getsource(NDS.balik_hpp_stok_pajak)
    assert "source_type = 'VENDOR_CREDIT_COGS' AND source_id = $2" in src and "COGS-{" not in src
