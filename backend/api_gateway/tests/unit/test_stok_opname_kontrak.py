"""Kontrak tambahan Stok Opname untuk FE (24 Nov 2026): aksi_tersedia + position_text + selisih/nilai BERTANDA (minus U+2212) dari server; angka tetap numerik."""
import asyncio
import os
import uuid
from datetime import date
from decimal import Decimal as D
from pathlib import Path

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

import pytest  # noqa: E402

from app.routers import stock_opnames as SO  # noqa: E402
from app.services import stok_opname_tampil as TP  # noqa: E402
from app.services.bayar_keluar_rencana import Blok  # noqa: E402

APP = Path(__file__).parents[2] / "app"
MINUS = "−"
P1, P2, P3 = uuid.UUID(int=1), uuid.UUID(int=2), uuid.UUID(int=3)


def jalan(c):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(c)


# ---------------------------------------------------------------- aksi_tersedia: matriks status x aksi
BENAR = {  # status -> aksi yang tersedia
    "draft": {"mulai", "isi", "hapus"}, "hitung": {"isi", "post", "batal"}, "posted": {"void"}, "void": set(), "batal": set(),
}


@pytest.mark.parametrize("status", ["draft", "hitung", "posted", "void", "batal"])
def test_matriks_aksi_per_status(status):
    a = TP.aksi_tersedia(status, 3)
    assert set(a) == set(TP.AKSI)
    assert {k for k, v in a.items() if v["tersedia"]} == BENAR[status]
    for k, v in a.items():                                        # tak tersedia WAJIB beralasan kalimat; tersedia alasan None
        assert (v["alasan"] is None) == v["tersedia"], (status, k)
        assert v["alasan"] is None or len(v["alasan"]) > 10


def test_mulai_butuh_baris_dan_alasan_spesifik():
    a = TP.aksi_tersedia("draft", 0)
    assert a["mulai"]["tersedia"] is False and "belum punya barang" in a["mulai"]["alasan"]
    assert TP.aksi_tersedia("draft", 2)["mulai"]["tersedia"] is True
    assert "dihapus, bukan dibatalkan" in TP.aksi_tersedia("draft", 1)["batal"]["alasan"]
    assert "mulai hitung dulu" in TP.aksi_tersedia("draft", 1)["post"]["alasan"]
    assert "void" in TP.aksi_tersedia("posted", 1)["batal"]["alasan"]


# ---------------------------------------------------------------- position_text
def test_position_text_per_status():
    assert TP.position_text("draft") == "Draf · belum dimulai"
    assert TP.position_text("hitung", 10, 4, 0) == "Sedang dihitung · 4/10 barang · hari ke-1"
    assert TP.position_text("hitung", 10, 10, 2) == "Sedang dihitung · 10/10 barang · hari ke-3"
    assert TP.position_text("hitung", 5, 0) == "Sedang dihitung · 0/5 barang"
    assert TP.position_text("posted") == "Selesai" and TP.position_text("void") == "Dibatalkan (void)"
    assert TP.position_text("batal") == "Dibatalkan sebelum selesai"


# ---------------------------------------------------------------- selisih & nilai bertanda
def test_selisih_text_tanda_minus_unicode_dan_nol():
    assert TP.selisih_text(D("3"), "pcs") == "+3 pcs"
    assert TP.selisih_text(D("-2.5"), "kg") == f"{MINUS}2.5 kg"
    assert TP.selisih_text(D("0"), "pcs") == "0 pcs" and TP.selisih_text(None, "pcs") is None
    assert TP.selisih_text(D("4"), None) == "+4" and "-" not in TP.selisih_text(D("-1"), "pcs")      # ASCII '-' dilarang


def test_medan_baris_naik_turun_nol_dan_belum_dihitung():
    n = TP.medan_baris(D("5"), D("500"), "pcs")
    assert n["selisih"] == D("5") and n["nilai_bertanda"] == D("500") and n["nilai_bertanda_text"] == "+Rp 500" and n["selisih_text"] == "+5 pcs"
    t = TP.medan_baris(D("-2"), D("200"), "pcs")
    assert t["nilai_bertanda"] == D("-200") and t["nilai_bertanda_text"] == f"{MINUS}Rp 200" and t["selisih_text"] == f"{MINUS}2 pcs"
    z = TP.medan_baris(D("0"), None, "pcs")
    assert z["nilai_bertanda"] == D("0") and z["nilai_bertanda_text"] == "Rp 0" and z["selisih_text"] == "0 pcs"
    assert TP.medan_baris(None, None, "pcs") == {"selisih": None, "selisih_text": None, "nilai_bertanda": None, "nilai_bertanda_text": None}


def test_total_bertanda_numerik_dan_teks():
    r = TP.total_bertanda(D("-1250"))
    assert r["total_nilai_bertanda"] == D("-1250") and r["total_nilai_bertanda_text"].startswith(MINUS) and isinstance(r["total_nilai_bertanda"], D)
    assert TP.total_bertanda(D("0"))["total_nilai_bertanda_text"] == "Rp 0"


# ---------------------------------------------------------------- _selisih_tampil: sumber angka per status
class KonnSA:
    def __init__(self, rows): self.rows = rows
    async def fetch(self, sql, *a):
        assert "stock_adjustment_items" in sql
        return self.rows


def o(status, adj=None):
    return {"id": uuid.UUID(int=9), "status": status, "adjustment_id": adj}


def test_posted_memakai_angka_sa_tertaut_dan_dihitung_selisih_nol_jadi_nol():
    rows = [{"product_id": P1, "quantity_adjustment": D("5"), "total_value": D("500")}, {"product_id": P2, "quantity_adjustment": D("-2"), "total_value": D("200")}]
    garis = [{"product_id": P1, "physical_quantity": D("9")}, {"product_id": P2, "physical_quantity": D("1")}, {"product_id": P3, "physical_quantity": D("4")},
             {"product_id": uuid.UUID(int=4), "physical_quantity": None}]
    d, total = jalan(SO._selisih_tampil(KonnSA(rows), "t", o("posted", uuid.UUID(int=8)), garis))
    assert d[P1] == (D("5"), D("500")) and d[P2] == (D("-2"), D("200")) and d[P3] == (D("0"), D("0")) and uuid.UUID(int=4) not in d
    assert total == D("300")


def test_draf_dan_batal_tanpa_selisih():
    for st in ("draft", "batal"):
        assert jalan(SO._selisih_tampil(None, "t", o(st), [])) == ({}, D("0"))
    assert jalan(SO._selisih_tampil(None, "t", o("posted", None), [])) == ({}, D("0"))


def test_hitung_memakai_penentu_posting_yang_sama(monkeypatch):
    async def rp(conn, tid, oid, bl, kunci=False):
        assert isinstance(bl, Blok) and bl.pratinjau and kunci is False                  # baca saja, tanpa kunci
        return {"baris": [{"product_id": str(P1), "dihitung": True, "selisih": D("-3"), "nilai": D("300")},
                          {"product_id": str(P2), "dihitung": False, "selisih": None, "nilai": None}], "total_nilai_bertanda": D("-300")}
    monkeypatch.setattr(SO, "rencana_posting", rp)
    d, total = jalan(SO._selisih_tampil(None, "t", o("hitung"), []))
    assert d == {P1: (D("-3"), D("300"))} and total == D("-300")


# ---------------------------------------------------------------- kabel di router
SRC = (APP / "routers" / "stock_opnames.py").read_text()


def test_daftar_detail_dan_post_preview_membawa_medan_kontrak():
    assert SRC.count("TP.aksi_tersedia(") == 2 and SRC.count("TP.position_text(") == 2         # daftar + detail
    assert "TP.total_bertanda(total)" in SRC and "TP.total_bertanda(h.get(\"total_nilai_bertanda\", NOL))" in SRC
    assert SRC.count("TP.medan_baris(") == 2                                                    # detail baris + post/preview baris
    assert "float(" not in SRC and "float(" not in (APP / "services" / "stok_opname_tampil.py").read_text()


def test_teks_memakai_minus_unicode_dari_penentu_bersama():
    src = (APP / "services" / "stok_opname_tampil.py").read_text()
    assert "SS.teks_bertanda" in src and "SS.nilai_bertanda" in src and '"\\u2212' in src
