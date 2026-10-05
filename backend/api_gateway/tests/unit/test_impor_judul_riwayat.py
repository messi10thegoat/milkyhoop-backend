"""Pemilik 3 Okt: SETIAP perubahan judul order tampil di riwayat SO "dari -> ke" + aktor -- termasuk lewat IMPOR
(dulu impor menyetel order_title diam-diam). Kode lewat impor sudah tercatat ORDER_CODE_IMPORTED."""
import json
import uuid

import pytest

from app.services import kode_order as KO

T, SID = "t-uji", uuid.uuid4()


class _Conn:
    def __init__(self, kode_lama, judul_lama):
        self.kode, self.judul, self.audit = kode_lama, judul_lama, []

    async def fetchrow(self, q, *a):
        if "order_code_settings" in q:
            return {"enabled": True, "template": "{SEQ}-{MM}-{YY}", "min_digits": 3, "reset": "monthly",
                    "trigger": "so_confirmed", "allow_override": True, "label": "Kode order", "title_label": "Judul order"}
        if q.strip().startswith("SELECT id, order_number, order_code FROM sales_orders"):
            return {"id": SID, "order_number": "SO-1", "order_code": self.kode}
        if "SELECT order_code, order_title FROM sales_orders" in " ".join(q.split()):
            return {"order_code": self.kode, "order_title": self.judul}
        raise AssertionError(q)

    async def fetchval(self, q, *a):
        return None

    async def execute(self, q, *a):
        if "INSERT INTO audit_logs" in q:
            self.audit.append((a[1], json.loads(a[7])))
        return "UPDATE 1"


@pytest.mark.asyncio
async def test_impor_mengubah_judul_tercatat_dari_ke():
    c = _Conn(None, "LAMA")
    h = await KO.impor(c, T, [{"order_number": "SO-1", "order_code": "101-09-26", "order_title": "kemeja gmim"}], False, uuid.uuid4())
    assert h["applied"]
    judul = [m for e, m in c.audit if e == "ORDER_TITLE_CHANGED"]
    assert len(judul) == 1 and judul[0]["ringkas"] == "Judul order: LAMA → KEMEJA GMIM"
    assert judul[0]["old"] == "LAMA" and judul[0]["new"] == "KEMEJA GMIM" and judul[0]["user_id"]


@pytest.mark.asyncio
async def test_impor_judul_sama_atau_tanpa_judul_tak_mencatat():
    c = _Conn("101-09-26", "KEMEJA GMIM")
    await KO.impor(c, T, [{"order_number": "SO-1", "order_code": "101-09-26", "order_title": "Kemeja  gmim"}], False, uuid.uuid4())
    c2 = _Conn(None, None)
    await KO.impor(c2, T, [{"order_number": "SO-1", "order_code": "101-09-26"}], False, uuid.uuid4())
    assert [e for e, _ in c.audit] == [] and [e for e, _ in c2.audit] == ["ORDER_CODE_IMPORTED"]
