"""Biaya standar SO t2 (1/4): aksi_tersedia detail biaya -- SATU bentuk {kunci: {tersedia, alasan}} (schemas/aksi_tersedia), BERTIPE di
ExpenseDetailResponse (penjaga test_aksi_tersedia_satu_bentuk), alasan dari penentu yang SAMA dgn POST /{id}/post dan /{id}/void."""
import asyncio
import uuid
from datetime import date

import pytest
from fastapi import HTTPException

from app.schemas.aksi_tersedia import AksiTersediaPeta
from app.schemas.expenses import ExpenseDetailResponse
from app.services import biaya_aksi as BA

URUT = ["posting", "ubah", "hapus", "batalkan", "tambah_bukti"]


def _exp(status, journal=None):
    return {"id": uuid.uuid4(), "status": status, "journal_id": journal, "expense_date": date(2026, 11, 7)}


@pytest.fixture
def periode(monkeypatch):
    keadaan = {"tutup": False, "rekon": False, "tutup_tgl": set()}

    async def cek(conn, tid, tgl):
        if keadaan["tutup"] or tgl in keadaan["tutup_tgl"]:
            raise HTTPException(status_code=403, detail="Periode November 2026 sudah ditutup.")

    async def rekon(conn, tid, sid):
        if keadaan["rekon"]:
            raise HTTPException(status_code=409, detail={"code": "SUDAH_REKONSILIASI", "message": "Sudah direkonsiliasi bank."})

    async def hari(conn, tid):
        return date(2026, 11, 7)
    import app.routers.expenses as RE
    import app.services.jaga_rekonsiliasi as JR
    import app.utils.tanggal_tenant as TT
    monkeypatch.setattr(RE, "check_period_is_open", cek)
    monkeypatch.setattr(JR, "tolak_void_bila_terekonsiliasi", rekon)
    monkeypatch.setattr(TT, "tanggal_dokumen", hari)
    return keadaan


def _a(exp):
    return asyncio.run(BA.aksi_tersedia_biaya(None, "t", exp))


def test_draf(periode):
    a = _a(_exp("draft"))
    assert list(a) == URUT
    assert a["posting"] == {"tersedia": True, "alasan": None} and a["ubah"]["tersedia"] and a["hapus"]["tersedia"]
    assert a["batalkan"] == {"tersedia": False, "alasan": "Draf dihapus, bukan dibatalkan."}


def test_terbit(periode):
    a = _a(_exp("posted", journal=uuid.uuid4()))
    assert a["posting"]["tersedia"] is False and a["ubah"]["tersedia"] is False
    assert a["batalkan"] == {"tersedia": True, "alasan": None} and a["tambah_bukti"]["tersedia"]


def test_periode_tutup_dan_rekon_memberi_alasan(periode):
    periode["tutup"] = True
    assert _a(_exp("draft"))["posting"]["alasan"] == "Periode November 2026 sudah ditutup."
    periode["tutup"], periode["rekon"] = False, True
    assert _a(_exp("posted", journal=uuid.uuid4()))["batalkan"]["alasan"] == "Sudah direkonsiliasi bank."


def test_posting_hanya_draf_walau_tanpa_jurnal(periode):
    """Sabotase s6: status bukan draf ditolak SENDIRI, bukan menumpang cek jurnal."""
    assert _a(_exp("posted"))["posting"] == {"tersedia": False, "alasan": "Hanya biaya draf yang bisa diterbitkan."}


def test_batal_periode_hari_ini_tutup(periode):
    """Sabotase s7: tanggal biaya terbuka tetapi periode HARI INI (jurnal pembalik) tutup -> batalkan tak tersedia."""
    periode["tutup_tgl"].add(date(2026, 11, 7))
    e = _exp("posted", journal=uuid.uuid4())
    e["expense_date"] = date(2026, 10, 1)
    assert _a(e)["batalkan"] == {"tersedia": False, "alasan": "Periode November 2026 sudah ditutup."}


def test_batal(periode):
    a = _a(_exp("void", journal=uuid.uuid4()))
    assert a["batalkan"]["alasan"] == "Biaya sudah dibatalkan." and a["tambah_bukti"]["tersedia"] is False


def test_detail_bertipe_satu_bentuk():
    assert ExpenseDetailResponse.model_fields["aksi_tersedia"].annotation.__args__[0] == AksiTersediaPeta
    r = ExpenseDetailResponse.model_validate({"data": {}, "aksi_tersedia": {"posting": {"tersedia": False, "alasan": "x"}}}).model_dump()
    assert r["aksi_tersedia"] == {"posting": {"tersedia": False, "alasan": "x"}}
