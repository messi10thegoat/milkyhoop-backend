"""V310 (25 Sep 2026): flag CW conversational_detail_so + conversational_form_so_save untuk KAOS saja.

Semantik DB (idempoten, kaos dapat, grapgrap tidak): gerbang scratch scripts/gate_v310_flag.sh.
Di sini: isi migrasi + kontrak "permissions/me meneruskan SEMUA flag aktif (tanpa daftar-izin)".
"""
import re
from pathlib import Path

import pytest

from app.services import tenant_features as tf

MIG = Path(tf.__file__).resolve().parents[3] / "migrations" / "V310__flag_cw_so_kaos.sql"
FLAG = {"conversational_detail_so", "conversational_form_so_save"}


def _kode():
    return "\n".join(b for b in MIG.read_text().splitlines() if not b.lstrip().startswith("--"))


def test_migrasi_hanya_kaos_dua_flag_tepat():
    k = _kode()
    ins = k.split("INSERT INTO tenant_features", 1)[1].split("ON CONFLICT", 1)[0]
    assert re.findall(r"'([a-z0-9-]+)'", ins.split("unnest", 1)[0]) == ["kaos-biru-konveksi"]
    assert set(re.findall(r"'(conversational_[a-z_]+)'", ins)) == FLAG
    assert "grapgrap" not in ins
    assert "ON CONFLICT (tenant_id, feature) DO NOTHING" in k
    for f in FLAG:                                   # CHECK tabel V304
        assert re.fullmatch(r"[a-z][a-z0-9_]{2,63}", f)


def test_migrasi_gagal_keras_dan_pagar_tenant_lain():
    k = _kode()
    assert "RAISE EXCEPTION 'V310: flag kaos-biru-konveksi tidak lengkap/aktif'" in k
    assert "tenant_id <> 'kaos-biru-konveksi'" in k and "RAISE EXCEPTION 'V310: flag kaos-saja" in k
    assert not re.search(r"^\s*(DELETE|UPDATE|TRUNCATE)\b", k, re.I | re.M)


class _Conn:
    def __init__(self, rows):
        self.rows, self.q = rows, []

    async def fetch(self, q, *a):
        self.q.append((q, a))
        return [{"feature": f} for f in self.rows]


@pytest.mark.asyncio
async def test_fitur_aktif_tanpa_daftar_izin():
    """Flag baru apa pun yang enabled di DB sampai ke `features` tanpa perubahan kode."""
    rows = sorted(FLAG | {"conversational_form_so", "conversational_workspace_so", "flag_karangan_xyz"})
    c = _Conn(rows)
    assert await tf.fitur_aktif(c, "kaos-biru-konveksi") == rows
    (q, a), = c.q
    assert "WHERE tenant_id = $1 AND enabled" in q and a == ("kaos-biru-konveksi",)
