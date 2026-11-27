"""BUG-407: idem_aksi barang/kategori tak boleh mengirim doc_id teks (nama kategori, "id:ke") ke kolom uuid result_id."""
import asyncio
import uuid
from unittest import mock

from app.services import barang_aksi as BA
from app.services import idem_buat


def _jalan(doc_id, resp):
    simpan = mock.AsyncMock(return_value=resp)
    async def kerja():
        return resp
    with mock.patch.object(idem_buat, "mulai_aksi", mock.AsyncMock(return_value=("kp", "sd", None))), \
         mock.patch.object(idem_buat, "kunci_dari", lambda r: None), mock.patch.object(idem_buat, "simpan", simpan):
        asyncio.run(BA.idem_aksi(None, {}, None, "X", doc_id, {}, kerja))
    return simpan.call_args.args[-1]


def test_doc_id_nama_kategori_tak_jadi_result_id():
    assert _jalan("uji kategori paritas", {"data": {"id": "bukan-uuid"}}) is None


def test_id_respons_dipakai_bila_uuid():
    u = uuid.uuid4()
    assert _jalan("uji kategori paritas", {"data": {"id": str(u)}}) == u


def test_doc_id_uuid_tetap():
    u = uuid.uuid4()
    assert _jalan(str(u), {}) == u
