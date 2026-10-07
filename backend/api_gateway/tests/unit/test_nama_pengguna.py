"""Nama pengguna SATU rantai (6 Okt 2026): user_profiles.display_name -> "User".fullname -> "User".name.

Latar: pemilik grapgrap mengganti nama di Akun > Profil (user_profiles.display_name), dropdown Penanda tangan Penawaran
(GET /api/team-members) dan default_penawaran tetap membaca "User".name -> nama lama. Pembaca nama pengguna WAJIB satu fungsi.
"""
import asyncio
import inspect
import os
from datetime import date

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")  # impor app.main butuh satu penyedia LLM

import pytest
from starlette.routing import Match

from app.routers import team_members as TM
from app.services import akun_saya as A
from app.services import default_dokumen as DD
from app.services.nama_pengguna import nama_pengguna, nama_pengguna_sql


@pytest.mark.parametrize("p,f,n,harap", [
    ("Grapgrap Clothing", "Nama Lengkap", "grapgrap manado", "Grapgrap Clothing"),   # profil menang
    (None, "Nama Lengkap", "grapgrap manado", "Nama Lengkap"),
    ("   ", "", "grapgrap manado", "grapgrap manado"),                                # kosong/spasi dilewati
    ("  Anton  ", None, None, "Anton"),                                                 # dipangkas
    (None, None, None, None), ("", " ", "\t", None),
])
def test_rantai(p, f, n, harap):
    assert nama_pengguna(p, f, n) == harap


def test_padanan_sql_urutan_sama():
    s = " ".join(nama_pengguna_sql("p", "u").split())
    assert s.index("p.display_name") < s.index("u.fullname") < s.index("u.name")
    assert "NULLIF(trim(" in s


class _KoneksiTtd:
    def __init__(self):
        self.sql = []

    async def fetchval(self, q, *a):
        return None

    async def fetchrow(self, q, *a):
        self.sql.append(q)
        if "default_quote_opening_text" in q:
            return {"default_quote_opening_text": None, "default_quote_closing_text": None, "default_quote_notes": None,
                    "default_quote_terms": None, "default_quote_signer_user_id": "610da610-c6df-4013-af7f-601554e2182b",
                    "default_quote_signer_title": None, "default_quote_signer_phone": None}
        if 'FROM "User"' in q:
            return {"id": "610da610-c6df-4013-af7f-601554e2182b", "nama": "Grapgrap Clothing", "email": "g@x.id"}
        return None


def test_default_penawaran_penanda_tangan_membaca_rantai():
    k = _KoneksiTtd()
    d = asyncio.run(DD.default_penawaran(k, "t-uji", date(2026, 10, 6)))
    assert d["signer"]["name"] == "Grapgrap Clothing"
    q = " ".join([s for s in k.sql if 'FROM "User"' in s][0].split())
    assert "LEFT JOIN user_profiles p ON p.user_id = u.id" in q and "p.display_name" in q and "u.fullname" in q


def test_team_members_menyertakan_display_name_daftar_dan_detail():
    assert "display_name" in TM.TeamMemberResponse.model_fields
    for fn in (TM.list_team_members if hasattr(TM, "list_team_members") else None, TM.get_team_member):
        if fn is None:
            continue
        src = " ".join(inspect.getsource(fn).split())
        assert "LEFT JOIN user_profiles up ON up.user_id = u.id" in src and "up.display_name AS profil_nama" in src
        assert 'display_name=nama_pengguna(row["profil_nama"], row["fullname"], row["name"])' in src
    # field lama tak berubah (halaman Tim)
    assert {"name", "fullname", "email"} <= set(TM.TeamMemberResponse.model_fields)


def test_rute_team_members_dimenangkan_handler_yang_benar_lewat_tabel_rute():
    from app.main import app

    def pemenang(path):
        scope = {"type": "http", "method": "GET", "path": path, "root_path": "", "headers": []}
        for r in app.routes:
            m, _ = r.matches(scope)
            if m == Match.FULL:
                return r.endpoint
        return None
    assert pemenang("/api/team-members") is TM.list_team_members
    assert pemenang("/api/team-members/3f2c6d0e-5a41-4c43-9a55-0d6f1a3b7c11") is TM.get_team_member


class _KoneksiAkun:
    def __init__(self, profil, fullname, name):
        self.profil, self.fullname, self.name = profil, fullname, name

    async def fetchrow(self, sql, *a):
        if 'FROM "User"' in sql:
            return {"name": self.name, "fullname": self.fullname, "email": "a@x.id"}
        if "user_profiles" in sql:
            return {"display_name": self.profil}
        if 'FROM "Tenant"' in sql:
            return {"id": a[0], "display_name": "T", "alias": None, "plan_tier": "BASE"}
        raise AssertionError(sql)


def test_me_nama_memakai_rantai_yang_sama(monkeypatch):
    from app.services import role_resolution as RR

    async def peran(conn, user_id, tenant_id):
        return None
    monkeypatch.setattr(RR, "try_resolve_business_role", peran)
    for p, f, n, harap in [("Grapgrap Clothing", "Lengkap", "x", "Grapgrap Clothing"), (None, "Lengkap", "x", "Lengkap"),
                           (None, None, "x", "x"), (None, None, None, "a")]:
        r = asyncio.run(A.akun_saya(_KoneksiAkun(p, f, n), "u1", "a@x.id", "kaos"))
        assert r["name"] == harap


def test_penjaga_pembaca_nama_tak_membaca_kolom_mentah_sendiri():
    """Pembaca nama untuk penanda tangan/me/team memakai fungsi bersama, bukan COALESCE buatan sendiri."""
    assert "nama_pengguna_sql(" in inspect.getsource(DD.default_penawaran)
    assert "nama_pengguna(" in inspect.getsource(A.akun_saya)
