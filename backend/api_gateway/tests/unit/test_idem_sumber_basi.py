"""Unit idempotensi-sumber (21 Okt 2026): replay HANYA bila dokumen sumber masih ada.

Temuan WORKSPACE: POST /receive-payments tanpa header memakai kunci default dari ISI; draf yang sudah DIHAPUS
tetap di-replay (201 id lama -> GET 404). Kebijakan (putusan MASTER):
  dokumen tidak ada                      -> proses baru (SEMUA jalur: kunci default maupun header klien)
  voided + kunci default-isi             -> proses baru
  voided + kunci header klien            -> replay (semantik retry)
  source_type aksi (tak di SUMBER_BUAT)  -> tak diperiksa (perilaku lama)
"""
import json
import os
import uuid

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

import pytest  # noqa: E402

from app.utils import idempotency as IDEM  # noqa: E402

T = "kaos-biru-konveksi"
DOK = str(uuid.uuid4())


class _Kon:
    """idempotency_keys + satu tabel dokumen tiruan {id: status}."""

    def __init__(self, dokumen=None):
        self.baris = {}            # (tenant, key) -> dict(result, result_id, source_type)
        self.dokumen = dokumen or {}
        self.dihapus = []
        self.sql = []

    def is_in_transaction(self):
        return True

    async def fetchrow(self, sql, tenant, key=None, *a):
        self.sql.append(sql)
        if "FROM idempotency_keys" in sql:
            r = self.baris.get((tenant, key))
            return dict(r) if r else None
        if "AS st FROM" in sql:    # status dokumen: ($1=id, $2=tenant)
            st = self.dokumen.get(str(tenant))
            return {"st": st} if str(tenant) in self.dokumen else None
        raise AssertionError(sql)

    async def fetchval(self, sql, doc_id, tenant):
        self.sql.append(sql)
        return 1 if str(doc_id) in self.dokumen else None

    async def execute(self, sql, *a):
        self.sql.append(sql)
        if sql.strip().startswith("DELETE FROM idempotency_keys"):
            self.baris.pop((a[0], a[1]), None)
            self.dihapus.append(a)
        elif sql.strip().startswith("INSERT INTO idempotency_keys"):
            key, tenant, st, result, result_id = a[0], a[1], a[2], a[3], a[4]
            self.baris.setdefault((tenant, key), {"result": result, "result_id": result_id, "source_type": st})


def _simpan(kon, key, source_type, result, result_id=None):
    kon.baris[(T, key)] = {"result": json.dumps(result), "result_id": result_id, "source_type": source_type}


def _op(catat, hasil=None):
    async def op():
        catat.append(1)
        return dict(hasil or {"data": {"id": str(uuid.uuid4())}})
    return op


@pytest.mark.asyncio
async def test_default_dokumen_hilang_proses_baru_dan_kunci_diganti():
    kon = _Kon(dokumen={})  # draf dihapus
    _simpan(kon, "RCV:x", "RECEIVE_PAYMENT", {"data": {"id": DOK}})
    jalan = []
    r = await IDEM.execute_idempotent(kon, T, "RCV:x", "RECEIVE_PAYMENT", _op(jalan), kunci_default=True)
    assert jalan == [1] and r.was_cached is False
    assert r.data["data"]["id"] != DOK
    assert json.loads(kon.baris[(T, "RCV:x")]["result"])["data"]["id"] == r.data["data"]["id"]  # kunci TERISI ulang


@pytest.mark.asyncio
async def test_default_dokumen_ada_posted_tetap_replay():
    kon = _Kon(dokumen={DOK: "posted"})
    _simpan(kon, "RCV:x", "RECEIVE_PAYMENT", {"data": {"id": DOK}})
    jalan = []
    r = await IDEM.execute_idempotent(kon, T, "RCV:x", "RECEIVE_PAYMENT", _op(jalan), kunci_default=True)
    assert jalan == [] and r.was_cached is True and r.data["data"]["id"] == DOK


@pytest.mark.asyncio
async def test_default_dokumen_voided_proses_baru():
    kon = _Kon(dokumen={DOK: "voided"})
    _simpan(kon, "RCV:x", "RECEIVE_PAYMENT", {"data": {"id": DOK}})
    jalan = []
    r = await IDEM.execute_idempotent(kon, T, "RCV:x", "RECEIVE_PAYMENT", _op(jalan), kunci_default=True)
    assert jalan == [1] and r.was_cached is False


@pytest.mark.asyncio
async def test_header_klien_voided_tetap_replay():
    kon = _Kon(dokumen={DOK: "voided"})
    _simpan(kon, "kunci-klien", "RECEIVE_PAYMENT", {"data": {"id": DOK}})
    jalan = []
    r = await IDEM.execute_idempotent(kon, T, "kunci-klien", "RECEIVE_PAYMENT", _op(jalan), kunci_default=False)
    assert jalan == [] and r.was_cached is True and r.data["data"]["id"] == DOK


@pytest.mark.asyncio
async def test_header_klien_dokumen_hilang_proses_baru():
    kon = _Kon(dokumen={})
    _simpan(kon, "kunci-klien", "BILL_PAYMENT", {"data": {"id": DOK}})
    jalan = []
    r = await IDEM.execute_idempotent(kon, T, "kunci-klien", "BILL_PAYMENT", _op(jalan), kunci_default=False)
    assert jalan == [1] and r.was_cached is False


@pytest.mark.asyncio
async def test_source_type_aksi_tidak_diperiksa():
    kon = _Kon(dokumen={})
    _simpan(kon, "k", "SO_FULFILL", {"data": {"id": DOK}})
    jalan = []
    r = await IDEM.execute_idempotent(kon, T, "k", "SO_FULFILL", _op(jalan), kunci_default=True)
    assert jalan == [] and r.was_cached is True       # perilaku lama, tak ada SELECT dokumen
    assert not any("FROM receive_payments" in q for q in kon.sql)


@pytest.mark.asyncio
async def test_id_tak_terbaca_tidak_menebak():
    kon = _Kon(dokumen={})
    _simpan(kon, "k", "RECEIVE_PAYMENT", {"data": {"id": "bukan-uuid"}})
    jalan = []
    r = await IDEM.execute_idempotent(kon, T, "k", "RECEIVE_PAYMENT", _op(jalan), kunci_default=True)
    assert jalan == [] and r.was_cached is True


@pytest.mark.asyncio
async def test_klien_dokumen_hilang_ambil_replay_none_dan_tidak_409():
    kon = _Kon(dokumen={})
    # isi BEDA + dokumen lama sudah hilang -> bukan konflik, permintaan baru sah
    _simpan(kon, "SI:u:k", "SI_CREATE", {"payload_hash": "lama", "response": {"data": {"id": DOK}}}, result_id=uuid.UUID(DOK))
    assert await IDEM.ambil_replay_klien(kon, T, "SI:u:k", "baru") is None
    assert (T, "SI:u:k") not in kon.baris


@pytest.mark.asyncio
async def test_klien_dokumen_ada_isi_beda_tetap_409_dan_isi_sama_replay():
    kon = _Kon(dokumen={DOK: "draft"})
    _simpan(kon, "SI:u:k", "SI_CREATE", {"payload_hash": "lama", "response": {"data": {"id": DOK}}}, result_id=uuid.UUID(DOK))
    with pytest.raises(IDEM.KunciIdempotensiDipakai):
        await IDEM.ambil_replay_klien(kon, T, "SI:u:k", "baru")
    assert await IDEM.ambil_replay_klien(kon, T, "SI:u:k", "lama") == {"data": {"id": DOK}}


def test_peta_sumber_hanya_source_type_buat_dan_tabel_literal():
    assert set(IDEM.SUMBER_BUAT) == {"RECEIVE_PAYMENT", "BILL_PAYMENT", "SALES_ORDER_CREATE", "QUOTE_CREATE",
                                     "SI_CREATE", "PROFORMA_CREATE", "CN_CREATE", "PO_CREATE", "VD_CREATE",
                                     "BILL_CREATE", "VC_CREATE"}
    for tabel, kol, st in IDEM.SUMBER_BUAT.values():
        assert tabel.replace("_", "").isalnum() and (kol is None or kol.isidentifier())


def test_tiga_jalur_default_meneruskan_kunci_default():
    import inspect
    from app.routers import receive_payments as RP, bill_payments as BP, sales_invoices as SI
    assert 'kunci_default=not request.headers.get("X-Idempotency-Key")' in inspect.getsource(RP)
    # bill_payments: kunci HANYA klien (putusan pemilik 22 Okt 2026) -> tanpa header = tanpa dedup, tak ada kunci default
    src = inspect.getsource(BP.create_bill_payment)
    assert "kunci_idempotensi_klien(request)" in src and "build_idempotency_default" not in src and "kunci_default=False" in src
    assert "sumber_basi(conn" in inspect.getsource(SI.record_payment)
