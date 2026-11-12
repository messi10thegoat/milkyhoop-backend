"""BUG-183b (putusan pemilik, persis Accurate): FG = STANDAR BOM x output baik kumulatif (+ subkontrak aktual proporsional), selisih bahan/konversi tertinggal di WIP per WO; month_end_reconcile
menguras sisa WIP WO SELESAI ke 5-90200 (jurnal PRODUCTION_RECONCILE yang sama, memo per WO, idempoten). Definisi 'selesai' = SATU penentu. Paritas angka (523.000/2.615.000) = uji salinan."""
import asyncio
import datetime as dt
import inspect
import uuid
from decimal import Decimal as D

import pytest

import app.routers.production as P
from app.services import produksi_terap_standar as PT
from app.services import produksi_wip_sisa as PW

AKHIR = dt.date(2026, 11, 30)


@pytest.mark.parametrize("planned,qty,sebelum,baik,dikredit,sub,harap", [
    (2615000, 50, 0, 50, 0, 0, 2615000),                       # WO 50 TESTER
    (523000, 10, 0, 10, 0, 0, 523000),                         # WO 10: bahan aktual lebih TIDAK menaikkan FG (planned standar saja)
    (523000, 10, 0, 4, 0, 0, 209200),                          # output sebagian
    (523000, 10, 4, 6, 209200, 0, 313800),                     # output kedua: kumulatif - yang dikredit (tak dobel)
    (100, 3, 0, 1, 0, 0, 33), (100, 3, 1, 1, 33, 0, 34), (100, 3, 2, 1, 67, 0, 33),    # pembulatan kumulatif: total 100 persis
    (523000, 10, 0, 10, 600000, 0, 0),                         # WO lama (output aktual > standar): clamp >= 0
    (523000, 10, 3, 7, 100000, 0, 423000),                     # WO lama sebagian: hanya selisih ke standar
    (523000, 10, 0, 10, 0, 40000, 563000),                     # subkontrak aktual ikut (1x) penuh
    (523000, 10, 0, 5, 0, 40000, 281500),                      # subkontrak proporsional (setengah)
    (523000, 10, 0, 12, 0, 40000, 627600 + 0),                 # overrun 12 dari 10: standar x 12 (627.600), subkontrak maks 1x (40.000) = 667.600
    (0, 10, 0, 10, 0, 0, 0), (100, 0, 0, 1, 0, 0, 0),
])
def test_nilai_fg_standar(planned, qty, sebelum, baik, dikredit, sub, harap):
    if (planned, qty, baik, sub) == (523000, 10, 12, 40000):
        harap = 667600
    assert PT.nilai_fg_standar(D(planned), D(qty), D(sebelum), D(baik), D(dikredit), D(sub)) == harap


@pytest.mark.parametrize("status,baik,rencana,tgl,harap", [
    ("completed", 10, 10, dt.date(2026, 11, 20), True),                  # status completed s/d akhir periode
    ("completed", 10, 10, dt.date(2026, 12, 2), True),                   # selesai Des tapi output baik s/d 30 Nov sudah >= rencana
    ("completed", 5, 10, dt.date(2026, 12, 2), False),                   # completed baru di periode berikutnya, output Nov belum penuh -> belum dikuras
    ("in_progress", 10, 10, None, True), ("in_progress", 12, 10, None, True),   # output baik >= rencana (tanpa /complete)
    ("in_progress", 9, 10, None, False), ("released", 0, 10, None, False), ("draft", 0, 10, None, False),
    ("cancelled", 10, 10, dt.date(2026, 11, 1), False),                  # batal tak pernah dikuras
    ("completed", 0, 10, None, True),                                    # completed tanpa tanggal (data lama)
    ("in_progress", 5, 0, None, False),
])
def test_wo_selesai_per(status, baik, rencana, tgl, harap):
    assert PT.wo_selesai_per(status, baik, rencana, tgl, AKHIR) is harap


def test_memo_kuras_bisa_dibaca_balik():
    oid = uuid.uuid4()
    m = PW.memo_kuras("WO-2026-000014", oid)
    assert m.startswith(PW.AWAL_MEMO) and PW._ID_DI_MEMO.search(m).group(1) == str(oid)


class Kon:
    """fetch palsu: WO, saldo WIP per WO, baris kuras lama."""

    def __init__(self, wo, saldo, kuras=()):
        self.wo, self.saldo, self.kuras, self.sql = wo, saldo, list(kuras), []

    async def fetch(self, sql, *a):
        s = " ".join(sql.split())
        self.sql.append((s, a))
        if "FROM production_orders po" in s:
            return self.wo
        if "GROUP BY je.source_id" in s:
            return [{"wo": k, "s": v} for k, v in self.saldo.items() if k in a[3]]
        if "je.source_type = 'PRODUCTION_RECONCILE'" in s:
            return self.kuras
        raise AssertionError(s)


def _wo(oid, nomor, status="completed", baik=10, rencana=10, tgl=dt.date(2026, 11, 20)):
    return {"id": oid, "order_number": nomor, "status": status, "planned_quantity": rencana, "actual_end_date": tgl, "baik": baik}


def jalan(k):
    return asyncio.run(PW.sisa_wip_selesai(k, "t1", "wip", AKHIR))


def test_sisa_hanya_wo_selesai_dan_tak_nol():
    a, b, c, d = (uuid.uuid4() for _ in range(4))
    k = Kon([_wo(a, "WO-A"), _wo(b, "WO-B", "in_progress", 5, 10, None), _wo(c, "WO-C"), _wo(d, "WO-D", "cancelled", 10, 10)],
            {str(a): D("40000"), str(b): D("999"), str(c): D("0")})
    h = jalan(k)
    assert [(x["order_number"], x["sisa"]) for x in h] == [("WO-A", D("40000.00"))]          # B belum selesai (dibiarkan), C WIP 0 (tak disentuh), D batal
    wipsql = [s for s, _ in k.sql if "GROUP BY je.source_id" in s][0]
    assert "is_effective_journal(je.id)" in wipsql and "je.tenant_id = $1" in wipsql and "je.journal_date <= $3" in wipsql


def test_wo_sudah_dikuras_tak_disentuh_lagi():
    a = uuid.uuid4()
    k = Kon([_wo(a, "WO-A")], {str(a): D("40000")}, kuras=[{"memo": PW.memo_kuras("WO-A", a), "debit": 0, "credit": D("40000")}])
    assert jalan(k) == []


def test_sisa_kredit_dan_kuras_sebagian():
    a, b = uuid.uuid4(), uuid.uuid4()
    k = Kon([_wo(a, "WO-A"), _wo(b, "WO-B")], {str(a): D("-75000"), str(b): D("100000")}, kuras=[{"memo": PW.memo_kuras("WO-B", b), "debit": 0, "credit": D("30000")}])
    h = {x["order_number"]: x["sisa"] for x in jalan(k)}
    assert h == {"WO-A": D("-75000.00"), "WO-B": D("70000.00")}


def test_report_output_fg_standar_bukan_aktual():
    s = inspect.getsource(P.report_output)
    assert "PT.nilai_fg_standar(" in s and "total_actual" not in s and "_output_terakhir" not in s and "OUTPUT_SISA" not in s
    assert s.index("PT.terapkan_ke_wip(") < s.index("PT.nilai_fg_standar(")
    assert 'order["planned_material_cost"]' in s and "subcontract_cost" in s
    assert "PT.nilai_fg_standar(_planned_std, planned, _baik_sebelum, _baik_kini, _sudah_dikredit, subcontract_cost)" in s     # nilai dari STANDAR (planned), bukan aktual


def test_month_end_menguras_wip_di_jurnal_yang_sama():
    s = inspect.getsource(P.month_end_reconcile)
    assert "PW.sisa_wip_selesai(conn, tenant_id, _wip_id, p_end)" in s and "PW.memo_kuras(" in s
    assert "not labor_active and not oh_active and not wip_sisa" in s
    assert '"wip_sisa":' in s and '"wip_dikuras":' in s
    # baris kuras: sisa > 0 -> Cr WIP / Dr varian; sisa < 0 -> Dr WIP / Cr varian
    assert "lines.append((_wip_id, ZERO, w[\"sisa\"], memo_w))" in s and "lines.append((_wip_id, -w[\"sisa\"], ZERO, memo_w))" in s
