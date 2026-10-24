"""AS-G7-M2 (24 Okt 2026, desain disetujui MASTER): penentu kirim penawaran (quotes.rencana_kirim_penawaran) dan penentu bagikan dokumen
(dokumen.rencana_bagikan) dipakai PENULIS (kunci=True: kunci QUOTE + FOR UPDATE di tempat yang sama) dan PRATINJAU (kunci=False, transaksi
READ ONLY): /documents/{kind}/{id}/share/preview tunggal, bulk share, bulk send. Perilaku nyata = harness scripts/ops/uji-nyata-as-g7m2."""
import ast
import inspect
import re
import uuid

PENULIS_BAGIKAN = re.compile(r"(?<![A-Za-z_])_bagikan\(conn")  # 'rencana_bagikan(conn' bukan panggilan penulis
from datetime import date, timedelta

import pytest
from fastapi import HTTPException

from app.routers import bulk_dokumen as BD
from app.routers import bulk_quote as BQ
from app.routers import dokumen as D
from app.routers import quotes as Q

KUNCI = re.compile(r"FOR UPDATE|pg_advisory", re.I)


def _panggil(fn) -> set:
    src = inspect.getsource(fn)
    t = ast.parse(src if not src.startswith(" ") else "if 1:\n" + src)
    return {(c.func.attr if isinstance(c.func, ast.Attribute) else getattr(c.func, "id", None)) for c in ast.walk(t) if isinstance(c, ast.Call)}


def test_penulis_kirim_memakai_penentu_berkunci_lalu_tandai():
    src = inspect.getsource(Q.kirim_penawaran_core)
    i_r = src.index("rencana_kirim_penawaran(conn, ctx, quote_id, kunci=True, send_email=send_email)")
    assert i_r < src.index("tandai_terkirim(") and "pg_advisory" not in src  # kunci QUOTE kini di penentu (kunci=True)
    assert 'await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"QUOTE:' in inspect.getsource(Q.rencana_kirim_penawaran)


def test_penulis_bagikan_memakai_penentu_berkunci_lalu_tautan():
    src = inspect.getsource(D._bagikan)
    assert src.index("rencana_bagikan(conn, request, ctx, kind, uid, kunci=True)") < src.index("buat_tautan(")
    assert 'if q is not None and info["akan_ditandai_terkirim"]' in src


def test_pratinjau_tanpa_penulis_dan_read_only():
    p = inspect.getsource(D.pratinjau_bagikan_dokumen)
    assert "conn.transaction(readonly=True)" in p and "kunci=False" in p and not PENULIS_BAGIKAN.search(p)
    # kontrol: pola menangkap panggilan penulis, tak tertipu nama penentu
    assert PENULIS_BAGIKAN.search("_, info = await _bagikan(conn, request, ctx, kind, uid, channel)")
    assert not PENULIS_BAGIKAN.search("info, _ = await rencana_bagikan(conn, request, ctx, kind, uid, kunci=False)")
    assert not hasattr(D, "_BatalkanBagikan")
    _, pv = BD._fungsi_bagikan(None, "invoice", "link")
    assert "_bagikan" not in _panggil(pv) and "rencana_bagikan" in _panggil(pv)
    assert "baca_saja=True" in inspect.getsource(BD.buat_router)
    assert "kirim_penawaran_core" not in _panggil(BQ._pratinjau_kirim) and "rencana=True" in inspect.getsource(BQ._pratinjau_kirim)
    assert "Q.kirim_penawaran_core(" in inspect.getsource(BQ._kirim)  # penulis massal tetap inti tunggal
    assert BQ.AKSI_BACA_SAJA == ("delete", "send")


class _Baca:
    def __init__(self, q):
        self.q, self.sql = q, []

    async def fetchrow(self, sql, *a):
        assert not KUNCI.search(sql), sql
        self.sql.append(sql)
        return self.q

    async def fetchval(self, sql, *a):
        assert not KUNCI.search(sql), sql
        return None

    async def execute(self, sql, *a):
        assert not KUNCI.search(sql), f"mengunci: {sql[:60]}"


@pytest.fixture
def hari(monkeypatch):
    async def tgl(conn, tid):
        return date(2026, 10, 24)
    monkeypatch.setattr(Q, "tanggal_dokumen", tgl)
    import app.utils.tanggal_tenant as TT
    monkeypatch.setattr(TT, "tanggal_dokumen", tgl)


CTX = {"tenant_id": "t", "user_id": str(uuid.uuid4())}


def _q(status, lewat=False):
    return {"id": uuid.uuid4(), "status": status, "quote_number": "QUO-1",
            "expiry_date": date(2026, 10, 24) + timedelta(days=-1 if lewat else 10)}


@pytest.mark.asyncio
async def test_rencana_kirim_tanpa_kunci_dan_galat_sama(hari):
    assert (await Q.rencana_kirim_penawaran(_Baca(_q("draft")), CTX, uuid.uuid4(), kunci=False))["quote_number"] == "QUO-1"
    for q, kode in ((None, 404), (_q("accepted"), 400), (_q("sent", lewat=True), 409)):
        with pytest.raises(HTTPException) as e:
            await Q.rencana_kirim_penawaran(_Baca(q), CTX, uuid.uuid4(), kunci=False)
        assert e.value.status_code == kode
    with pytest.raises(HTTPException) as e:
        await Q.rencana_kirim_penawaran(_Baca(_q("draft")), CTX, uuid.uuid4(), kunci=False, send_email=True)
    assert e.value.status_code == 422


@pytest.mark.asyncio
async def test_rencana_bagikan_penawaran_tanpa_kunci(hari):
    info, q = await D.rencana_bagikan(_Baca(_q("draft")), None, CTX, "quotation", uuid.uuid4(), kunci=False)
    assert q is not None and info["akan_ditandai_terkirim"] is True and info["boleh"] is True and info["number"] == "QUO-1"
    with pytest.raises(HTTPException) as e:
        await D.rencana_bagikan(_Baca(_q("sent", lewat=True)), None, CTX, "quotation", uuid.uuid4(), kunci=False)
    assert e.value.status_code == 409


@pytest.mark.asyncio
async def test_kontrol_merah_penjaga_tiruan_menangkap_kunci(hari):
    """Kontrol: jalur penulis (kunci=True) MEMANG mengunci -> tiruan baca-saja meledak (penjaga bisa gagal)."""
    with pytest.raises(AssertionError):
        await Q.rencana_kirim_penawaran(_Baca(_q("draft")), CTX, uuid.uuid4(), kunci=True)
    with pytest.raises(AssertionError):
        await D.rencana_bagikan(_Baca(_q("draft")), None, CTX, "quotation", uuid.uuid4(), kunci=True)
