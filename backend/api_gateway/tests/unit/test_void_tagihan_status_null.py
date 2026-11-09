"""VOID-NULL (10 Nov 2026, putusan MASTER A): tagihan lama tanpa status_v2 ditolak berkalimat oleh penentu bersama tagihan_aksi.blok_void
(penulis void_bill + pratinjau void + aksi_tersedia fpbatal), bukan 500 dari trigger trg_bills_status_v2_validation (NULL -> void ditolak DB).
Draf normal (status_v2 'draft') TETAP boleh dibatalkan (desain DB V460: draft -> void). Bukti salinan: scripts/ops/uji-salinan-void-null.sh."""
import asyncio
import inspect
import uuid

import pytest

from app.services import tagihan_aksi as TA

T = "kaos-biru-konveksi"


def run(c):
    return asyncio.run(c)


class Kon:
    async def fetch(self, sql, *a):
        return []


def _bill(status, status_v2):
    return {"id": uuid.uuid4(), "invoice_number": "BILL-2609-0007", "status": status, "status_v2": status_v2, "journal_id": None}


def test_draf_lama_tanpa_status_siklus_ditolak_berkalimat():
    b = run(TA.blok_void(Kon(), T, _bill("draft", None)))
    assert [x["code"] for x in b] == ["STATUS_SIKLUS_KOSONG"]
    assert b[0]["message"] == "Faktur pembelian BILL-2609-0007 adalah draf lama; hapus drafnya, bukan dibatalkan."


def test_non_draf_tanpa_status_siklus_ditolak_berkalimat():
    b = run(TA.blok_void(Kon(), T, _bill("posted", None)))
    assert b[0]["code"] == "STATUS_SIKLUS_KOSONG" and "belum punya status siklus" in b[0]["message"]


@pytest.mark.parametrize("status,status_v2", [("draft", "draft"), ("posted", "posted"), ("unpaid", "draft")])
def test_status_siklus_ada_tidak_terblok_oleh_penjaga_baru(monkeypatch, status, status_v2):
    async def bukan(*a):
        return False
    monkeypatch.setattr(TA, "tagihan_pembuka", bukan)
    assert run(TA.blok_void(Kon(), T, _bill(status, status_v2))) == []  # draf normal tetap boleh dibatalkan (draft -> void)


def test_sudah_batal_tetap_didahulukan():
    assert run(TA.blok_void(Kon(), T, _bill("void", None)))[0]["code"] == "SUDAH_BATAL"


def test_kolom_absen_gagal_keras_bukan_dianggap_null():
    with pytest.raises(KeyError):
        run(TA.blok_void(Kon(), T, {"id": uuid.uuid4(), "invoice_number": "X", "status": "draft"}))


def test_penulis_dan_penentu_bersama_membaca_status_v2():
    from app.services import bills_service as BS
    src = inspect.getsource(BS.BillsService.void_bill)
    assert "TA.blok_void(conn, tenant_id, bill)" in src and "status_v2" in src
    assert "status_v2" in inspect.getsource(TA.rencana_void) and "blok_void(conn, tid, b)" in inspect.getsource(TA.rencana_void)
