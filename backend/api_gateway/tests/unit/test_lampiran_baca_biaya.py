"""B8b (12 Nov 2026): Biaya di hub pembaca lampiran GET /api/lampiran/expense/{id} -> FE punya SATU pembaca lampiran. Dijaga: izin rute (expense R) == izin
GET /api/expenses/{id}/attachments, url grup = rute unduh Biaya (routers/expenses), SQL bertenant di tiap tabel + entity_type 'expense', induk SAMA dengan pabrik
(lintas tenant 404 tanpa kueri baris), bentuk mode dokumen, jenis lain tak berubah. Biaya TIDAK punya rute pabrik kedua (tak ada rute ganda yang membayangi)."""
import re
import uuid

import pytest
from fastapi import HTTPException

from app.middleware import permission_middleware as pm
from app.routers import lampiran_dokumen as LD
from app.routers import lampiran_induk as LI
from app.routers import sales_order_lampiran as SL
from tests.unit.test_lampiran_baca_kasbank import Kon, T, jalankan
from tests.unit.test_lampiran_so_keluarga import _pemenang, baris

DOK = uuid.uuid4()


def test_izin_rute_dan_izin_grup_sama_dengan_daftar_lampiran_biaya():
    assert pm.izin_untuk_jalur(f"/api/lampiran/expense/{DOK}") == ("expense", "R")
    assert pm.izin_untuk_jalur(SL.jalur_izin_jenis("expense")) == ("expense", "R")
    assert pm.izin_untuk_jalur(f"/api/expenses/{DOK}/attachments") == ("expense", "R")


def test_url_grup_ke_rute_unduh_biaya():
    r = _pemenang(SL.url_unduh_lampiran(SL.JENIS_KELUARGA["expense"][0], uuid.uuid4(), uuid.uuid4()))
    assert r is not None and r.endpoint.__module__.rsplit(".", 1)[-1] == "expenses" and r.endpoint.__name__ == "download_expense_attachment"


def test_spesifikasi_dan_tanpa_rute_ganda():
    assert LD.JENIS_TUNGGAL["expense"] == (LI.SPEC_BIAYA, "expense_date")
    assert (LI.SPEC_BIAYA.entitas, LI.SPEC_BIAYA.tabel, LI.SPEC_BIAYA.kolom_nomor) == ("expense", "expenses", "expense_number")
    assert LI.SPEC_BIAYA.pra_cek_dok is None
    assert not hasattr(LI, "router_biaya")                                   # tak ada router pabrik: rute Biaya tetap satu (routers/expenses)
    assert not set(LD.JENIS_TUNGGAL) & (set(LD.JENIS_DOKUMEN) | set(LD.JENIS_PEMBELIAN))


def test_sql_bertenant_tiap_tabel():
    s, tgl = LD.JENIS_TUNGGAL["expense"]
    q = " ".join(LD.sql_tunggal("expense").split())
    assert "WHERE expenses.id = $1 AND expenses.tenant_id = $2" in q
    assert "da.entity_type = 'expense' AND da.entity_id = expenses.id AND da.tenant_id = $2" in q
    assert "d.tenant_id = $2 AND d.deleted_at IS NULL" in q and f"expenses.{tgl}::date AS tanggal" in q
    for tabel, alias in re.findall(r"\bFROM\s+(\w+)(?:\s+(\w+))?\s+WHERE", q):
        assert re.search(rf"\b{alias or tabel}\.tenant_id = (\$2|expenses\.tenant_id)", q), (tabel, q)


def test_bentuk_keluaran_mode_dokumen(monkeypatch):
    a1, a2 = uuid.uuid4(), uuid.uuid4()
    kon = Kon(rows=[baris("expense", DOK, "EXP-2611-0001", a1, "nota.png"), baris("expense", DOK, "EXP-2611-0001", a2, "lama.pdf", storage="local")])
    out = jalankan(monkeypatch, kon, "expense")
    assert kon.kueri[0][1] == LI.sql_spec(LI.SPEC_BIAYA)["induk"] and kon.kueri[1][1] == LD.sql_tunggal("expense") and kon.kueri[1][2] == (DOK, T)
    assert out["sumber"] == {"jenis": "expense", "id": str(DOK), "nomor": "KB-0001", "mode": "dokumen", "sales_order": None}
    g = out["data"][0]
    assert g["dokumen"]["jenis"] == "expense" and out["jumlah"] == 2
    lp = {x["display_name"]: x for x in g["lampiran"]}
    assert lp["nota.png"]["url"] == f"/api/expenses/{DOK}/attachments/{a1}/download" and lp["nota.png"]["tersedia"] is True
    assert lp["lama.pdf"]["url"] is None and lp["lama.pdf"]["tersedia"] is False


def test_lintas_tenant_404_tanpa_kueri_baris(monkeypatch):
    kon = Kon(induk=False)
    with pytest.raises(HTTPException) as e:
        jalankan(monkeypatch, kon, "expense")
    assert e.value.status_code == 404 and [k for k, *_ in kon.kueri] == ["induk"]
