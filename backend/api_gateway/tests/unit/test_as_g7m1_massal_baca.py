"""AS-G7-M1 (24 Okt 2026, desain disetujui MASTER): pratinjau massal hapus SO / hapus penawaran / batal proforma = penentu BACA SAJA
(inti penulis tak dijalankan) dan transaksi luar pratinjau READ ONLY untuk aksi itu. Penulis (_tulis_*) tak berubah.
Perilaku nyata (LAMA == BARU, pratinjau tak menunggu kunci penulis, net 0) = harness scripts/ops/uji-nyata-as-g7m1."""
import ast
import inspect
import uuid

import pytest
from fastapi import HTTPException

from app.routers import bulk_proforma as BP
from app.routers import bulk_quote as BQ
from app.routers import bulk_so as BS
from app.routers import proformas as PF
from app.services import bulk


def _panggil(fn) -> set:
    t = ast.parse(inspect.getsource(fn).lstrip() if not inspect.getsource(fn).startswith(" ") else "if 1:\n" + inspect.getsource(fn))
    return {(c.func.attr if isinstance(c.func, ast.Attribute) else getattr(c.func, "id", None)) for c in ast.walk(t) if isinstance(c, ast.Call)}


def test_pratinjau_hapus_tanpa_inti_penulis():
    assert "hapus_so_draf_core" not in _panggil(BS._pratinjau_hapus) and "rencana_hapus_so" in _panggil(BS._pratinjau_hapus)
    assert "hapus_penawaran_core" not in _panggil(BQ._pratinjau_hapus) and "rencana_hapus_penawaran" in _panggil(BQ._pratinjau_hapus)
    # penulis tetap: penentu lalu inti
    assert "hapus_so_draf_core" in _panggil(BS._tulis_hapus) and "hapus_penawaran_core" in _panggil(BQ._tulis_hapus)


def test_pratinjau_batal_proforma_tanpa_kunci_dan_tanpa_inti():
    tulis, pratinjau = BP._fungsi("x")
    src = inspect.getsource(pratinjau)
    assert "batal_proforma_core" not in src and "kunci=False" in src
    assert "batal_proforma_core" in inspect.getsource(tulis) and "kunci=False" not in inspect.getsource(tulis)


def test_rute_pratinjau_baca_saja():
    assert "baca_saja=True" in inspect.getsource(BS._pasang)
    assert BQ.AKSI_BACA_SAJA == ("delete", "send") and "baca_saja=aksi in AKSI_BACA_SAJA" in inspect.getsource(BQ._pasang)
    assert "baca_saja=True" in inspect.getsource(BP.bulk_cancel_preview)


@pytest.mark.asyncio
async def test_baca_proforma_404_teks_sama_dengan_kunci_proforma():
    class C:
        async def fetchrow(self, q, *a):
            return None

        async def execute(self, q, *a):
            raise AssertionError("tak boleh mengunci")
    with pytest.raises(HTTPException) as a:
        await BP._baca_proforma(C(), {"tenant_id": "t"}, uuid.uuid4())
    with pytest.raises(HTTPException) as b:
        await PF._kunci_proforma(C(), {"tenant_id": "t"}, uuid.uuid4())
    assert (a.value.status_code, a.value.detail) == (b.value.status_code, b.value.detail) == (404, "Proforma tidak ditemukan.")


class _Tx:
    def __init__(self, log, kw):
        self.log, self.kw = log, kw

    async def start(self):
        self.log.append(("start", self.kw))

    async def rollback(self):
        self.log.append("rollback")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _Conn:
    def __init__(self):
        self.log = []

    def transaction(self, **kw):
        return _Tx(self.log, kw)


class _Pool:
    def __init__(self):
        self.conn = _Conn()

    def acquire(self):
        c = self.conn

        class A:
            async def __aenter__(s):
                return c

            async def __aexit__(s, *a):
                return False
        return A()


@pytest.mark.asyncio
@pytest.mark.parametrize("baca,harap", [(True, {"readonly": True}), (False, {})])
async def test_transaksi_luar_pratinjau_read_only_hanya_bila_diminta(baca, harap):
    p = _Pool()

    async def fn(conn, ctx, i):
        return [], {"ok": 1}
    r = await bulk.pratinjau_per_item(p, {"tenant_id": "t"}, "delete", [uuid.uuid4()], fn, baca_saja=baca)
    assert p.conn.log[0] == ("start", harap) and p.conn.log[-1] == "rollback" and r["ok_count"] == 1
