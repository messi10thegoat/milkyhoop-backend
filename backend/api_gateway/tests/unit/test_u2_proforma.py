"""U2 Proforma BE (4 Okt 2026, MASTER GO): /summary, /{id}/history, /issue/preview, /cancel/preview, idempotensi
/issue + /cancel, batal dalam satu transaksi + mutex baris SO. Tanpa DB; perilaku nyata (ringkasan = daftar, paritas
pratinjau <=> tulis termasuk perpindahan atribusi uang muka, nol tulis, nol jurnal) = harness kaos rollback."""
import asyncio
import inspect
import re
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routers import proformas as PF

MW = Path(PF.__file__).parents[1] / "middleware" / "permission_middleware.py"


def test_rute_terpasang_dan_urutan():
    get = [r.path for r in PF.router.routes if "GET" in getattr(r, "methods", set())]
    assert get.index("/summary") < get.index("/{proforma_id}"), "summary DI ATAS /{proforma_id}"
    nama = {(r.path, tuple(sorted(r.methods))): r.endpoint.__name__ for r in PF.router.routes}
    assert nama[("/summary", ("GET",))] == "get_proforma_summary"
    assert nama[("/{proforma_id}/history", ("GET",))] == "get_proforma_history"
    assert nama[("/{proforma_id}/issue/preview", ("POST",))] == "preview_issue_proforma"
    assert nama[("/{proforma_id}/cancel/preview", ("POST",))] == "preview_cancel_proforma"
    assert nama[("/{proforma_id}/issue", ("POST",))] == "issue_proforma"
    assert nama[("/{proforma_id}/cancel", ("POST",))] == "cancel_proforma"


def test_izin_riwayat_terpetakan_baca():
    assert re.search(r'\(r"\^/api/proformas/\[\^/\]\+/history\$", \["GET"\], "proforma", "R"\)', MW.read_text())


def test_satu_penentu_satu_penulis():
    for tulis, pratinjau, rencana, penulis in (("issue_proforma", "_pratinjau", "_rencana_terbit", "_tulis_terbit"),
                                               ("cancel_proforma", "_pratinjau", "_rencana_batal", "_tulis_batal")):
        st, sp = inspect.getsource(getattr(PF, tulis)), inspect.getsource(getattr(PF, pratinjau))
        for f in (rencana, penulis):
            assert f + "(" in st and f + "(" in sp, (tulis, f)
    assert "UPDATE proformas SET status = 'issued'" not in inspect.getsource(PF.issue_proforma)
    assert "_BatalkanPratinjau" in inspect.getsource(PF._pratinjau), "pratinjau WAJIB rollback"


def test_batal_satu_transaksi_dan_mutex_so():
    src = inspect.getsource(PF.cancel_proforma)
    i_tx, i_cek = src.index("async with conn.transaction():"), src.index("_rencana_batal(")
    assert i_tx < i_cek, "cek terbayar DI DALAM transaksi"
    assert src.index("FROM sales_orders WHERE id = $1 AND tenant_id = $2 FOR UPDATE") < i_cek


def test_blok_pertama_diangkat_dengan_status_dan_detail_lama():
    r = {"blocks": [PF._blok("PROFORMA_NOT_DRAFT", 400, "Hanya proforma 'draft' ..."), PF._blok("X", 422, "y")]}
    with pytest.raises(HTTPException) as e:
        PF._angkat_blok_pertama(r)
    assert e.value.status_code == 400 and e.value.detail == "Hanya proforma 'draft' ..."
    PF._angkat_blok_pertama({"blocks": []})
    d = {"code": "SO_NOT_BILLABLE", "message": "m"}
    assert PF._blok("SO_NOT_BILLABLE", 400, d)["message"] == "m"


class _C:
    async def execute(self, q, *a):
        return "OK"


def _req(kunci):
    return SimpleNamespace(headers={"X-Idempotency-Key": kunci} if kunci else {})


def test_idem_aksi_tanpa_kunci_dan_badan_beda(monkeypatch):
    ctx = {"tenant_id": "t", "user_id": str(uuid.uuid4())}
    assert asyncio.run(PF._idem_aksi(_C(), ctx, _req(None), "CANCEL", "p", {}, None)) == (None, None, None)
    async def beda(*a):
        raise LookupError("beda")
    monkeypatch.setattr(PF, "ambil_replay_klien", beda)
    with pytest.raises(HTTPException) as e:
        asyncio.run(PF._idem_aksi(_C(), ctx, _req("k"), "CANCEL", "p", {"reason": "a"}, None))
    assert e.value.status_code == 409 and e.value.detail["code"] == "IDEMPOTENCY_KEY_REUSED"


def test_pratinjau_batal_alasan_opsional_tapi_tulis_wajib():
    assert PF.PratinjauBatalProforma().reason is None
    with pytest.raises(Exception):
        PF.CancelProformaRequest(reason="")
    assert "CANCEL_REASON_REQUIRED" in inspect.getsource(PF._rencana_batal)
