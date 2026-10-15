"""W-20261015-BAGIKAN-BATAL (putusan MASTER; pola NetSuite: record void tak dibagikan): share TUNGGAL
(/documents/{kind}/{id}/share + /share/preview) menolak dokumen DIBATALKAN dengan kode DOKUMEN_BATAL yang SAMA dengan jalur
MASSAL. Satu penentu `dokumen.tolak_batal` dipakai _bagikan dan bulk_dokumen._periksa_bagikan. Penawaran tetap memakai
penentunya sendiri (QUOTE_NOT_SHAREABLE)."""
import asyncio
import inspect
import uuid

import pytest
from fastapi import HTTPException

from app.routers import bulk_dokumen as BK
from app.routers import dokumen as DK

T = "kaos-biru-konveksi"
UID = uuid.uuid4()

# kind -> status batal nyata tabelnya
BATAL = [("invoice", "void"), ("delivery", "voided"), ("credit_note", "void"), ("proforma", "cancelled"),
         ("rekap", "cancelled"), ("receipt", "void"), ("receipt", "voided")]


class Conn:
    def __init__(self, status, nomor="DOK-1"):
        self.row = {"nomor": nomor, "status": status}

    async def fetchrow(self, sql, *a):
        return self.row

    async def fetchval(self, sql, *a):  # receipt: bukan uang muka -> tanpa cek izin E uang muka
        return None


def bagikan(monkeypatch, kind, status):
    from app.services import dokumen_bagikan as DB
    dibuat = []

    async def buat(conn, tid, k, uid, channel, user_id):
        dibuat.append(k)
        return {"id": "s1", "url": "u", "sent_at": None, "expires_at": None}
    monkeypatch.setattr(DB, "buat_tautan", buat)
    req = object()
    hasil = asyncio.run(DK._bagikan(Conn(status), req, {"tenant_id": T, "user_id": "u"}, kind, UID, "link"))
    return hasil, dibuat


@pytest.mark.parametrize("kind,status", BATAL)
def test_share_tunggal_menolak_batal_dengan_kode_massal(monkeypatch, kind, status):
    with pytest.raises(HTTPException) as e:
        bagikan(monkeypatch, kind, status)
    assert e.value.status_code == 409 and e.value.detail["code"] == "DOKUMEN_BATAL"
    assert "sudah dibatalkan; tak bisa dibagikan" in e.value.detail["message"]


@pytest.mark.parametrize("kind,status", [("invoice", "posted"), ("invoice", "paid"), ("delivery", "posted"),
                                         ("rekap", "confirmed"), ("receipt", "posted")])
def test_kontrol_dokumen_aktif_tetap_dibagikan(monkeypatch, kind, status):
    (hasil, info), dibuat = bagikan(monkeypatch, kind, status)
    assert dibuat == [kind] and info["boleh"] is True


def test_satu_penentu_tunggal_dan_massal():
    assert "tolak_batal(kind, dok)" in inspect.getsource(DK._bagikan)
    src = inspect.getsource(BK._periksa_bagikan)
    assert "D.tolak_batal(kind, dok)" in src and "_BATAL" not in src
    assert not hasattr(BK, "_BATAL")
    assert DK.STATUS_BATAL_BAGIKAN == frozenset({"void", "voided", "cancelled", "batal"})


def test_pesan_massal_tak_berubah():
    with pytest.raises(HTTPException) as e:
        DK.tolak_batal("invoice", {"nomor": "INV-1", "status": "VOID"})
    assert e.value.detail == {"code": "DOKUMEN_BATAL", "message": "Faktur INV-1 sudah dibatalkan; tak bisa dibagikan."}
    DK.tolak_batal("invoice", {"nomor": "INV-1", "status": "posted"})  # tak melempar
