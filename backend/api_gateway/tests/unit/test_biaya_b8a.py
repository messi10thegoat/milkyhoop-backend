"""Biaya B8a: bukti Biaya = lampiran NYATA (document_attachments entity 'expense' + documents belum dihapus), BUKAN tabel mati expense_attachments (0 baris, tanpa penulis).
SATU predikat (services/biaya_daftar) untuk daftar/pill/summary/aksi; SATU kueri daftar lampiran (_EXP_ATT_SQL_DAFTAR) untuk GET /{id}/attachments + detail; riwayat/documents baca tautan yang sama.
Penjaga 'dua pembaca sama-sama salah': tak satu pun berkas Biaya boleh membaca expense_attachments (sabotase pembaca lama harus MERAH). Paritas data = uji salinan."""
import asyncio
import inspect
import re
import uuid

import app.routers.expenses as EX
import app.services.biaya_daftar as BD
import app.services.biaya_riwayat as BR


def _tanpa_komentar(modul) -> str:
    return "\n".join(re.sub(r"#.*$", "", ln) for ln in inspect.getsource(modul).splitlines())


def test_tak_ada_pembaca_tabel_mati():
    for m in (EX, BD, BR):
        assert not re.search(r"(FROM|JOIN)\s+expense_attachments", _tanpa_komentar(m)), m.__name__


def test_predikat_ada_bukti_dan_jumlah():
    for sql in (BD.sql_ada_bukti("expenses"), BD.sql_ada_bukti("e"), BD.sql_jumlah_bukti("expenses")):
        assert "document_attachments da" in sql and "da.entity_type = 'expense'" in sql
        assert "d.deleted_at IS NULL" in sql
        assert "da.tenant_id = " in sql and "d.tenant_id = da.tenant_id" in sql   # tenant eksplisit di KEDUA tabel
    assert BD.sql_ada_bukti("e").count("e.id") == 1 and "e.tenant_id" in BD.sql_ada_bukti("e")
    assert BD.sql_jumlah_bukti().startswith("(SELECT COUNT(*)")


def test_pill_tanpa_bukti_memakai_predikat_baru():
    assert BD.sql_ada_bukti("expenses") in BD.sql_aksi("tanpa_bukti", "$2")


def test_daftar_memakai_jumlah_bukti_baru():
    s = _tanpa_komentar(EX)
    assert 'BD.sql_jumlah_bukti("expenses")' in s and "AS jumlah_bukti" in s


def test_satu_kueri_daftar_lampiran_untuk_detail_dan_get():
    s = _tanpa_komentar(EX)
    assert s.count("_EXP_ATT_SQL_DAFTAR") == 3          # definisi + GET /attachments + detail
    sql = EX._EXP_ATT_SQL_DAFTAR
    assert "da.entity_type = 'expense'" in sql and "d.deleted_at IS NULL" in sql and "da.tenant_id = $2 AND d.tenant_id = $2" in sql


def test_detail_attachments_bentuk_respons_sama_dengan_get():
    src = inspect.getsource(EX.get_expense_detail)
    assert "_exp_lampiran_ke_respons(attachments" in src and "_EXP_ATT_SQL_DAFTAR" in src
    assert "bool(result[\"attachments\"])" in src      # position_text tetap turunan lampiran nyata


class _Kon:
    def __init__(self):
        self.sql = []

    async def fetch(self, q, *a):
        self.sql.append((q, a))
        return []


def test_riwayat_bukti_baca_tautan_nyata_bersaring_tenant():
    k = _Kon()
    asyncio.run(BR._bukti(k, "kaos-biru-konveksi", uuid.uuid4()))
    q, a = k.sql[0]
    assert "document_attachments" in q and "da.entity_type = 'expense'" in q and "d.deleted_at IS NULL" in q
    assert "da.tenant_id = $2" in q and a[1] == "kaos-biru-konveksi"
