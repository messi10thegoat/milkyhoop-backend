"""Proforma PDF (26 Sep 2026): tanda DRAF untuk proforma belum terbit + format=url|inline.

Terukur (ekstraksi teks PDF, fitz; kontrol: nomor dokumen terbaca): draf PRO-2609-0028 dulu TANPA tanda (tampil
seperti tagihan sah bila terkirim ke pelanggan), batal sudah DIBATALKAN (1f317429). Kini draf bertanda DRAF,
terbit bersih, batal tetap DIBATALKAN. format=url = path gateway (pola Unit 2 faktur); bawaan tetap inline.
"""
import inspect
import uuid
from types import SimpleNamespace

import pytest

from app.services import pdf_service as PS


@pytest.fixture
def tangkap(monkeypatch):
    html = {}

    class _HTML:
        def __init__(self, string=None, **kw):
            html["isi"] = string

        def write_pdf(self, **kw):
            return b"%PDF"
    monkeypatch.setattr(PS, "HTML", _HTML)
    return html


def _proforma(status, **x):
    d = {"proforma_number": "PRO-2609-0028", "proforma_date": "2026-09-26", "due_date": "2026-10-03", "status": status,
         "amount": 1000000, "paid_amount": 0, "sales_order_number": "SO-2609-0001", "customer_name": "Rahayu",
         "purpose": "dp", "items": []}
    d.update(x)
    return d


@pytest.mark.parametrize("status,draf,batal", [("draft", True, False), ("issued", False, False), ("cancelled", False, True)])
def test_tanda_sesuai_status(tangkap, status, draf, batal):
    PS.get_pdf_service().generate_proforma_pdf(_proforma(status, cancelled_at="2026-09-20T03:00:00Z"), {"name": "Grapgrap"})
    isi = tangkap["isi"]
    assert ("Belum diterbitkan" in isi) is draf
    assert (">DRAF<" in isi) is draf
    assert ("DIBATALKAN" in isi) is batal


def test_template_memuat_partial_draf():
    src = (PS.TEMPLATE_DIR / "proforma.html").read_text()
    assert '{% include "_partials/tanda_draf.html" %}' in src
    assert "{% if draf %}" in (PS.TEMPLATE_DIR / "_partials" / "tanda_draf.html").read_text()


class _Acq:
    def __init__(self, c):
        self.c = c

    async def __aenter__(self):
        return self.c

    async def __aexit__(self, *a):
        return False


class Conn:
    async def fetchrow(self, sql, *a):
        return {"proforma_number": "PRO-2609-0028", "id": a[0]}


@pytest.mark.asyncio
async def test_format_url_path_gateway(monkeypatch):
    from app.routers import proformas as PR
    pid = uuid.uuid4()

    async def _pool():
        return SimpleNamespace(acquire=lambda: _Acq(Conn()))
    monkeypatch.setattr(PR, "get_pool", _pool)
    req = SimpleNamespace(state=SimpleNamespace(user={"tenant_id": "grapgrap-manado", "user_id": "22222222-2222-2222-2222-222222222222"}))
    r = await PR.get_proforma_pdf(req, str(pid), format="url")
    assert r["data"]["url"] == f"/api/proformas/{pid}/pdf?format=inline"
    assert r["data"]["filename"] == "PRO-2609-0028.pdf" and r["data"]["expires_at"] is None


def test_bawaan_tetap_inline():
    from app.routers import proformas as PR
    assert inspect.signature(PR.get_proforma_pdf).parameters["format"].default.default == "inline"
