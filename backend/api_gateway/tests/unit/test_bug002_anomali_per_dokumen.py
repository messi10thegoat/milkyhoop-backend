"""BUG-002 (QA Cowork 25 Sep) -- GET /api/anomalies: SATU kartu per dokumen + qty bersatuan.

Kasus nyata: SO 001-09-26 grapgrap (Rahayu Umar, 63 pcs, completed, 0 faktur) tampil DUA kartu --
so_invoiced_mismatch "63 tercatat sudah difakturkan, tetapi faktur yang ada hanya 0." dan
so_closed_uninvoiced "berstatus selesai, tetapi tidak ada satu pun faktur untuknya". Akar satu.

Handler list_anomalies dipanggil UTUH di atas DB palsu (kueri dijawab per pemeriksaan) --
fungsi gabung benar + handler tak memanggilnya = merah di sini.
"""
import datetime as dt
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.routers import anomalies as A

TENANT = "tenant-uji"
SO1 = "11111111-1111-1111-1111-111111111111"
SO2 = "22222222-2222-2222-2222-222222222222"


def _mismatch(so_id, num, unit="pcs", q=63):
    return {
        "id": so_id, "order_number": num, "customer_name": "Rahayu Umar", "status": "completed",
        "age_days": 9, "description": "Kaos Pendek + Sablon", "quantity": Decimal(q),
        "quantity_invoiced": Decimal(q), "unit": unit, "linked_q": Decimal(0),
        "unit_price": Decimal(100000), "discount_percent": Decimal(0),
    }


def _closed(so_id, num):
    return {"id": so_id, "order_number": num, "customer_name": "Rahayu Umar", "status": "completed",
            "total_amount": Decimal(6300000), "age_days": 9}


class Conn:
    def __init__(self, mismatch=(), closed=()):
        self.mismatch, self.closed = list(mismatch), list(closed)

    async def fetch(self, sql, *a):
        s = " ".join(sql.split())
        if s.startswith("WITH linked AS"):
            return self.mismatch
        if "so.status IN ('completed', 'closed')" in s:
            return self.closed
        if "FROM customer_deposits d" in s:
            return []
        if s.startswith("SELECT 'sales_invoice' AS dt"):
            return []
        raise AssertionError(f"fetch tak dikenal: {s[:60]}")


@pytest.fixture
def jalankan(monkeypatch):
    async def _j(conn):
        class Pool:
            def acquire(self):
                class C:
                    async def __aenter__(self):
                        return conn

                    async def __aexit__(self, *e):
                        return False
                return C()

        class PE:
            async def get_user_context(self, *a):
                return {}

            async def can(self, *a):
                return True

        async def _pool():
            return Pool()

        async def _tgl(c, t):
            return dt.date(2026, 9, 25)

        async def _zona(c, t):
            return SimpleNamespace(key="Asia/Jakarta")

        monkeypatch.setattr(A, "get_db_pool", _pool)
        monkeypatch.setattr(A, "get_policy_engine", lambda: PE())
        monkeypatch.setattr(A, "tanggal_dokumen", _tgl)
        monkeypatch.setattr(A, "zona_tenant", _zona)
        req = SimpleNamespace(state=SimpleNamespace(user={"tenant_id": TENANT, "user_id": "u"}))
        return (await A.list_anomalies(req))["data"]
    return _j


@pytest.mark.asyncio
async def test_so_dua_anomali_jadi_satu_kartu_memuat_keduanya(jalankan):
    d = await jalankan(Conn([_mismatch(SO1, "001-09-26")], [_closed(SO1, "001-09-26")]))
    assert len(d["findings"]) == 1
    f = d["findings"][0]
    assert f["document_id"] == SO1 and f["severity"] == "high"
    assert f["checks"] == ["so_invoiced_mismatch", "so_closed_uninvoiced"]
    assert [r["check"] for r in f["reasons"]] == f["checks"]
    assert f["message"].count("001-09-26") == 1
    assert "63 pcs tercatat sudah difakturkan" in f["message"]
    assert "berstatus selesai" in f["message"]
    assert f["message"].startswith("SO 001-09-26: ") and f["message"].endswith(".")
    assert "buat ulang fakturnya" in f["suggested_action"] and "batalkan SO" in f["suggested_action"]
    assert f["amount"] == 6300000.0
    # hitungan per pemeriksaan tetap jujur (masing-masing menemukan 1)
    assert {c["key"]: c["count"] for c in d["checks"]}["so_closed_uninvoiced"] == 1
    assert {c["key"]: c["count"] for c in d["checks"]}["so_invoiced_mismatch"] == 1


@pytest.mark.asyncio
async def test_dua_so_berbeda_tetap_dua_kartu(jalankan):
    d = await jalankan(Conn([_mismatch(SO1, "001-09-26")], [_closed(SO2, "002-09-26")]))
    assert [f["document_id"] for f in d["findings"]] == [SO1, SO2]
    assert all(len(f["reasons"]) == 1 for f in d["findings"])
    assert d["findings"][1]["message"].startswith("SO 002-09-26 berstatus selesai")


@pytest.mark.asyncio
async def test_temuan_tunggal_pesan_tak_berubah_selain_satuan(jalankan):
    d = await jalankan(Conn([_mismatch(SO1, "001-09-26")]))
    assert d["findings"][0]["message"] == (
        "SO 001-09-26: 63 pcs tercatat sudah difakturkan, tetapi faktur yang ada hanya 0 pcs."
    )


@pytest.mark.asyncio
async def test_satuan_kosong_tanpa_tebakan(jalankan):
    d = await jalankan(Conn([_mismatch(SO1, "001-09-26", unit="  ")]))
    assert d["findings"][0]["message"] == (
        "SO 001-09-26: 63 tercatat sudah difakturkan, tetapi faktur yang ada hanya 0."
    )
