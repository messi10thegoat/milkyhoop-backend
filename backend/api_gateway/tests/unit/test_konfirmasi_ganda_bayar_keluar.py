"""Agen chat create_bill_payment (22 Okt 2026, putusan pemilik: POST /bill-payments kunci HANYA klien): agen TIDAK mengirim
X-Idempotency-Key; yang mencegah pembayaran ganda saat konfirmasi ganda = klaim atomik pending_actions (UPDATE ... SET status =
'EXECUTING' WHERE status = 'PENDING' RETURNING id) di unified_chat._confirm_direct_action.

Dibuktikan dengan pool palsu yang MENIRU UPDATE bersyarat Postgres (klaim kedua -> NULL) dan httpx perekam: dua konfirmasi yang
SAMA-SAMA membaca 'PENDING' (balapan) -> TEPAT 1 POST /api/bill-payments. KONTROL: klaim yang selalu berhasil -> 2 POST (alat bisa
membedakan; klaim itulah pagarnya). Batas: tak menguji endpoint sungguhan (itu uji nyata E)."""
import json
import os
import sys
import uuid

import pytest

sys.path.insert(0, "/app/backend/api_gateway")
os.environ.setdefault("OPENAI_API_KEY", "uji-konfirmasi-ganda-bukan-kunci")

try:
    import httpx
    from app.routers import unified_chat as UC
except Exception:  # pragma: no cover
    UC = None
    httpx = None

pytestmark = pytest.mark.skipif(UC is None, reason="unified_chat tidak bisa diimpor")

PLAN = {"vendor_id": "11111111-1111-1111-1111-111111111111", "vendor_name": "TES Vendor", "payment_date": "2026-10-22",
        "bank_account_id": "22222222-2222-2222-2222-222222222222", "total_amount": 500, "payment_method": "bank_transfer",
        "allocations": [{"bill_id": "33333333-3333-3333-3333-333333333333", "amount_applied": 500}]}


class PoolKlaim:
    """fetchrow pending_actions selalu 'PENDING' (dua konfirmasi membaca sebelum salah satu mengklaim); fetchval klaim EXECUTING
    meniru UPDATE bersyarat: hanya yang pertama mendapat id (atau selalu, untuk KONTROL)."""

    def __init__(self, selalu_menang=False):
        self.diklaim = set()
        self.selalu_menang = selalu_menang

    async def fetchrow(self, sql, *args):
        if "FROM pending_actions" in sql:
            return {"action_id": "create_bill_payment", "action_plan": json.dumps(PLAN), "status": "PENDING", "expires_at": None}
        return None

    async def fetchval(self, sql, *args):
        if "SET status = 'EXECUTING'" in sql:
            pid = args[0]
            if pid in self.diklaim and not self.selalu_menang:
                return None
            self.diklaim.add(pid)
            return pid
        return uuid.uuid4()

    async def fetch(self, sql, *args):
        return []

    async def execute(self, sql, *args):
        return "UPDATE 1"


class Req:
    headers = {"authorization": "Bearer uji-konfirmasi-ganda"}


class Perekam:
    rekaman = []

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def request(self, method=None, url=None, json=None, headers=None, **k):
        Perekam.rekaman.append((method, url, headers or {}))
        return Balasan()

    async def post(self, url, **k):
        Perekam.rekaman.append(("POST", url, k.get("headers") or {}))
        return Balasan()

    async def get(self, url, **k):
        return Balasan()


class Balasan:
    status_code = 201
    text = "{}"

    def json(self):
        return {"success": True, "data": {"id": "00000000-0000-0000-0000-0000000000bb", "payment_number": "BP-TES"}}


async def _dua_konfirmasi(monkeypatch, pool):
    Perekam.rekaman = []
    monkeypatch.setattr(httpx, "AsyncClient", Perekam)
    pid = str(uuid.uuid4())
    for _ in range(2):
        try:
            await UC._confirm_direct_action(pending_action_id=pid, tenant_id="t-uji", user_id="u-uji", pool=pool, http_request=Req(),
                                            session_id=None, doc_status="POSTED")
        except Exception:
            pass  # pasca-POST (rekap/riwayat) boleh gagal di pool palsu; yang dinilai = jumlah POST bayar
    return [r for r in Perekam.rekaman if r[0] == "POST" and r[1].endswith("/api/bill-payments")]


@pytest.mark.asyncio
async def test_konfirmasi_ganda_tepat_satu_pembayaran(monkeypatch):
    post = await _dua_konfirmasi(monkeypatch, PoolKlaim())
    assert len(post) == 1, post
    # pagar UTAMA tetap klaim atomik (kontrol di bawah: tanpa klaim -> 2 POST). AS-G12 (23 Okt 2026): agen kini JUGA mengirim
    # X-Idempotency-Key = agen:{pending_action_id} -> lapis kedua (mis. ulang HTTP dalam satu eksekusi = replay, bukan bayar ganda).
    assert all(h.get("X-Idempotency-Key", "").startswith("agen:") for _, _, h in post), post


@pytest.mark.asyncio
async def test_kontrol_tanpa_klaim_dua_pembayaran(monkeypatch):
    assert len(await _dua_konfirmasi(monkeypatch, PoolKlaim(selalu_menang=True))) == 2
