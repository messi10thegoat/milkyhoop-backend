"""U8 (5 Okt 2026, GO MASTER): daftar Penerimaan memuat penerimaan VOIDED secara ADITIF.

Dulu penerimaan voided TAK PERNAH tampil (jurnalnya dibalik -> hilang dari daftar jurnal efektif): ?status=voided = 0 baris dan
daftar kas 35 != summary 40. Kini baris voided HANYA bila status=voided atau include_voided=true (+ voided_at, void_reason).
Penjaga: (1) SQL daftar+hitung untuk panggilan LAMA identik dgn sebelum U8 (hash SQL ternormalisasi dibekukan dari kode lama);
(2) bentuk respons lama utuh (tanpa kunci baru, dict biasa); (3) pemanggil langsung tanpa argumen (objek Query truthy) TIDAK
mengaktifkan; (4) voided: CTE bertenant eksplisit + status='voided', respons JSON dgn voided_at/void_reason + koersi angka sama;
(5) kesetaraan daftar vs summary dibuktikan di data nyata kaos (harness), bukan di tes ini."""
import asyncio
import hashlib
from datetime import date, datetime, timezone
from fastapi.responses import JSONResponse

import pytest

from app.routers import receive_payments as RP

# Hash SQL ternormalisasi (spasi dirapatkan) dari kode LAMA (master 7c054bcd, sebelum U8) per panggilan lama.
HASH_LAMA = {
    "bawaan": {"count": "f67eb897f5f7e57e", "main": "0dcb5b578cd12767"},
    "cari": {"count": "689c9cef86e00b3e", "main": "3695fde8630a41fe"},
    "cash": {"count": "3be3e3407bb16ada", "main": "ca29693690b16032"},
    "posted": {"count": "06042848d4c14e9a", "main": "0014f6c1b5e6e2db"},
    "tgl": {"count": "e5215cea13d70c86", "main": "96884ff47f3960d1"},
}
PANGGILAN = {"bawaan": {}, "posted": {"status": "posted"}, "cash": {"source_type": "cash"}, "cari": {"search": "abc"},
             "tgl": {"date_from": date(2026, 1, 1)}}


class _Konn:
    def __init__(self, baris=()):
        self.baris, self.sql = list(baris), []

    async def execute(self, sql, *a):
        self.sql.append(("exec", sql))

    async def fetchval(self, sql, *a):
        self.sql.append(("count", sql))
        return len(self.baris)

    async def fetch(self, sql, *a):
        self.sql.append(("main", sql))
        return list(self.baris) if "unified" in sql else []

    async def fetchrow(self, sql, *a):
        self.sql.append(("row", sql))
        return None


class _Req:
    class state:
        user = {"tenant_id": "t1", "user_id": "0bccdb25-fdf0-4e99-9024-b9a20846f76c"}


def _jalan(monkeypatch, baris=(), **kw):
    k = _Konn(baris)

    class _P:
        def acquire(self_):
            class _A:
                async def __aenter__(s):
                    return k

                async def __aexit__(s, *a):
                    return False
            return _A()

    async def gp():
        return _P()
    monkeypatch.setattr(RP, "get_pool", gp)
    a = dict(status="all", customer_id=None, payment_method=None, source_type="all", search=None, date_from=None,
             date_to=None, skip=0, limit=50, sort_by="created_at", sort_order="desc")
    a.update(kw)
    keluar = asyncio.run(RP.list_receive_payments(_Req(), **a))
    return k, keluar


def _hash(sql):
    return hashlib.sha256(" ".join(sql.split()).encode()).hexdigest()[:16]


@pytest.mark.parametrize("nama", sorted(PANGGILAN))
def test_sql_panggilan_lama_identik_dengan_sebelum_u8(monkeypatch, nama):
    k, keluar = _jalan(monkeypatch, **PANGGILAN[nama])
    got = {j: _hash(q) for j, q in k.sql if j in ("count", "main")}
    assert got == HASIL_LAMA(nama), f"SQL panggilan lama {nama!r} BERUBAH: {got}"
    assert isinstance(keluar, dict) and set(keluar) == {"items", "total", "has_more"}


def HASIL_LAMA(nama):
    return HASH_LAMA[nama]


def test_pemanggil_langsung_tanpa_argumen_tak_mengaktifkan_voided(monkeypatch):
    """Tanpa include_voided, argumen bawaan = objek Query() (truthy): HARUS tak mengaktifkan."""
    import inspect
    k = _Konn()

    class _P:
        def acquire(self_):
            class _A:
                async def __aenter__(s):
                    return k

                async def __aexit__(s, *a):
                    return False
            return _A()

    async def gp():
        return _P()
    monkeypatch.setattr(RP, "get_pool", gp)
    sig = inspect.signature(RP.list_receive_payments)
    a = dict(status="all", customer_id=None, payment_method=None, source_type="all", search=None, date_from=None,
             date_to=None, skip=0, limit=50, sort_by="created_at", sort_order="desc",
             include_voided=sig.parameters["include_voided"].default)  # = objek Query() bawaan, BUKAN False
    assert a["include_voided"] is not True and bool(a["include_voided"]) is True  # kontrol: memang truthy
    asyncio.run(RP.list_receive_payments(_Req(), **a))
    assert not any("voided_items" in q for _, q in k.sql)


def _baris_voided():
    t = datetime(2026, 10, 3, 5, 0, tzinfo=timezone.utc)
    return {"id": "11111111-1111-1111-1111-111111111111", "payment_number": "RCV-2026-0001", "customer_id": "c1",
            "customer_name": "X", "payment_date": date(2026, 10, 1), "payment_method": "bank_transfer", "source_type": "cash",
            "total_amount": 10000, "allocated_amount": 10000, "unapplied_amount": 0, "status": "voided", "invoice_count": 1,
            "created_at": t, "settlement_type": "RECEIVE_PAYMENT", "source_document_type": None, "source_document_id": None,
            "source_document_number": None, "voided_at": t, "void_reason": "salah nominal"}


@pytest.mark.parametrize("kw", [{"status": "voided"}, {"include_voided": True}, {"status": "all", "include_voided": True, "source_type": "cash"}])
def test_voided_diminta_sql_memuat_cte_bertenant_dan_respons_membawa_void(monkeypatch, kw):
    k, keluar = _jalan(monkeypatch, baris=[_baris_voided()], **kw)
    sql = " ".join(" ".join(q.split()) for j, q in k.sql if j in ("count", "main"))
    assert sql.count("voided_items AS") == 2 and sql.count("SELECT * FROM voided_items") == 2  # daftar + hitung
    assert "rp.tenant_id = $1 AND rp.status = 'voided'" in sql
    assert isinstance(keluar, JSONResponse)
    import json
    d = json.loads(keluar.body)
    it = d["items"][0]
    assert it["status"] == "voided" and it["void_reason"] == "salah nominal" and it["voided_at"].startswith("2026-10-03")
    assert it["total_amount"] == 10000.0 and isinstance(it["total_amount"], float)  # koersi sama dgn response_model
    assert set(d) == {"items", "total", "has_more"} and d["total"] == 1


def test_voided_di_filter_status_posted_tak_membawa_baris_voided_ke_hasil(monkeypatch):
    """include_voided + status=posted: CTE ada, tetapi filter q.status = $n tetap menyaring (bukan membocorkan voided)."""
    k, _ = _jalan(monkeypatch, status="posted", include_voided=True)
    main = next(q for j, q in k.sql if j == "main")
    assert "q.status = $2" in " ".join(main.split())


def test_hash_lama_bisa_merah_kontrol():
    """Kontrol alat: hash berubah bila SQL berubah satu karakter."""
    assert _hash("SELECT 1") != _hash("SELECT 2") and _hash("a  b") == _hash("a b")
