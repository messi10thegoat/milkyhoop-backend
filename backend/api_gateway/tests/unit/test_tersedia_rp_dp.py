"""`tersedia` pada lampiran Penerimaan Pembayaran + Uang Muka Pelanggan (25 Sep 2026).

Terukur 25 Sep: 30 baris documents storage_type='local' berkasnya musnah (recreate 23 Sep, /tmp
kontainer). Beban/faktur/hub sudah memberi `tersedia` (s3 saja); penerimaan & DP belum -> kaos
RCV-2026-0003 tampil normal lalu unduhan 404. Aturan satu: utils.lampiran_unduh.lampiran_tersedia.
"""
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.utils import lampiran_unduh as LU

APP = Path(LU.__file__).resolve().parents[1]
TENANT = "kaos-biru-konveksi"


@pytest.mark.parametrize("st,harap", [("s3", True), ("S3", True), ("local", False), (None, False), ("", False)])
def test_lampiran_tersedia(st, harap):
    assert LU.lampiran_tersedia(st) is harap


def test_aturan_sama_dengan_beban_dan_faktur():
    from app.routers import expenses as E
    from app.routers import sales_invoices as SI

    # nilai yang ADA di DB (diukur 25 Sep: hanya 's3' dan 'local', huruf kecil). Beban membandingkan
    # peka-huruf ('S3' -> False) sedangkan faktur/helper ini tidak -- beda hanya di luar data nyata.
    for st in ("s3", "local", None, ""):
        assert LU.lampiran_tersedia(st) == E._exp_lampiran_tersedia(st) == SI._si_lampiran_tersedia(st)


def _kolom_select(q):
    """Nama kolom keluaran SELECT (alias atau bagian sesudah titik) -- tiruan asyncpg Record."""
    daftar = q.split("SELECT", 1)[1].split("FROM", 1)[0]
    out = []
    for bagian in re.split(r",(?![^(]*\))", daftar):
        b = bagian.strip()
        m = re.search(r"\bAS\s+(\w+)\s*$", b, re.I)
        out.append(m.group(1) if m else b.split(".")[-1].strip())
    return out


def test_pengurai_kolom_bisa_merah():
    assert _kolom_select("SELECT d.id, d.file_type AS mime_type, COALESCE(u.a, u.b) AS n FROM x") == \
        ["id", "mime_type", "n"]


_NILAI = {"id": None, "file_name": "a.png", "file_path": "k/a.png", "file_size": 10, "mime_type": "image/png",
          "file_type": "image/png", "thumbnail_url": None, "uploaded_at": datetime(2026, 9, 2, tzinfo=timezone.utc),
          "attachment_type": "receipt", "display_order": 0, "uploaded_by": None, "uploaded_by_name": "Anton"}


class _Conn:
    """fetch -> baris HANYA berisi kolom yang disebut SELECT (kolom hilang = KeyError, seperti asyncpg)."""

    def __init__(self, storage):
        self.storage = storage

    async def execute(self, q, *a):
        return "OK"

    async def fetch(self, q, *a):
        kol = _kolom_select(q)
        rows = []
        for st in self.storage:
            r = {k: _NILAI.get(k) for k in kol}
            r["id"] = uuid.uuid4()
            if "storage_type" in kol:
                r["storage_type"] = st
            rows.append(r)
        return rows

    def transaction(self):
        class _T:
            async def __aenter__(s):
                return None

            async def __aexit__(s, *e):
                return False
        return _T()


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        pool = self

        class _Ctx:
            async def __aenter__(self):
                return pool.conn

            async def __aexit__(self, *e):
                return False
        return _Ctx()


@pytest.mark.asyncio
async def test_daftar_lampiran_penerimaan_tersedia(monkeypatch):
    from app.routers import receive_payments as R

    c = _Conn(["s3", "local"])

    async def _pool():
        return _Pool(c)
    monkeypatch.setattr(R, "get_pool", _pool)
    monkeypatch.setattr(R, "get_user_context", lambda r: {"tenant_id": TENANT})
    res = await R.list_payment_attachments(request=None, payment_id=str(uuid.uuid4()))
    assert [x["tersedia"] for x in res["data"]] == [True, False]
    assert all(x["url"] for x in res["data"])          # url tetap ada (rute download menjawab 404 bersih)


def test_semua_kueri_lampiran_penerimaan_memilih_storage_type():
    """Tiga kueri yang memberi makan _rp_lampiran_ke_respons (daftar, detail, jalur jurnal-saja)."""
    src = (APP / "routers/receive_payments.py").read_text()
    blok = [m.group(0) for m in re.finditer(r"SELECT d\.id, d\.file_name.*?FROM document_attachments", src, re.S)]
    assert len(blok) == 3, len(blok)
    for b in blok:
        assert "d.storage_type" in b, b


def test_pembentuk_respons_penerimaan_membawa_tersedia():
    from app.routers import receive_payments as R

    rows = [{**_NILAI, "id": uuid.uuid4(), "storage_type": st} for st in ("local", "s3")]
    out = R._rp_lampiran_ke_respons(rows, uuid.uuid4())
    assert [x["tersedia"] for x in out] == [False, True]
    assert R._rp_lampiran_ke_respons(rows, None)[0]["url"] is None


@pytest.mark.asyncio
async def test_daftar_lampiran_uang_muka_tersedia(monkeypatch):
    from app.routers import customer_deposits as CD

    c = _Conn(["local", "s3", None])

    async def _pool():
        return _Pool(c)

    async def _muat(conn, deposit_id, tenant_id):
        return {"id": deposit_id}
    monkeypatch.setattr(CD, "get_pool", _pool)
    monkeypatch.setattr(CD, "get_user_context", lambda r: {"tenant_id": TENANT})
    monkeypatch.setattr(CD, "_dep_att_load_deposit", _muat)
    res = await CD.list_deposit_attachments(request=None, deposit_id=uuid.uuid4())
    assert [x["tersedia"] for x in res["attachments"]] == [False, True, False]
    assert all(x["url"] for x in res["attachments"])
