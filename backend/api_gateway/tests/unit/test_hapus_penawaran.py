"""Hapus penawaran pola Xero/NetSuite (3 Okt 2026, pemilik + MASTER): HANYA draf yang belum pernah keluar (sent_at
kosong, 0 tautan, tak dirujuk SO); selain itu 409 QUOTE_NOT_DELETABLE "Batalkan saja", nol tulis. Kunci QUOTE +
FOR UPDATE + set_config aktor + DELETE dalam SATU transaksi (audit DOCUMENT_DELETED = trigger)."""
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routers import quotes as Q

T, QID = "t-uji", str(uuid.uuid4())


class _Conn:
    def __init__(self, status="draft", sent_at=None, tautan=False, so=False):
        self.st, self.sent, self.tautan, self.so = status, sent_at, tautan, so
        self.dalam, self.tulis = 0, []

    def transaction(self):
        c = self

        class _T:
            async def __aenter__(s):
                c.dalam += 1

            async def __aexit__(s, *e):
                c.dalam -= 1
                return False
        return _T()

    async def execute(self, q, *a):
        self.tulis.append((" ".join(q.split())[:30], self.dalam > 0))

    async def fetchrow(self, q, *a):
        s = " ".join(q.split())
        assert "FOR UPDATE" in s and a[1] == T
        return {"id": uuid.UUID(QID), "status": self.st, "quote_number": "QUO-1", "sent_at": self.sent}

    async def fetchval(self, q, *a):
        assert a[0] == T, "tenant WAJIB"
        if "document_shares" in q:
            return 1 if self.tautan else None
        if "FROM sales_orders" in q:
            return 1 if self.so else None
        raise AssertionError(q)


@pytest.fixture
def pasang(monkeypatch):
    def _p(c):
        class _P:
            def acquire(self):
                class _A:
                    async def __aenter__(s):
                        return c

                    async def __aexit__(s, *a):
                        return False
                return _A()

        async def gp():
            return _P()
        monkeypatch.setattr(Q, "get_pool", gp)
        return c
    return _p


def _req():
    return SimpleNamespace(state=SimpleNamespace(user={"user_id": "0bccdb25-fdf0-4e99-9024-b9a20846f76c",
                                                       "tenant_id": T}), headers={})


@pytest.mark.asyncio
async def test_draf_bersih_terhapus_satu_transaksi_beraktor(pasang):
    c = pasang(_Conn())
    r = await Q.delete_quote(_req(), QID)
    assert r.data == {"quote_number": "QUO-1"}
    jenis = [s for s, _ in c.tulis]
    assert jenis[0].startswith("SELECT pg_advisory_xact_lock")
    assert any(s.startswith("SELECT set_config('app.user") for s in jenis)
    assert jenis[-1].startswith("DELETE FROM quotes")
    assert all(d for _, d in c.tulis), "kunci, aktor, DELETE di SATU transaksi"


@pytest.mark.asyncio
@pytest.mark.parametrize("kw,kata", [
    ({"status": "sent"}, "berstatus Terkirim"), ({"status": "void"}, "berstatus Batal"),
    ({"status": "converted"}, "berstatus Dikonversi"),
    ({"sent_at": datetime(2026, 10, 3, tzinfo=timezone.utc)}, "ditandai terkirim"),
    ({"tautan": True}, "dibagikan"), ({"so": True}, "dijadikan pesanan"),
])
async def test_selain_draf_bersih_409_nol_hapus(pasang, kw, kata):
    c = pasang(_Conn(**kw))
    with pytest.raises(HTTPException) as e:
        await Q.delete_quote(_req(), QID)
    assert e.value.status_code == 409 and e.value.detail["code"] == "QUOTE_NOT_DELETABLE"
    assert kata in e.value.detail["message"] and e.value.detail["message"].endswith("Batalkan saja.")
    assert not any(s.startswith("DELETE") for s, _ in c.tulis)
