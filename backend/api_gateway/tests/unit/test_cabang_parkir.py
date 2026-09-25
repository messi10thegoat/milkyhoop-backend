"""Parkir 6 rute cabang ber-SQL rusak (kolom hantu journal_entries.entry_date) — 25 Sep 2026.

Tiap rute rusak -> 409 FEATURE_NOT_AVAILABLE "Fitur cabang belum tersedia." SEBELUM handler (nol SQL);
rute cabang yang sehat tidak diparkir. Penjaga premis: SQL rusak itu memang masih ada di berkasnya
(kalau kelak diperbaiki, tes ini merah -> lepas parkirnya dengan sadar).
"""
import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import branches as B

RUSAK = [
    ("GET", "/api/branches/comparison"),
    ("GET", "/api/branches/ranking"),
    ("POST", "/api/branches/transfers"),
    ("POST", "/api/branches/transfers/00000000-0000-0000-0000-000000000001/receive"),
    ("GET", "/api/branches/00000000-0000-0000-0000-000000000001/summary"),
    ("GET", "/api/branches/00000000-0000-0000-0000-000000000001/trial-balance"),
]


@pytest.fixture
def klien(monkeypatch):
    async def meledak(*a, **k):
        raise AssertionError("SQL/DB disentuh padahal rute diparkir")
    for nama in ("get_pool", "get_db_pool", "get_db_connection"):
        if hasattr(B, nama):
            monkeypatch.setattr(B, nama, meledak)
    app = FastAPI()
    app.include_router(B.router, prefix="/api/branches")
    return TestClient(app)


@pytest.mark.parametrize("metode,path", RUSAK)
def test_rute_rusak_409_nol_sql(klien, metode, path):
    r = klien.request(metode, path, json={})
    assert r.status_code == 409, (path, r.status_code, r.text)
    assert r.json()["detail"] == {"code": "FEATURE_NOT_AVAILABLE", "message": "Fitur cabang belum tersedia."}


def test_hanya_enam_rute_diparkir():
    diparkir = set()
    for r in B.router.routes:
        if any(getattr(d.call, "__qualname__", "").startswith("fitur_belum_tersedia_dengan")
               for d in getattr(r, "dependant", None).dependencies if getattr(r, "dependant", None)):
            for m in r.methods:
                diparkir.add((m, r.path))
    assert diparkir == {("GET", "/comparison"), ("GET", "/ranking"), ("POST", "/transfers"),
                        ("POST", "/transfers/{transfer_id}/receive"), ("GET", "/{branch_id}/summary"),
                        ("GET", "/{branch_id}/trial-balance")}


def test_premis_sql_rusak_masih_ada():
    src = Path(B.__file__).read_text()
    assert len(re.findall(r"\bentry_date\b", src)) >= 7
