"""KARAKTERISASI hapus draf tunggal Pembelian (B1b-2, 29 Okt 2026) -- DITULIS SEBELUM inti dipindah ke hapus_draf_tagihan_core /
hapus_draf_bayar_core / hapus_draf_umv_core / hapus_draf_nd_core, dan WAJIB tetap hijau sesudahnya (arahan MASTER: status, idem, galat,
respons). Per handler: respons sukses persis; SQL tulis + parameter; urutan idem -> kunci -> baca FOR UPDATE -> penentu -> tulis; 404 / bukan
draf / galat lain (kode + teks); replay ber-kunci = respons lama tanpa tulis; tagihan: app.user_id sebelum DELETE (V230), objek lampiran
dihapus SESUDAH commit dan TIDAK saat gagal, galat FK = 400 teks lama, PO tertaut dipulihkan."""
import asyncio
import os
import uuid
from decimal import Decimal

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

import asyncpg  # noqa: E402
import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from app.routers import bill_payments as RBP  # noqa: E402
from app.routers import bills as RB  # noqa: E402
from app.routers import vendor_credits as RVC  # noqa: E402
from app.routers import vendor_deposits as RVD  # noqa: E402
from app.services import bills_service as BS  # noqa: E402
from app.services import idem_buat as IB  # noqa: E402
from app.services import po_daftar as PDF  # noqa: E402
from app.services import tagihan_aksi as TA  # noqa: E402

T = "kaos-biru-konveksi"
DID = uuid.UUID("11111111-2222-3333-4444-555555555555")
UID = uuid.UUID("99999999-8888-7777-6666-555555555555")
D = Decimal


def _n(sql):
    return " ".join(sql.split())


class _Tx:
    def __init__(self, kon): self.kon = kon
    async def __aenter__(self):
        self.kon.log.append(("tx", "BEGIN", ()))
        return self
    async def __aexit__(self, et, *a):
        self.kon.log.append(("tx", "ROLLBACK" if et else "COMMIT", ()))
        return False


class Kon:
    """baris per tabel (FROM <tabel>); meledak = awalan SQL yang melempar galat (RuntimeError / asyncpg FK)."""
    def __init__(self, baris=None, hapus_ok=True, meledak=None, galat=RuntimeError("db meledak")):
        self.baris, self.hapus_ok, self.meledak, self.galat = baris or {}, hapus_ok, meledak, galat
        self.log = []

    def transaction(self, **kw): return _Tx(self)

    def _c(self, j, sql, a):
        q = _n(sql)
        self.log.append((j, q, a))
        if self.meledak and q.startswith(self.meledak):
            raise self.galat
        return q

    def _cari(self, q):
        for tabel, r in self.baris.items():
            if f"FROM {tabel} " in q + " ":
                return r
        return None

    async def execute(self, sql, *a):
        self._c("exec", sql, a)
        return "OK"

    async def fetchrow(self, sql, *a):
        return self._cari(self._c("fetchrow", sql, a))

    async def fetch(self, sql, *a):
        q = self._c("fetch", sql, a)
        if q.startswith("DELETE FROM bill_attachments"):
            return [{"file_path": "lampiran/a.pdf"}]
        return []

    async def fetchval(self, sql, *a):
        q = self._c("fetchval", sql, a)
        if q.startswith("DELETE FROM vendor_credits"):
            return DID if self.hapus_ok else None
        return None

    def tulis(self):
        return [(q, a) for j, q, a in self.log if q.startswith(("DELETE", "UPDATE", "INSERT"))]

    def indeks(self, pred):
        return next(i for i, x in enumerate(self.log) if pred(x))


class Pool:
    def __init__(self, kon): self.kon = kon

    def acquire(self):
        kon = self.kon

        class CM:
            async def __aenter__(s): return kon
            async def __aexit__(s, *a): return False
        return CM()


class _Req:
    def __init__(self, kunci=None):
        self.headers = {"X-Idempotency-Key": kunci} if kunci else {}
        self.state = type("S", (), {"user": {"tenant_id": T, "user_id": str(UID)}})()


class Idem:
    def __init__(self, lama=None): self.lama, self.mulai, self.disimpan = lama, [], []

    def pasang(self, mp):
        async def mulai_aksi(conn, ctx, kunci, prefix, doc_id, isi, response=None):
            conn.log.append(("idem", prefix, ()))
            self.mulai.append((kunci, prefix, str(doc_id), isi() if callable(isi) else isi))
            if not kunci:
                return None, None, None
            return f"{prefix}:{ctx['user_id']}:{doc_id}:{kunci}", "S", self.lama

        async def simpan(conn, ctx, kp, sd, st, resp, rid):
            from fastapi.encoders import jsonable_encoder
            self.disimpan.append((kp, st, str(rid)))
            return jsonable_encoder(resp)
        mp.setattr(IB, "mulai_aksi", mulai_aksi)
        mp.setattr(IB, "simpan", simpan)
        return self


def _jalan(c):
    return asyncio.run(c)


# ============================================================ FAKTUR PEMBELIAN (DELETE /bills/{id})
DRAF = {"id": DID, "invoice_number": "PB-2610-0099", "amount_paid": D("0"), "status": "unpaid", "status_v2": "draft",
        "accounting_status": "draft", "operational_status": "unpaid"}


def _pasang_tagihan(mp, kon, po=()):
    async def gp(): return Pool(kon)
    mp.setattr(RB, "get_pool", gp)

    async def svc():
        return BS.BillsService(Pool(kon), accounting_facade=None)
    mp.setattr(RB, "get_bills_service", svc)
    objek = []

    async def hapus_objek(paths, konteks):
        kon.log.append(("objek", "hapus", tuple(paths)))
        objek.append(paths)
    mp.setattr(BS, "hapus_objek_sesudah_commit", hapus_objek)

    async def terkait(conn, tid, bid): return list(po)
    pulih = []

    async def pulihkan(conn, tid, ids): pulih.append((tid, list(ids)))
    mp.setattr(PDF, "po_terkait_bill", terkait)
    mp.setattr(PDF, "pulihkan_tagih", pulihkan)
    return objek, pulih


def test_tagihan_sukses_respons_sql_userid_dan_objek_sesudah_commit(monkeypatch):
    kon = Kon({"bills": DRAF})
    objek, pulih = _pasang_tagihan(monkeypatch, kon)
    r = _jalan(RB.delete_bill(_Req(), DID))
    assert r == {"success": True, "message": "Faktur pembelian dihapus."}
    sel = kon.indeks(lambda x: x[0] == "fetchrow" and "FROM bills" in x[1])
    assert kon.log[sel][1].endswith("FOR UPDATE") and kon.log[sel][2] == (DID, T)
    i_user = kon.indeks(lambda x: "set_config('app.user_id'" in x[1])
    i_lamp = kon.indeks(lambda x: x[1].startswith("DELETE FROM bill_attachments"))
    i_del = kon.indeks(lambda x: x[1].startswith("DELETE FROM bills"))
    i_commit = kon.indeks(lambda x: x == ("tx", "COMMIT", ()))
    i_obj = kon.indeks(lambda x: x[0] == "objek")
    assert sel < i_user < i_lamp < i_del < i_commit < i_obj
    assert kon.log[i_user][2] == (str(UID),) and kon.log[i_del][1] == "DELETE FROM bills WHERE id = $1 AND tenant_id = $2"
    assert kon.log[i_del][2] == (DID, T) and objek == [["lampiran/a.pdf"]] and pulih == []
    i_ten = kon.indeks(lambda x: "set_config('app.tenant_id'" in x[1])
    assert kon.log[i_ten][2] == (T,) and i_ten < sel


def test_tagihan_po_tertaut_dipulihkan_di_transaksi_sama(monkeypatch):
    po = [uuid.uuid4()]
    kon = Kon({"bills": DRAF})
    _, pulih = _pasang_tagihan(monkeypatch, kon, po=po)
    _jalan(RB.delete_bill(_Req(), DID))
    assert pulih == [(T, po)]


def test_tagihan_tak_ada_404_teks_tanpa_tulis_tanpa_objek(monkeypatch):
    kon = Kon({})
    objek, _ = _pasang_tagihan(monkeypatch, kon)
    with pytest.raises(HTTPException) as e:
        _jalan(RB.delete_bill(_Req(), DID))
    assert e.value.status_code == 404 and e.value.detail == "Faktur pembelian tidak ditemukan."
    assert kon.tulis() == [] and objek == []


@pytest.mark.parametrize("ubah", [{"status_v2": "posted", "accounting_status": "posted", "status": "unpaid"},
                                  {"status": "void", "status_v2": "void"},
                                  {"amount_paid": D("1000")}])
def test_tagihan_blok_400_pesan_penentu_pertama_tanpa_tulis(monkeypatch, ubah):
    b = {**DRAF, **ubah}
    kon = Kon({"bills": b})
    objek, _ = _pasang_tagihan(monkeypatch, kon)
    with pytest.raises(HTTPException) as e:
        _jalan(RB.delete_bill(_Req(), DID))
    assert e.value.status_code == 400 and e.value.detail == TA.blok_hapus(b, None)[0]["message"]
    assert kon.tulis() == [] and objek == []


def test_tagihan_ada_pembayaran_teks_persis(monkeypatch):
    kon = Kon({"bills": {**DRAF, "amount_paid": D("5")}})
    _pasang_tagihan(monkeypatch, kon)
    with pytest.raises(HTTPException) as e:
        _jalan(RB.delete_bill(_Req(), DID))
    assert e.value.detail == "Faktur pembelian yang sudah dibayar tidak bisa dihapus, batalkan saja."


def test_tagihan_referensi_fk_400_teks_lama_tanpa_objek(monkeypatch):
    fk = asyncpg.ForeignKeyViolationError("fk")
    fk.detail = "Key (id)=(x) is still referenced"
    kon = Kon({"bills": DRAF}, meledak="DELETE FROM bills", galat=fk)
    objek, _ = _pasang_tagihan(monkeypatch, kon)
    with pytest.raises(HTTPException) as e:
        _jalan(RB.delete_bill(_Req(), DID))
    assert e.value.status_code == 400
    assert e.value.detail == "Faktur draft tidak dapat dihapus karena masih memiliki referensi terkait: Key (id)=(x) is still referenced"
    assert objek == []


def test_tagihan_galat_lain_500(monkeypatch):
    kon = Kon({"bills": DRAF}, meledak="DELETE FROM bills")
    _pasang_tagihan(monkeypatch, kon)
    with pytest.raises(HTTPException) as e:
        _jalan(RB.delete_bill(_Req(), DID))
    assert e.value.status_code == 500 and e.value.detail == "Gagal menghapus faktur pembelian."


def test_tagihan_idem_prefix_simpan_dan_replay(monkeypatch):
    kon = Kon({"bills": DRAF})
    _pasang_tagihan(monkeypatch, kon)
    idem = Idem().pasang(monkeypatch)
    r = _jalan(RB.delete_bill(_Req("K-1"), DID))
    assert idem.mulai[0] == ("K-1", "BILL_DELETE", str(DID), {}) and idem.disimpan == [(f"BILL_DELETE:{UID}:{DID}:K-1", "BILL_DELETE", str(DID))]
    assert r == {"success": True, "message": "Faktur pembelian dihapus."}
    assert kon.indeks(lambda x: x[0] == "idem") < kon.indeks(lambda x: "FROM bills" in x[1])
    lama = {"success": True, "message": "lama"}
    kon2 = Kon({"bills": {**DRAF, "status_v2": "void"}})
    objek, _ = _pasang_tagihan(monkeypatch, kon2)
    Idem(lama=lama).pasang(monkeypatch)
    assert _jalan(RB.delete_bill(_Req("K-1"), DID)) == lama and kon2.tulis() == [] and objek == []


# ============================================================ PEMBAYARAN KELUAR (DELETE /bill-payments/{id})
BAYAR = {"id": DID, "payment_number": "BP-2610-0007", "status": "draft", "total_amount": D("100")}


def _pasang_pool(mp, mod, kon):
    async def gp(): return Pool(kon)
    mp.setattr(mod, "get_pool", gp)


def test_bayar_sukses_respons_sql_kunci_urutan(monkeypatch):
    kon = Kon({"bill_payments_v2": BAYAR})
    _pasang_pool(monkeypatch, RBP, kon)
    r = _jalan(RBP.delete_bill_payment(_Req(), str(DID)))
    assert (r["success"], r["message"], r["data"]) == (True, "Pembayaran BP-2610-0007 dihapus.", {"id": str(DID)})
    i_ten = kon.indeks(lambda x: x[1] == f"SET LOCAL app.tenant_id = '{T}'")
    i_lock = kon.indeks(lambda x: "pg_advisory_xact_lock" in x[1])
    i_baca = kon.indeks(lambda x: x[0] == "fetchrow" and "FROM bill_payments_v2" in x[1])
    assert kon.log[i_lock][2] == (f"BILL_PAYMENT:{DID}",) and kon.log[i_baca][1].endswith("FOR UPDATE")
    assert kon.tulis() == [("DELETE FROM bill_payment_allocations WHERE payment_id = $1::uuid", (str(DID),)),
                           ("DELETE FROM bill_payments_v2 WHERE id = $1::uuid", (str(DID),))]
    assert i_ten < i_lock < i_baca < kon.indeks(lambda x: x[1].startswith("DELETE"))


@pytest.mark.parametrize("baris,kode,teks", [
    ({}, 404, "Pembayaran tidak ditemukan."),
    ({"bill_payments_v2": {**BAYAR, "status": "posted"}}, 400, "Hanya pembayaran berstatus Draf yang bisa dihapus."),
    ({"bill_payments_v2": {**BAYAR, "status": "voided"}}, 400, "Hanya pembayaran berstatus Draf yang bisa dihapus."),
])
def test_bayar_tolak_teks_tanpa_tulis(monkeypatch, baris, kode, teks):
    kon = Kon(baris)
    _pasang_pool(monkeypatch, RBP, kon)
    with pytest.raises(HTTPException) as e:
        _jalan(RBP.delete_bill_payment(_Req(), str(DID)))
    assert (e.value.status_code, e.value.detail) == (kode, teks) and kon.tulis() == []


def test_bayar_galat_lain_500(monkeypatch):
    kon = Kon({"bill_payments_v2": BAYAR}, meledak="DELETE FROM bill_payments_v2")
    _pasang_pool(monkeypatch, RBP, kon)
    with pytest.raises(HTTPException) as e:
        _jalan(RBP.delete_bill_payment(_Req(), str(DID)))
    assert (e.value.status_code, e.value.detail) == (500, "Gagal menghapus pembayaran.")


def test_bayar_idem_prefix_dan_replay(monkeypatch):
    kon = Kon({"bill_payments_v2": BAYAR})
    _pasang_pool(monkeypatch, RBP, kon)
    idem = Idem().pasang(monkeypatch)
    _jalan(RBP.delete_bill_payment(_Req("K-1"), str(DID)))
    assert idem.mulai[0] == ("K-1", "BP_DELETE", str(DID), {}) and idem.disimpan[0] == (f"BP_DELETE:{UID}:{DID}:K-1", "BP_DELETE", str(DID))
    assert kon.indeks(lambda x: x[0] == "idem") < kon.indeks(lambda x: "pg_advisory_xact_lock" in x[1])
    kon2 = Kon({"bill_payments_v2": {**BAYAR, "status": "posted"}})
    _pasang_pool(monkeypatch, RBP, kon2)
    Idem(lama={"success": True, "message": "lama"}).pasang(monkeypatch)
    assert _jalan(RBP.delete_bill_payment(_Req("K-1"), str(DID))) == {"success": True, "message": "lama"} and kon2.tulis() == []


# ============================================================ UANG MUKA PEMBELIAN (DELETE /vendor-deposits/{id})
UMV = {"id": DID, "deposit_number": "VD-2026-00099", "status": "draft", "tenant_id": T}


def test_umv_sukses_respons_inggris_lama_sql_dan_kunci(monkeypatch):
    kon = Kon({"vendor_deposits": UMV})
    _pasang_pool(monkeypatch, RVD, kon)
    r = _jalan(RVD.delete_vendor_deposit(_Req(), DID))
    assert r == {"message": "Vendor deposit deleted"}
    i_lock = kon.indeks(lambda x: "pg_advisory_xact_lock" in x[1])
    i_baca = kon.indeks(lambda x: x[0] == "fetchrow" and "FROM vendor_deposits" in x[1])
    assert kon.log[i_lock][2] == (f"VENDOR_DEPOSIT:{DID}",)
    assert kon.log[i_baca][1] == "SELECT * FROM vendor_deposits WHERE id = $1 AND tenant_id = $2 FOR UPDATE" and i_lock < i_baca
    assert kon.tulis() == [("DELETE FROM vendor_deposits WHERE id = $1 AND tenant_id = $2 AND status = 'draft'", (DID, T))]


@pytest.mark.parametrize("baris,kode,teks", [
    ({}, 404, "Uang muka vendor tidak ditemukan."),
    ({"vendor_deposits": {**UMV, "status": "posted"}}, 400, "Hanya uang muka draf yang bisa dihapus."),
    ({"vendor_deposits": {**UMV, "status": "void"}}, 400, "Hanya uang muka draf yang bisa dihapus."),
])
def test_umv_tolak_teks_tanpa_tulis(monkeypatch, baris, kode, teks):
    kon = Kon(baris)
    _pasang_pool(monkeypatch, RVD, kon)
    with pytest.raises(HTTPException) as e:
        _jalan(RVD.delete_vendor_deposit(_Req(), DID))
    assert (e.value.status_code, e.value.detail) == (kode, teks) and kon.tulis() == []


def test_umv_idem_prefix_dan_replay(monkeypatch):
    kon = Kon({"vendor_deposits": UMV})
    _pasang_pool(monkeypatch, RVD, kon)
    idem = Idem().pasang(monkeypatch)
    _jalan(RVD.delete_vendor_deposit(_Req("K-1"), DID))
    assert idem.mulai[0] == ("K-1", "UMV_DELETE", str(DID), {}) and idem.disimpan[0][1] == "UMV_DELETE"
    kon2 = Kon({"vendor_deposits": {**UMV, "status": "posted"}})
    _pasang_pool(monkeypatch, RVD, kon2)
    Idem(lama={"message": "lama"}).pasang(monkeypatch)
    assert _jalan(RVD.delete_vendor_deposit(_Req("K-1"), DID)) == {"message": "lama"} and kon2.tulis() == []


# ============================================================ NOTA DEBIT (DELETE /vendor-credits/{id})
ND = {"id": DID, "status": "draft", "credit_number": "VC-2610-0099"}


def test_nd_sukses_respons_sql_kunci(monkeypatch):
    kon = Kon({"vendor_credits": ND})
    _pasang_pool(monkeypatch, RVC, kon)
    r = _jalan(RVC.delete_vendor_credit(_Req(), DID))
    assert r == {"success": True, "message": "Nota debit dihapus.", "data": {"id": str(DID), "vendor_credit_number": "VC-2610-0099"}}
    i_lock = kon.indeks(lambda x: "pg_advisory_xact_lock" in x[1])
    i_baca = kon.indeks(lambda x: x[0] == "fetchrow" and "FROM vendor_credits" in x[1])
    assert kon.log[i_lock][2] == (f"VENDOR_CREDIT:{DID}",) and i_lock < i_baca
    assert kon.log[i_baca][1] == "SELECT id, status, credit_number FROM vendor_credits WHERE id = $1 AND tenant_id = $2 FOR UPDATE"
    assert kon.tulis() == [("DELETE FROM vendor_credits WHERE id = $1 AND tenant_id = $2 AND status = 'draft' RETURNING id", (DID, T))]


@pytest.mark.parametrize("baris,hapus_ok,kode,teks", [
    ({}, True, 404, "Nota debit tidak ditemukan."),
    ({"vendor_credits": {**ND, "status": "posted"}}, True, 400,
     "Hanya nota debit berstatus Draf yang bisa dihapus. Nota debit terbit dibatalkan, bukan dihapus."),
    ({"vendor_credits": ND}, False, 409, "Nota debit sudah berubah status. Muat ulang halaman."),
])
def test_nd_tolak_teks(monkeypatch, baris, hapus_ok, kode, teks):
    kon = Kon(baris, hapus_ok=hapus_ok)
    _pasang_pool(monkeypatch, RVC, kon)
    with pytest.raises(HTTPException) as e:
        _jalan(RVC.delete_vendor_credit(_Req(), DID))
    assert (e.value.status_code, e.value.detail) == (kode, teks)
    if kode != 409:
        assert kon.tulis() == []


def test_nd_galat_lain_500(monkeypatch):
    kon = Kon({"vendor_credits": ND}, meledak="DELETE FROM vendor_credits")
    _pasang_pool(monkeypatch, RVC, kon)
    with pytest.raises(HTTPException) as e:
        _jalan(RVC.delete_vendor_credit(_Req(), DID))
    assert (e.value.status_code, e.value.detail) == (500, "Gagal menghapus nota debit.")


def test_nd_idem_prefix_dan_replay(monkeypatch):
    kon = Kon({"vendor_credits": ND})
    _pasang_pool(monkeypatch, RVC, kon)
    idem = Idem().pasang(monkeypatch)
    _jalan(RVC.delete_vendor_credit(_Req("K-1"), DID))
    assert idem.mulai[0] == ("K-1", "VC_DELETE", str(DID), {}) and idem.disimpan[0][1] == "VC_DELETE"
    assert kon.indeks(lambda x: x[0] == "idem") < kon.indeks(lambda x: "pg_advisory_xact_lock" in x[1])
    kon2 = Kon({"vendor_credits": {**ND, "status": "posted"}})
    _pasang_pool(monkeypatch, RVC, kon2)
    Idem(lama={"success": True, "message": "lama"}).pasang(monkeypatch)
    assert _jalan(RVC.delete_vendor_credit(_Req("K-1"), DID)) == {"success": True, "message": "lama"} and kon2.tulis() == []
