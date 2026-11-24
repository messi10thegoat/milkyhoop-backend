"""Batch B (24 Nov 2026): ubah batch = SATU penentu untuk POST /item-batches/{id}/ubah/preview dan PATCH /{id}.
Blok: NOMOR_BATCH_KEMBAR 409, NOMOR_BATCH_TERPAKAI 409, NOMOR_BATCH_KOSONG 422, TANGGAL_KEDALUWARSA_SEBELUM_PRODUKSI 422; tanggal/mutu tetap boleh."""
import asyncio
import os
import uuid
from datetime import date
from pathlib import Path
from types import SimpleNamespace as NS

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

import pytest  # noqa: E402

from app.services import batch_aksi as BA  # noqa: E402
from app.services.bayar_keluar_rencana import Blok  # noqa: E402
from app.services.galat_berkode import GalatBerkode  # noqa: E402

APP = Path(__file__).parents[2] / "app"
HARI = date(2026, 11, 24)
BID, IID = uuid.UUID(int=1), uuid.UUID(int=2)


def jalan(c):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(c)


def batch(**kw):
    b = {"id": BID, "item_id": IID, "batch_number": "LOT-001", "manufacture_date": date(2026, 1, 1), "expiry_date": date(2027, 1, 1), "supplier_batch_number": None,
         "quality_grade": None, "quality_notes": None, "status": "active", "current_quantity": 10, "base_unit": "pcs", "item_name": "Susu", "bill_id": None, "purchase_order_id": None}
    b.update(kw)
    return b


class Konn:
    def __init__(self, b, kembar=False, rujukan=None):
        self.b, self.kembar, self.rujukan, self.sql = b, kembar, rujukan or {}, []
    async def fetchrow(self, sql, *a):
        self.sql.append(sql)
        return self.b
    async def fetchval(self, sql, *a):
        self.sql.append(sql)
        if "FROM item_batches WHERE tenant_id" in sql:
            return 1 if self.kembar else None
        for t, n in self.rujukan.items():
            if f"FROM {t} " in sql:
                return n
        return 0


def body(**kw):
    d = dict(batch_number=None, manufacture_date=None, expiry_date=None, supplier_batch_number=None, quality_grade=None, quality_notes=None, status=None)
    d.update(kw)
    return NS(**d)


def kode(r):
    return [x["code"] for x in r["blocks"]]


def pr(c, **kw):
    return jalan(BA.rencana_ubah(c, "t", BID, body(**kw), Blok(True), HARI))


# ---------------------------------------------------------------- nomor batch
def test_ubah_nomor_bebas_lolos_dengan_teks_sebelum_sesudah():
    r = pr(Konn(batch()), batch_number="LOT-002")
    assert r["blocks"] == [] and r["rencana"]["perubahan"] == [{"field": "batch_number", "label": "Nomor batch", "sebelum_text": "LOT-001", "sesudah_text": "LOT-002"}]
    assert r["rencana"]["ringkas"] == "Batch LOT-001: Nomor batch LOT-001 → LOT-002"


def test_nomor_kembar_409_berkode_di_penulis_dan_blok_di_pratinjau():
    assert kode(pr(Konn(batch(), kembar=True), batch_number="LOT-009")) == ["NOMOR_BATCH_KEMBAR"]
    with pytest.raises(GalatBerkode) as e:
        jalan(BA.rencana_ubah(Konn(batch(), kembar=True), "t", BID, body(batch_number="LOT-009"), Blok(False), HARI))
    assert e.value.status_code == 409 and e.value.code == "NOMOR_BATCH_KEMBAR" and "LOT-009" in e.value.detail


@pytest.mark.parametrize("tabel", list(BA.TABEL_RUJUKAN_BATCH))
def test_nomor_terpakai_diblok_bila_dirujuk_tabel_mana_pun(tabel):
    r = pr(Konn(batch(), rujukan={tabel: 2}), batch_number="LOT-777")
    assert kode(r) == ["NOMOR_BATCH_TERPAKAI"] and "tidak bisa diganti" in r["blocks"][0]["message"] and "Tanggal dan mutu" in r["blocks"][0]["message"]


@pytest.mark.parametrize("kolom", ["bill_id", "purchase_order_id"])
def test_nomor_terpakai_diblok_bila_batch_asal_penerimaan(kolom):
    assert kode(pr(Konn(batch(**{kolom: uuid.UUID(int=5)})), batch_number="LOT-777")) == ["NOMOR_BATCH_TERPAKAI"]


def test_nomor_sama_atau_tak_dikirim_tak_memicu_pagar_nomor():
    assert pr(Konn(batch(), kembar=True, rujukan={"inventory_ledger": 9}), batch_number="LOT-001")["blocks"] == []
    assert pr(Konn(batch(), kembar=True, rujukan={"inventory_ledger": 9}), quality_grade="A")["blocks"] == []


def test_nomor_kosong_atau_spasi_diblok_dan_di_trim():
    assert kode(pr(Konn(batch()), batch_number="   ")) == ["NOMOR_BATCH_KOSONG"]
    r = pr(Konn(batch()), batch_number="  LOT-005 ")
    assert r["rencana"]["perubahan"][0]["sesudah_text"] == "LOT-005"


# ---------------------------------------------------------------- tanggal & mutu tetap boleh walau nomor terkunci
def test_tanggal_dan_mutu_boleh_pada_batch_terpakai():
    r = pr(Konn(batch(), rujukan={"inventory_ledger": 9}), expiry_date=date(2027, 6, 30), quality_grade="A")
    assert r["blocks"] == [] and {x["field"] for x in r["rencana"]["perubahan"]} == {"expiry_date", "quality_grade"}
    assert [x["sesudah_text"] for x in r["rencana"]["perubahan"] if x["field"] == "expiry_date"] == ["30 Jun 2027"]


def test_kedaluwarsa_sebelum_produksi_422_di_dua_arah():
    assert kode(pr(Konn(batch()), expiry_date=date(2025, 12, 31))) == ["TANGGAL_KEDALUWARSA_SEBELUM_PRODUKSI"]          # vs produksi lama 2026-01-01
    assert kode(pr(Konn(batch()), manufacture_date=date(2027, 2, 1))) == ["TANGGAL_KEDALUWARSA_SEBELUM_PRODUKSI"]       # vs kedaluwarsa lama 2027-01-01
    assert pr(Konn(batch()), manufacture_date=date(2027, 1, 1))["blocks"] == []                                          # sama hari boleh
    with pytest.raises(GalatBerkode) as e:
        jalan(BA.rencana_ubah(Konn(batch()), "t", BID, body(expiry_date=date(2025, 1, 1)), Blok(False), HARI))
    assert e.value.status_code == 422 and e.value.code == "TANGGAL_KEDALUWARSA_SEBELUM_PRODUKSI"


def test_kedaluwarsa_lampau_peringatan_bukan_blok_dan_tanpa_perubahan_beralasan():
    r = pr(Konn(batch()), expiry_date=date(2026, 6, 1))
    assert r["blocks"] == [] and any("sudah lewat" in x for x in r["peringatan"])
    n = pr(Konn(batch()), quality_grade=None)
    assert n["blocks"] == [] and n["rencana"]["perubahan"] == [] and n["rencana"]["ringkas"] == "Batch LOT-001: tidak ada perubahan." and n["peringatan"]


def test_batch_tak_ada_404():
    r = pr(Konn(None), quality_grade="A")
    assert kode(r) == ["BATCH_TIDAK_DITEMUKAN"]


def test_status_ikut_memakai_penentu_status_yang_sama():
    r = pr(Konn(batch()), status="quarantine")
    assert r["blocks"] == [] and r["rencana"]["perubahan"][0]["field"] == "status" and r["rencana"]["perubahan"][0]["sesudah_text"] == "Karantina"


# ---------------------------------------------------------------- kabel router + izin
SRC = (APP / "routers" / "item_batches.py").read_text()


def test_patch_dan_pratinjau_memakai_satu_penentu_dan_unique_race_jadi_409():
    assert SRC.count("BA.rencana_ubah(") == 2
    patch = SRC[SRC.index("async def update_item_batch"):SRC.index('@router.post("/{batch_id}/adjust/preview")')]
    assert "Blok(False)" in patch and "asyncpg.UniqueViolationError" in patch and '"NOMOR_BATCH_KEMBAR"' in patch
    pre = SRC[SRC.index("async def preview_ubah_batch"):SRC.index("async def preview_ubah_batch") + 900]
    assert "readonly=True" in pre and "UPDATE " not in pre and "INSERT " not in pre


def test_izin_pratinjau_ubah_batch_adalah_U_dan_di_atas_pola_post_generik():
    mw = (APP / "middleware" / "permission_middleware.py").read_text()
    khusus = '(r"^/api/item-batches/[^/]+/ubah/preview$", ["POST"], "item_batch", "U")'
    assert khusus in mw and mw.index(khusus) < mw.index('(r"^/api/item-batches", ["POST"], "item_batch", "C")')
    assert "POST /api/item-batches/{batch_id}/ubah/preview" in (Path(__file__).parent / "inventaris_rute_tulis.txt").read_text()


def test_kunci_field_blok_untuk_fe():
    for k, f in {"NOMOR_BATCH_KEMBAR": "batch_number", "NOMOR_BATCH_TERPAKAI": "batch_number", "NOMOR_BATCH_KOSONG": "batch_number",
                 "TANGGAL_KEDALUWARSA_SEBELUM_PRODUKSI": "expiry_date"}.items():
        assert BA.KODE_FIELD[k] == f
