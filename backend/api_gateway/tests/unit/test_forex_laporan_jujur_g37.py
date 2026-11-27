"""G37: laporan /forex/gain-loss memberi catatan jujur (selisih kurs tak dicatat otomatis; kosong bukan berarti nihil)."""
import asyncio
from datetime import date
from types import SimpleNamespace

import app.routers.currencies as C


class Kon:
    async def fetch(self, *a):
        return []


class Pool:
    def acquire(self):
        class A:
            async def __aenter__(s):
                return Kon()

            async def __aexit__(s, *a):
                return False
        return A()


def test_laporan_kosong_membawa_catatan_jujur(monkeypatch):
    async def gp():
        return Pool()
    monkeypatch.setattr(C, "get_pool", gp)
    monkeypatch.setattr(C, "get_user_context", lambda r: {"tenant_id": "t"})
    out = asyncio.run(C.get_forex_gain_loss_report(SimpleNamespace(), start_date=date(2026, 11, 1), end_date=date(2026, 11, 27)))
    d = out.data
    assert d.items == [] and d.dicatat_otomatis is False
    assert "belum dicatat otomatis" in d.catatan_text and "Kosong bukan berarti" in d.catatan_text
