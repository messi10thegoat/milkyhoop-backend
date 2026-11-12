"""BUG-183 (putusan pemilik P1 'standar otomatis'): saat report-output, upah + overhead STANDAR (planned per unit x output baik, kumulatif) diterapkan ke WIP lewat jurnal yang SAMA dgn /labor
(PRODUCTION_LABOR Dr WIP/Cr MFG_LABOR_APPLIED 2-10430; PRODUCTION_OVERHEAD Dr WIP/Cr MFG_OVERHEAD_APPLIED 2-10440) hanya untuk KEKURANGAN terhadap yang sudah tercatat (aktual /labor menang; tak dobel;
output kedua tak mengulang). Penerimaan FG = bahan + upah + overhead. Angka WO 50 kaos = 2.615.000 = uji salinan."""
import asyncio
import inspect
import uuid
from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from app.services import produksi_terap_standar as PT


@pytest.mark.parametrize("planned,planned_qty,sudah_baik,baik,aktual,harap", [
    (475000, 50, 0, 50, 0, 475000),                  # semua output sekaligus
    (475000, 50, 0, 30, 0, 285000),                  # output pertama 30 dari 50
    (475000, 50, 30, 20, 285000, 190000),            # output kedua menutup sisa, tak mengulang
    (475000, 50, 50, 0, 475000, 0),                  # output tanpa barang baik: nol
    (100000, 50, 0, 50, 20000, 80000),               # /labor sebagian (20.000) -> standar menutup KEKURANGAN saja
    (100000, 50, 0, 50, 200000, 0),                  # aktual lebih besar dari standar -> aktual menang, nol standar
    (0, 50, 0, 50, 0, 0),                            # BOM tanpa upah/overhead -> nol
    (475000, 50, 0, 60, 0, 570000),                  # kelebihan output (60 > 50): standar per unit x 60
    (100, 3, 0, 1, 0, 33),                           # pembulatan rupiah (33,33 -> 33)
    (100, 3, 1, 1, 33, 34),                          # kumulatif 66,67 -> 67 - 33 = 34 (total 67, tak hilang/dobel)
])
def test_kekurangan_standar(planned, planned_qty, sudah_baik, baik, aktual, harap):
    assert PT.kekurangan_standar(D(planned), D(planned_qty), D(sudah_baik), D(baik), D(aktual)) == harap


def test_planned_qty_nol_tidak_meledak():
    assert PT.kekurangan_standar(D(100), D(0), D(0), D(5), D(0)) == 0


class Kon:
    def __init__(self):
        self.sql = []

    async def fetchval(self, sql, *a):
        self.sql.append((" ".join(sql.split()), a))
        return "JV-LB-2611-0001"

    async def execute(self, sql, *a):
        self.sql.append((" ".join(sql.split()), a))


def test_jurnal_sama_dengan_labor(monkeypatch):
    peran = {}

    async def resolve(conn, tid, role):
        peran[str(role)] = 1
        return uuid.uuid4()
    monkeypatch.setattr(PT, "resolve_account_id_by_role", resolve)
    k = Kon()
    asyncio.run(PT.terapkan_ke_wip(k, tenant_id="t", user_id="u", order_id="o1", order_number="WO-1", jenis="labor", jumlah=475000, tanggal="2026-11-12"))
    asyncio.run(PT.terapkan_ke_wip(k, tenant_id="t", user_id="u", order_id="o1", order_number="WO-1", jenis="overhead", jumlah=100000, tanggal="2026-11-12"))
    ins = [(s, a) for s, a in k.sql if s.startswith("INSERT INTO journal_entries")]
    assert [a[5] for _, a in ins] == ["PRODUCTION_LABOR", "PRODUCTION_OVERHEAD"]
    assert [a[7] for _, a in ins] == [475000, 100000]
    nomor = [a for s, a in k.sql if "get_next_journal_number" in s]
    assert [a[1] for a in nomor] == ["JV-LB", "JV-OH"]                       # penomoran/seri SAMA dgn /labor
    baris = [a for s, a in k.sql if s.startswith("INSERT INTO journal_lines")]
    assert len(baris) == 4 and baris[0][3] == 475000 and baris[1][3] == 475000 and baris[2][3] == 100000
    assert sum(1 for s, _ in k.sql if s.startswith("UPDATE journal_entries SET status = 'POSTED'")) == 2    # DRAFT -> baris -> POSTED
    assert any("MFG_LABOR_APPLIED" in p for p in peran) and any("MFG_OVERHEAD_APPLIED" in p for p in peran) and any("WIP_GENERIC" in p for p in peran)


def test_nol_tidak_menerbitkan_jurnal(monkeypatch):
    k = Kon()
    asyncio.run(PT.terapkan_ke_wip(k, tenant_id="t", user_id="u", order_id="o1", order_number="WO-1", jenis="labor", jumlah=0, tanggal="2026-11-12"))
    assert k.sql == []


def test_report_output_menerapkan_standar_sebelum_hitung_biaya_satuan():
    import app.routers.production as P
    s = inspect.getsource(P.report_output)
    i_terap = s.index("PT.kekurangan_standar")
    assert s.index("PT.terapkan_ke_wip") > i_terap and i_terap < s.index("total_actual = (")
    assert s.count("PT.terapkan_ke_wip(") == 2 and "actual_labor_cost = COALESCE(actual_labor_cost, 0) + $" in s


def test_labor_memakai_helper_yang_sama():
    import app.routers.production as P
    s = inspect.getsource(P.record_labor)
    assert s.count("PT.terapkan_ke_wip(") == 2 and "INSERT INTO journal_entries" not in s
