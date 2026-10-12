"""No. SPK / kode order = nomor INTERNAL produksi (12 Okt 2026, putusan pemilik; pola SAP Production Order / NetSuite Work
Order / Accurate Perintah Kerja): terbit saat produksi DILEPAS lewat "Terbitkan SPK" (idempoten) atau opsi di Terima DP;
tenant 'manual_only' (grapgrap, V417) tak terbit otomatis saat konfirmasi; kode manual tak pernah ditimpa; tak dicetak
di dokumen konsumen (lihat test_u7_label_kode_order.test_pdf_konsumen_tanpa_kode_order)."""
import asyncio
import ast
import os
import uuid
from datetime import date
from pathlib import Path

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

import pytest

from app.services import kode_order as KO

APP = Path(KO.__file__).resolve().parent.parent
SO = uuid.uuid4()


def _setelan(enabled=True, trigger="manual_only"):
    return {"enabled": enabled, "template": "{SEQ}-{MM}-{YY}", "min_digits": 3, "reset": "monthly", "trigger": trigger,
            "allow_override": True, "label": "No. SPK", "title_label": "Judul"}


class Konek:
    def __init__(self, so):
        self.so, self.log = so, []

    async def execute(self, sql, *a):
        self.log.append(" ".join(sql.split()) + " " + " ".join(map(str, a)))

    async def fetchrow(self, sql, *a):
        self.log.append(" ".join(sql.split()))
        assert "tenant_id = $2" in sql and a[1] == "t"
        return self.so


def _jalan(so, monkeypatch, setelan=None, terbit="022-10-26"):
    async def muat(conn, tid):
        return setelan or _setelan()
    dipanggil = []

    async def inti(conn, tid, so_id, tgl, s, sumber, aktor, meta):
        dipanggil.append((sumber, meta))
        return terbit
    monkeypatch.setattr(KO, "muat_setelan", muat)
    monkeypatch.setattr(KO, "_terbitkan_inti", inti)
    k = Konek(so)
    return asyncio.run(KO.terbitkan_spk(k, "t", SO, date(2026, 10, 12), "u")), dipanggil, k


def test_terbit_untuk_so_terkonfirmasi_tanpa_kode(monkeypatch):
    h, d, k = _jalan({"id": SO, "order_number": "SO-1", "status": "confirmed", "order_code": None}, monkeypatch)
    assert h == {"status": 200, "order_code": "022-10-26", "issued": True, "label": "No. SPK"}
    assert d == [("manual", {"trigger": "terbitkan_spk"})]
    assert any("ORDER_CODE_SO:t:" in s for s in k.log)


def test_idempoten_sudah_berkode_tidak_menerbitkan_lagi(monkeypatch):
    h, d, _ = _jalan({"id": SO, "order_number": "SO-1", "status": "invoiced", "order_code": "017-10-26"}, monkeypatch)
    assert h == {"status": 200, "order_code": "017-10-26", "issued": False, "label": "No. SPK"} and d == []


@pytest.mark.parametrize("status", ["draft", "cancelled", "completed"])
def test_status_tak_sah_409_berkode(monkeypatch, status):
    h, d, _ = _jalan({"id": SO, "order_number": "SO-1", "status": status, "order_code": None}, monkeypatch)
    assert h["status"] == 409 and h["code"] == "SPK_STATUS_TIDAK_SAH" and "No. SPK" in h["message"] and d == []


def test_so_tak_ada_404(monkeypatch):
    h, d, _ = _jalan(None, monkeypatch)
    assert h == {"status": 404} and d == []


def test_setelan_mati_galat(monkeypatch):
    with pytest.raises(KO.KodeOrderGalat):
        _jalan({"id": SO, "order_number": "SO-1", "status": "confirmed", "order_code": None}, monkeypatch,
               setelan=_setelan(enabled=False))


def test_manual_only_tak_terbit_otomatis_saat_konfirmasi_dan_kode_manual_tak_ditimpa():
    src = (APP / "services" / "kode_order.py").read_text()
    t = ast.get_source_segment(src, next(n for n in ast.walk(ast.parse(src))
                                         if isinstance(n, ast.AsyncFunctionDef) and n.name == "terbitkan"))
    assert 'if pemicu != "so_confirmed" or s["trigger"] != "so_confirmed":' in t      # manual_only -> no-op
    i = ast.get_source_segment(src, next(n for n in ast.walk(ast.parse(src))
                                         if isinstance(n, ast.AsyncFunctionDef) and n.name == "_terbitkan_inti"))
    assert 'if not so or so["order_code"]:' in i and "AND order_code IS NULL" in i   # kode (manual) tak pernah ditimpa


def test_rute_izin_inventaris_dan_opsi_uang_muka():
    mw = (APP / "middleware" / "permission_middleware.py").read_text()
    assert '(r"^/api/sales-orders/[^/]+/kode-order(/preview)?$", ["POST"], "sales_order", "U")' in mw
    assert mw.index("kode-order(/preview)") < mw.index('(r"^/api/sales-orders", ["GET"]')
    inv = (APP.parent / "tests" / "unit" / "inventaris_rute_tulis.txt").read_text()
    assert "POST /api/sales-orders/{order_id}/kode-order\n" in inv and "POST /api/sales-orders/{order_id}/kode-order/preview\n" in inv
    from app.schemas.customer_deposits import CreateCustomerDepositRequest
    assert CreateCustomerDepositRequest.model_fields["terbitkan_kode_order"].default is False
    cd = (APP / "routers" / "customer_deposits.py").read_text()
    assert "if body.terbitkan_kode_order:" in cd and "_KO.terbitkan_spk(conn, ctx[\"tenant_id\"], _guard_order_id," in cd
    assert cd.index("post_result = await _post_deposit(conn, ctx, dep_id)") < cd.index("if body.terbitkan_kode_order:")


def test_v417_hanya_grapgrap_manual_only():
    sql = (APP.parents[2] / "backend" / "migrations" / "V417__kode_order_manual_grapgrap.sql").read_text()
    assert "SET trigger = 'manual_only'" in sql and "WHERE tenant_id = 'grapgrap-manado'" in sql
    assert "tenant selain grapgrap ikut manual_only" in sql
