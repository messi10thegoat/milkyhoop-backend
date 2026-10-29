"""KARAKTERISASI buat Nota Debit lama (ND-BUAT, 29 Okt 2026) -- DITULIS SEBELUM uang permintaan Decimal + pagar DPP/diskon baris + bentuk
dari-FPB, dan WAJIB tetap hijau sesudahnya untuk ND tanpa pajak dan ND 11% (syarat MASTER): respons create & pratinjau, argumen INSERT,
galat lama, sidik idempotensi (Law 14: badan nominal bulat = sidik byte-identik dengan medan `int` lama), payload pratinjau."""
import asyncio
import json
import os
import uuid
from decimal import Decimal

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from app.routers import vendor_credits as VC  # noqa: E402
from app.schemas import vendor_credits as VS  # noqa: E402
from app.services import idem_buat as IB  # noqa: E402
from app.services import nota_debit_rencana as NDR  # noqa: E402
from app.utils.idempotency import hash_payload  # noqa: E402

T = "kaos-biru-konveksi"
UID = uuid.UUID("99999999-8888-7777-6666-555555555555")
VID = uuid.UUID("11111111-0000-0000-0000-000000000001")
BID = uuid.UUID("22222222-0000-0000-0000-000000000002")
NDID = uuid.UUID("33333333-0000-0000-0000-000000000003")
D = Decimal


def _n(sql):
    return " ".join(sql.split())


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


class Kon:
    def __init__(self, vendor=True, bill=None):
        self.vendor, self.bill, self.log = vendor, bill, []

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
            return self.bill
        return None

    async def fetch(self, sql, *a):
        q = self._c("fetch", sql, a)
        if "FROM tax_codes" in q:  # resolver faktor DPP (kode baru): tarif 11 = DPP penuh
            return [{"dpp_factor_num": 1, "dpp_factor_den": 1}]
        return []

    def sisip(self, awal):
        return [a for j, q, a in self.log if q.startswith(awal)]


class Pool:
    def __init__(self, kon): self.kon = kon

    def acquire(self):
        kon = self.kon

        class CM:
            async def __aenter__(s): return kon
            async def __aexit__(s, *a): return False
        return CM()


class _Req:
    def __init__(self, kunci=None, user=UID):
        self.headers = {"X-Idempotency-Key": kunci} if kunci else {}
        self.state = type("S", (), {"user": {"tenant_id": T, "user_id": str(user) if user else None}})()


def _pasang(mp, kon):
    async def gp(): return Pool(kon)
    mp.setattr(VC, "get_pool", gp)

    async def prasyarat(pool, tid=None): return None
    mp.setattr(VC, "_ensure_role_preconditions", prasyarat)

    async def nomor(conn, tid): return "VC-2610-0099"
    mp.setattr(NDR, "perkiraan_nomor_nd", nomor)

    async def rencana(conn, ctx, vc, pratinjau=False, items=None):
        return {"blocks": NDR.Blok(True), "journals": [{"total": float(vc["total_amount"]), "tax": float(vc["tax_amount"])}],
                "stock": [], "bills": []}
    mp.setattr(NDR, "rencana_posting", rencana)


BADAN = {
    "tanpa_pajak": {"vendor_id": str(VID), "vendor_name": "Vendor A", "vendor_credit_date": "2026-10-29", "reason": "return",
                    "items": [{"description": "Kain", "quantity": 2, "unit_price": 1000}]},
    "pajak_11_baris": {"vendor_id": str(VID), "vendor_name": "Vendor A", "vendor_credit_date": "2026-10-29", "reason": "return",
                       "items": [{"description": "Kain", "quantity": 3, "unit_price": 1000, "tax_rate": 11}]},
    "pajak_11_kepala_diskon_dok": {"vendor_name": "Vendor A", "vendor_credit_date": "2026-10-29", "reason": "damaged",
                                   "items": [{"description": "Kain", "quantity": 4, "unit_price": 2500}],
                                   "discount_percent": 10, "tax_rate": 11},
}
# (subtotal, diskon dokumen, pajak, total) yang dihitung kode lama
HARAP = {"tanpa_pajak": (D("2000.00"), D("0"), D("0.00"), D("2000.00")),
         "pajak_11_baris": (D("3000.00"), D("0"), D("330.00"), D("3330.00")),
         "pajak_11_kepala_diskon_dok": (D("10000.00"), D("1000.00"), D("990.00"), D("9990.00"))}
TOTAL_JSON = {"tanpa_pajak": 2000, "pajak_11_baris": 3330, "pajak_11_kepala_diskon_dok": 9990}


def _jalan(c):
    return asyncio.run(c)


# ---------------------------------------------------------------- create
@pytest.mark.parametrize("kasus", sorted(BADAN))
def test_create_respons_dan_insert_kepala_persis(monkeypatch, kasus):
    kon = Kon()
    _pasang(monkeypatch, kon)
    r = _jalan(VC.create_vendor_credit(_Req(), VS.CreateVendorCreditRequest(**BADAN[kasus])))
    assert r == {"success": True, "message": "Nota debit dibuat.",
                 "data": {"id": str(NDID), "vendor_credit_number": "VC-2610-0099", "total_amount": TOTAL_JSON[kasus], "status": "draft"}}
    (k,) = kon.sisip("INSERT INTO vendor_credits")
    sub, disk, pajak, total = HARAP[kasus]
    assert (D(str(k[6])), D(str(k[8])), D(str(k[10])), D(str(k[11]))) == (sub, disk, pajak, total)
    assert k[0] == T and k[1] == "VC-2610-0099" and k[3] == "Vendor A"
    baris = kon.sisip("INSERT INTO vendor_credit_items")
    assert len(baris) == 1 and D(str(baris[0][6])) == D(str(BADAN[kasus]["items"][0]["unit_price"]))


def test_create_tanpa_user_401(monkeypatch):
    kon = Kon()
    _pasang(monkeypatch, kon)
    with pytest.raises(HTTPException) as e:
        _jalan(VC.create_vendor_credit(_Req(user=None), VS.CreateVendorCreditRequest(**BADAN["tanpa_pajak"])))
    assert (e.value.status_code, e.value.detail) == (401, "ID pengguna wajib ada.") and kon.log == []


def test_create_vendor_tak_ada_400_teks_tanpa_insert(monkeypatch):
    kon = Kon(vendor=False)
    _pasang(monkeypatch, kon)
    with pytest.raises(HTTPException) as e:
        _jalan(VC.create_vendor_credit(_Req(), VS.CreateVendorCreditRequest(**BADAN["tanpa_pajak"])))
    assert (e.value.status_code, e.value.detail) == (400, "Vendor tidak ditemukan.") and not kon.sisip("INSERT")


def test_create_tagihan_vendor_lain_400_teks(monkeypatch):
    kon = Kon(bill={"id": BID, "invoice_number": "PB-1", "vendor_id": uuid.uuid4(), "status": "unpaid", "status_v2": "posted"})
    _pasang(monkeypatch, kon)
    with pytest.raises(HTTPException) as e:
        _jalan(VC.create_vendor_credit(_Req(), VS.CreateVendorCreditRequest(**{**BADAN["tanpa_pajak"], "original_bill_id": str(BID)})))
    assert (e.value.status_code, e.value.detail) == (400, "Tagihan PB-1 milik vendor lain.") and not kon.sisip("INSERT")


@pytest.mark.parametrize("kasus", sorted(BADAN))
def test_create_sidik_idem_byte_identik_dengan_badan_int(monkeypatch, kasus):
    """Law 14: sidik = hash_payload(body.model_dump(mode="json")). Badan nominal BULAT -> sidik SAMA dengan dict int literal (= medan int lama)."""
    kon = Kon()
    _pasang(monkeypatch, kon)
    dicatat = []

    async def mulai(conn, ctx, kunci, prefix, body, tabel, nama, response=None):
        dicatat.append((kunci, prefix, tabel, nama, hash_payload(body.model_dump(mode="json")), body.model_dump(mode="json")))
        return f"{prefix}:{ctx['user_id']}:{kunci}", "S", None

    async def simpan(conn, ctx, kp, sd, st, resp, rid): return resp
    monkeypatch.setattr(IB, "mulai", mulai)
    monkeypatch.setattr(IB, "simpan", simpan)
    _jalan(VC.create_vendor_credit(_Req("K-1"), VS.CreateVendorCreditRequest(**BADAN[kasus])))
    kunci, prefix, tabel, nama, sidik, dump = dicatat[0]
    assert (kunci, prefix, tabel, nama) == ("K-1", "VC_CREATE", "vendor_credits", "vendor_credit")
    for b in dump["items"]:
        assert isinstance(b["unit_price"], int) and isinstance(b["discount_amount"], int)  # JSON bulat = int, seperti dulu
    assert isinstance(dump["discount_amount"], int)
    assert sidik == hash_payload(json.loads(json.dumps(dump)))


# ---------------------------------------------------------------- pratinjau
KUNCI_RESPONS = {"action", "can_proceed", "blocks", "vendor_credit_number_preview", "vendor_credit_number_estimated", "items", "subtotal",
                 "discount_amount", "tax_amount", "total_amount", "original_bill_number", "journals", "stock", "bills",
                 "bank_transactions", "payload", "preview"}


@pytest.mark.parametrize("kasus", sorted(BADAN))
def test_pratinjau_respons_kunci_angka_payload_persis(monkeypatch, kasus):
    kon = Kon()
    _pasang(monkeypatch, kon)
    badan = VS.CreateVendorCreditRequest(**BADAN[kasus])
    r = _jalan(VC.preview_create_vendor_credit(_Req(), _badan_pratinjau(badan)))
    d = r["data"]
    assert r["success"] is True and set(d) == KUNCI_RESPONS
    sub, disk, pajak, total = HARAP[kasus]
    assert (d["subtotal"], d["discount_amount"], d["tax_amount"], d["total_amount"]) == (float(sub), float(disk), float(pajak), float(total))
    assert d["can_proceed"] is True and d["blocks"] == [] and d["vendor_credit_number_preview"] == "VC-2610-0099"
    assert d["journals"] == [{"total": float(total), "tax": float(pajak)}]
    assert d["payload"] == json.loads(json.dumps(badan.model_dump(mode="json")))
    for b in d["payload"]["items"]:
        assert isinstance(b["unit_price"], int)
    assert ("tx", "BEGIN RO", ()) in kon.log and kon.log[-1] == ("tx", "ROLLBACK", ())
    assert not kon.sisip("INSERT") and not kon.sisip("UPDATE") and not kon.sisip("DELETE")


def test_pratinjau_vendor_tak_ada_blok_200(monkeypatch):
    kon = Kon(vendor=False)
    _pasang(monkeypatch, kon)
    d = _jalan(VC.preview_create_vendor_credit(_Req(), _badan_pratinjau(VS.CreateVendorCreditRequest(**BADAN["tanpa_pajak"]))))["data"]
    assert d["can_proceed"] is False and [b["code"] for b in d["blocks"]] == ["VENDOR_TIDAK_DITEMUKAN"]
    assert d["vendor_credit_number_preview"] is None and d["journals"] == [] and d["subtotal"] == 2000.0


def _badan_pratinjau(badan):
    """Pratinjau menerima model badan rute (dulu CreateVendorCreditRequest; ND-BUAT: VendorCreditPreviewRequest superset)."""
    import inspect
    tipe = inspect.signature(VC.preview_create_vendor_credit).parameters["body"].annotation
    return badan if tipe is VS.CreateVendorCreditRequest else tipe(**badan.model_dump())
