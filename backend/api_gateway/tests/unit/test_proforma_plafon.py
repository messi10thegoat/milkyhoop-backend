"""Plafon tagihan proforma (celah 2), PDF (celah 1), "Sudah Dibayar" — SATU atribusi (services.proforma_atribusi),
putusan MASTER 28 Sep 2026. Fixture = SO NYATA grapgrap/kaos (diukur 28 Sep) + repro journey + pengetatan.
Angka harapan = literal spek, bukan konstanta modul."""
from datetime import datetime
from decimal import Decimal as D

import pytest
from fastapi import HTTPException

from app.routers import proformas as pf
from app.services import proforma_atribusi as PA
from app.services import proforma_terbayar as PT
from app.services.pdf_service import PDFService


def _t(h, m=0):
    return datetime(2026, 9, h, m)


def _pf(i, amt, at, purpose="DP", status="issued"):
    return {"id": i, "amount": D(str(amt)), "status": status, "issued_at": at, "proforma_number": f"PRO-{i}",
            "purpose": purpose}


def _dp(i, amt, at, pf_id=None):
    return {"id": i, "amount": D(str(amt)), "proforma_id": pf_id, "created_at": at}


def _sisa(total, pros, deps):
    a = PA.atribusikan(pros, deps)
    issued = sum((p["amount"] for p in pros if p["status"] == "issued"), D("0"))
    return pf.sisa_bisa_ditagih(total, issued, a["tak_tertagih"]), a


@pytest.mark.parametrize("nama,total,pros,deps,harap", [
    ("grapgrap 019-09-26: PELUNASAN lalu DP 1 menit kemudian tanpa tautan", 3_570_000,
     [_pf("a", 3_570_000, _t(18, 9), "PELUNASAN")], [_dp("d", 3_570_000, _t(18, 10))], 0),
    ("grapgrap SO-2609-0001: DP 14 Sep SEBELUM proforma 18 Sep, nominal sama", 4_670_000,
     [_pf("a", 3_000_000, _t(18))], [_dp("d", 3_000_000, _t(14))], 1_670_000),
    ("grapgrap 009-08-26: DP bayar proforma tanpa tautan", 52_250_000,
     [_pf("a", 30_210_000, _t(16, 6))], [_dp("d", 30_210_000, _t(16, 7))], 22_040_000),
    ("REPRO journey: DP 2 jt dulu, lalu PELUNASAN 3 jt ditagih neto -> 0 (max() memberi 2 jt)", 5_000_000,
     [_pf("a", 3_000_000, _t(20), "PELUNASAN")], [_dp("d", 2_000_000, _t(19))], 0),
    ("kaos SO-2609-0262: proforma penuh 110.000 + DP tanpa tautan Rp 30 -> lantai 0", 110_000,
     [_pf("a", 110_000, _t(20))], [_dp("d", 30, _t(21))], 0),
    ("pengetatan: DP 2 jt TANPA proforma, SO 5 jt -> 3 jt (dulu 5 jt)", 5_000_000, [], [_dp("d", 2_000_000, _t(20))],
     3_000_000),
])
def test_sisa_bisa_ditagih_fixture_nyata(nama, total, pros, deps, harap):
    assert _sisa(total, pros, deps)[0] == harap, nama


def test_tautan_menang_atas_pencocokan():
    pros = [_pf("A", 1_000_000, _t(10)), _pf("B", 1_000_000, _t(11))]
    deps = [_dp("d1", 1_000_000, _t(12), pf_id="B"), _dp("d2", 1_000_000, _t(13))]
    a = PA.atribusikan(pros, deps)
    assert a["per_proforma"]["B"]["tertaut"] == 1_000_000 and a["pasangan"] == {"d2": "A"}
    assert a["tak_tertagih"] == 0


def test_tiap_proforma_dicocokkan_sekali_saja():
    a = PA.atribusikan([_pf("A", 500_000, _t(10))], [_dp("d1", 500_000, _t(11)), _dp("d2", 500_000, _t(12))])
    assert a["pasangan"] == {"d1": "A"} and a["tak_tertagih"] == 500_000 and a["dep_tak_tertagih"] == ["d2"]


def test_draf_dan_batal_tak_bisa_dicocokkan():
    a = PA.atribusikan([_pf("A", 1_000, _t(10), status="draft"), _pf("B", 1_000, _t(10), status="cancelled")],
                       [_dp("d", 1_000, _t(11))])
    assert a["tak_tertagih"] == 1_000


class _Conn:
    """fetchrow = issued_total_for_order; fetch = muat_atribusi (proforma, lalu uang muka)."""
    def __init__(self, issued, pros, deps):
        self.issued, self.batch, self.sql = issued, [pros, deps], []

    async def fetchrow(self, sql, *a):
        self.sql.append(sql)
        return {"total": self.issued}

    async def fetch(self, sql, *a):
        self.sql.append(sql)
        return self.batch.pop(0)


def _so_row(p):
    return {**p, "sales_order_id": "so"}


@pytest.mark.asyncio
async def test_pagar_menolak_pelunasan_penuh_bila_dp_tanpa_tagihan():
    c = _Conn(0, [], [{**_dp("d", 2_000_000, _t(19)), "so_id": "so"}])
    with pytest.raises(HTTPException) as e:
        await pf.assert_within_order_total(c, "t", "so", 5_000_000.0, 5_000_000.0)
    d = e.value.detail
    assert e.value.status_code == 400
    for bagian in ("Nilai Sales Order", "sudah ditagih (issued)", "uang muka diterima", "di luar tagihan",
                   "sisa yang bisa ditagih", "3.000.000,00", "2.000.000,00"):
        assert bagian in d, d


@pytest.mark.asyncio
async def test_pagar_repro_sesudah_pelunasan_neto_tak_ada_sisa():
    c = _Conn(3_000_000, [_so_row(_pf("a", 3_000_000, _t(20), "PELUNASAN"))], [{**_dp("d", 2_000_000, _t(19)), "so_id": "so"}])
    with pytest.raises(HTTPException):
        await pf.assert_within_order_total(c, "t", "so", 5_000_000.0, 1.0)


@pytest.mark.asyncio
async def test_rincian_komponen_dan_sql_bukan_void_draft():
    c = _Conn(1_000_000, [_so_row(_pf("a", 1_000_000, _t(10)))],
              [{**_dp("d1", 1_000_000, _t(11)), "so_id": "so"}, {**_dp("d2", 500_000, _t(12)), "so_id": "so"}])
    r = await pf.rincian_tagih(c, "t", "so", 5_000_000.0)
    assert r == {"order_total": 5_000_000.0, "issued_total": 1_000_000.0, "received_total": 1_500_000.0,
                 "received_not_billed": 500_000.0, "billable_remaining": 3_500_000.0}
    q = " ".join(" ".join(c.sql).split())
    assert "cd.status NOT IN ('void', 'draft')" in q and "cd.tenant_id = $1" in q and "p.tenant_id = cd.tenant_id" in q


# ---------------------------------------------------------------- "Sudah Dibayar" (proforma_terbayar)
@pytest.mark.asyncio
async def test_sudah_dibayar_nol_pada_pelunasan_neto(monkeypatch):
    """Repro: DP 2 jt di luar tagihan TAK boleh mengalir jadi 'Sudah Dibayar' PELUNASAN 3 jt (halaman yang sama sudah
    menulis 'Uang muka sudah diterima -2 jt')."""
    class _C:
        async def fetch(self, sql, *a):
            return [_so_row(_pf("a", 3_000_000, _t(20), "PELUNASAN"))]

    async def atr(conn, t, so_ids, exclude_proforma_id=None):
        return {"so": PA.atribusikan([_pf("a", 3_000_000, _t(20), "PELUNASAN")], [_dp("d", 2_000_000, _t(19))])}

    async def tutup(conn, t, so_ids):
        return {"so": {"faktur": D("0"), "uang_muka_sisa": D("2000000"), "total": D("2000000")}}
    monkeypatch.setattr(PA, "muat_atribusi", atr)
    monkeypatch.setattr(PT, "tertutup_pesanan", tutup)
    r = await PT.terbayar_proforma(_C(), "t", ["so"])
    assert r["a"]["paid"] == 0 and r["a"]["paid_breakdown"]["uang_muka_di_luar_tagihan"] == 2_000_000.0


@pytest.mark.asyncio
async def test_sudah_dibayar_dp_tercocok_langsung(monkeypatch):
    class _C:
        async def fetch(self, sql, *a):
            return [_so_row(_pf("a", 3_000_000, _t(18)))]

    async def atr(conn, t, so_ids, exclude_proforma_id=None):
        return {"so": PA.atribusikan([_pf("a", 3_000_000, _t(18))], [_dp("d", 3_000_000, _t(14))])}

    async def tutup(conn, t, so_ids):
        return {"so": {"faktur": D("0"), "uang_muka_sisa": D("3000000"), "total": D("3000000")}}
    monkeypatch.setattr(PA, "muat_atribusi", atr)
    monkeypatch.setattr(PT, "tertutup_pesanan", tutup)
    r = await PT.terbayar_proforma(_C(), "t", ["so"])
    assert r["a"]["paid"] == 3_000_000 and r["a"]["paid_breakdown"]["dari_uang_muka_langsung"] == 3_000_000.0


# ---------------------------------------------------------------- PDF (celah 1)
def _html(purpose, amount, total, billed_before, received_before, tak):
    from datetime import datetime as _dt
    svc = PDFService()
    data = {"proforma_number": "PRO-1", "purpose": purpose, "amount": amount, "order_total_amount": total,
            "dp_percent_display": int(round(amount / total * 100)), "billed_before": billed_before,
            "received_before": received_before, "paid_amount": 0, "outstanding_amount": amount,
            "remaining_after_this": pf.sisa_bisa_ditagih(total, billed_before + amount, tak),
            "is_partially_paid": False, "items": [], "order_items": []}
    return svc.jinja_env.get_template("proforma.html").render(   # konteks = generate_proforma_pdf
        proforma=data, company={"name": "X"}, purpose_label=svc.PROFORMA_PURPOSE_LABELS.get(purpose, purpose),
        status_label="TERBIT", generated_at=_dt(2026, 9, 28), batal=None, draf=False)


def test_pdf_pelunasan_neto_repro():
    h = _html("PELUNASAN", 3_000_000, 5_000_000, 0, 2_000_000, 2_000_000)
    assert "Pelunasan yang Ditagih" in h and "Uang muka sudah diterima" in h and "-Rp 2.000.000" in h
    assert "Sisa setelah tagihan ini" in h and "Rp 0" in h and "Pelunasan setelah" not in h


def test_pdf_dp_kedua_sisa_memperhitungkan_tagihan_sebelumnya():
    """DP 2 (10%) sesudah DP 1 (30%) terbit & dibayar tanpa tautan -> pelunasan 3.000.000 (dulu 4.500.000)."""
    h = _html("DP", 500_000, 5_000_000, 1_500_000, 1_500_000, 0)
    assert "Uang Muka yang Ditagih" in h and "Pelunasan setelah uang muka" in h
    assert "3.000.000" in h and "4.500.000" not in h


def test_pdf_tanpa_baris_pelunasan_bila_sisa_nol():
    h = _html("TERMIN", 4_000_000, 10_000_000, 6_000_000, 6_000_000, 0)
    assert "Termin yang Ditagih" in h and "Pelunasan setelah" not in h
