"""DP-POSISI (2 Nov 2026; disetujui MASTER, cermin pratinjau UMP): before/after pratinjau aksi DP membawa position_text =
teks status daftar/detail DP (uang_muka_daftar.status_text_um atas status_detail_dp) -- SATU penentu; after DIHITUNG dari
keadaan SESUDAH (mis. terapkan sebagian -> teks 'partial'), bukan disalin dari before."""
import asyncio
import uuid
from decimal import Decimal

from app.routers import customer_deposits as CD
from app.services import uang_muka_rencana as UMR
from app.services.status_uang_muka import status_detail_dp
from app.services.uang_muka_daftar import status_text_um

T = "tenant-uji"


def _j(c):
    return asyncio.run(c)


class Conn:
    def __init__(self, status, amount, dipakai, kembali, apl=Decimal("0"), ref=Decimal("0")):
        self.r = {"deposit_number": "DEP-1", "status": status, "amount": amount, "amount_applied": dipakai, "amount_refunded": kembali}
        self.apl, self.ref = apl, ref

    async def fetchrow(self, sql, *a):
        return self.r

    async def fetchval(self, sql, *a):
        return self.ref if "customer_deposit_refunds" in sql else self.apl


def _sisa(monkeypatch, nilai):
    async def f(conn, tid, did):
        return Decimal(nilai)
    monkeypatch.setattr(CD, "compute_deposit_remaining", f)


def test_sebelum_position_text_sama_dengan_daftar(monkeypatch):
    _sisa(monkeypatch, "600")
    k = _j(CD._keadaan_dp(Conn("partial", Decimal("1000"), Decimal("400"), Decimal("0")), T, uuid.uuid4()))
    assert k["position_text"] == status_text_um(status_detail_dp("partial", Decimal("400"), Decimal("0")))
    assert k["position_text"] and k["status_detail"] == status_detail_dp("partial", Decimal("400"), Decimal("0"))


def test_sesudah_terapkan_sebagian_dan_penuh_dihitung_ulang(monkeypatch):
    _sisa(monkeypatch, "1000")
    c = Conn("posted", Decimal("1000"), Decimal("0"), Decimal("0"))
    sebelum = _j(CD._keadaan_dp(c, T, uuid.uuid4()))
    sebagian = _j(UMR.keadaan_sesudah(c, T, uuid.uuid4(), "apply", {"total": Decimal("400")}))
    penuh = _j(UMR.keadaan_sesudah(c, T, uuid.uuid4(), "apply", {"total": Decimal("1000")}))
    assert sebagian["status"] == "partial" and sebagian["position_text"] == status_text_um(status_detail_dp("partial", Decimal("400"), Decimal("0")))
    assert penuh["status"] == "applied" and penuh["position_text"] == status_text_um(status_detail_dp("applied", Decimal("1000"), Decimal("0")))
    assert len({sebelum["position_text"], sebagian["position_text"], penuh["position_text"]}) == 3  # tak disalin dari before
