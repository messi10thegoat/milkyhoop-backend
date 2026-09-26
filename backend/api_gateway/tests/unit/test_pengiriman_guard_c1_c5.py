"""Pengiriman faktur (26 Sep 2026): guard status + C1 (baris dipagari faktur) + C5 (idempotensi terikat faktur).

- Faktur ber-DP/pelunasan (partial/paid) dulu TAK BISA dikirim (guard status == 'posted'); fixdp/V309 membuat
  faktur ber-DP 'partial' -> kirim terblokir. Kini posted/partial/paid (pembayaran tak menghapus kewajiban serah).
- C1: SELECT ... FOR UPDATE & UPDATE sales_invoice_items dulu hanya WHERE id -> baris faktur B bisa diubah saat
  jurnal dibukukan ke faktur A. Kini WHERE id AND invoice_id; salah milik -> 404, transaksi rollback.
- C5: kunci idempotensi dicari per (tenant, key) saja & SESUDAH guard status -> kunci dipakai ulang di faktur lain
  mengembalikan "sudah dikirim" palsu; replay sesudah terkirim penuh jadi 400. Kini terikat invoice_id + dicek dulu.
Probe scratch dua sisi: INV-2609-0005 (partial) kode baru lolos guard -> 409 stok (sah), lama 400; baris milik
INV-2609-0005 dikirim lewat INV-2609-0047 (posted): baru 404 nol tulisan, lama maju sampai cek stok.
"""
import inspect
import uuid
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routers import sales_invoices as SI

T = "kaos-biru-konveksi"
INV = uuid.uuid4()
WH = uuid.uuid4()
ITEM = uuid.uuid4()


class _Tx:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *a):
        return False


class Conn:
    def __init__(self, status="partial", fstatus="pending", existing=None):
        self.status, self.fstatus, self.existing, self.sql = status, fstatus, existing, []

    def transaction(self):
        return _Tx()

    async def execute(self, sql, *a):
        self.sql.append(sql)

    async def fetchrow(self, sql, *a):
        self.sql.append(sql)
        if "FROM sales_invoices" in sql:
            return {"id": INV, "invoice_number": "INV-2609-0005", "customer_name": "X", "total_amount": 1, "invoice_date": date(2026, 9, 1),
                    "status": self.status, "fulfillment_status": self.fstatus, "warehouse_id": None}
        if "FROM invoice_fulfillments" in sql:
            return self.existing
        return None


@pytest.fixture
def pasang(monkeypatch):
    dipanggil = []

    class _Acq:
        def __init__(self, c):
            self.c = c

        async def __aenter__(self):
            return self.c

        async def __aexit__(self, *a):
            return False

    def _set(conn):
        async def _pool():
            return SimpleNamespace(acquire=lambda: _Acq(conn))
        monkeypatch.setattr(SI, "get_pool", _pool)

    async def _eksekusi(*a, **k):
        dipanggil.append(k.get("idempotency_key"))
        return {"fulfillment_id": "baru"}

    async def _periode(*a, **k):
        return None

    async def _tgl(c, t):
        return date(2026, 9, 26)

    monkeypatch.setattr(SI, "_execute_fulfillment", _eksekusi)
    monkeypatch.setattr(SI, "check_period_is_open", _periode)
    monkeypatch.setattr(SI, "tanggal_dokumen", _tgl)
    return SimpleNamespace(set=_set, dipanggil=dipanggil)


class Req:
    def __init__(self, body):
        self._b = body
        self.state = SimpleNamespace(user={"tenant_id": T, "user_id": "22222222-2222-2222-2222-222222222222"})

    async def json(self):
        return self._b


def _body(key=None):
    b = {"warehouse_id": str(WH), "items": [{"invoice_item_id": str(ITEM), "quantity": 1}], "fulfillment_date": "2026-09-26"}
    if key:
        b["idempotency_key"] = key
    return b


@pytest.mark.asyncio
@pytest.mark.parametrize("st", ["posted", "partial", "paid"])
async def test_faktur_terbit_termasuk_berbayar_bisa_dikirim(pasang, st):
    pasang.set(Conn(status=st))
    r = await SI.fulfill_invoice(Req(_body()), INV)
    assert r["data"] == {"fulfillment_id": "baru"} and len(pasang.dipanggil) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("st", ["draft", "void"])
async def test_draf_void_ditolak(pasang, st):
    pasang.set(Conn(status=st))
    with pytest.raises(HTTPException) as e:
        await SI.fulfill_invoice(Req(_body()), INV)
    assert e.value.status_code == 400 and not pasang.dipanggil


def _hash_body():
    from hashlib import sha256
    return sha256(SI.canonical_json({"warehouse_id": str(WH), "fulfillment_date": "2026-09-26", "recognize_revenue": True,
                                     "items": [{"invoice_item_id": str(ITEM), "qty": "1"}]}).encode()).hexdigest()


@pytest.mark.asyncio
async def test_c5_kunci_faktur_lain_409(pasang):
    pasang.set(Conn(existing={"id": uuid.uuid4(), "payload_hash": _hash_body(), "status": "posted", "invoice_id": uuid.uuid4()}))
    with pytest.raises(HTTPException) as e:
        await SI.fulfill_invoice(Req(_body("k1")), INV)
    assert e.value.status_code == 409 and "faktur lain" in e.value.detail and not pasang.dipanggil


@pytest.mark.asyncio
async def test_c5_replay_sesudah_terkirim_penuh_bukan_400(pasang):
    fid = uuid.uuid4()
    pasang.set(Conn(fstatus="fulfilled", existing={"id": fid, "payload_hash": _hash_body(), "status": "posted", "invoice_id": INV}))
    r = await SI.fulfill_invoice(Req(_body("k1")), INV)
    assert r["data"]["fulfillment_id"] == str(fid) and not pasang.dipanggil


def test_c1_baris_dipagari_faktur():
    s = " ".join(inspect.getsource(SI._execute_fulfillment).split())
    assert "FROM sales_invoice_items WHERE id=$1 AND invoice_id=$2 FOR UPDATE" in s
    assert "WHERE id = $1 AND invoice_id = $4" in s
    assert "WHERE id=$1 FOR UPDATE" not in s
