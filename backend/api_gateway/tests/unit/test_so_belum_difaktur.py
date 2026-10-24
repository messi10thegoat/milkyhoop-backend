"""BELUM-DIFAKTUR (24 Okt 2026, pemilik): kartu/pill laporan SO "Belum difaktur" = Q-011 belum_ditagih (turunan jurnal) − DP belum dipakai.
Satu predikat untuk ?aksi=belum_difaktur, summary belum_difaktur_count/_value, medan baris & detail; BUKAN kolom aksi baris; pemanggil
lama fakta (dashboard, pengingat) tanpa kueri jurnal tambahan."""
import ast
import inspect
import textwrap
from decimal import Decimal as D

from app.routers import sales_orders as SO
from app.schemas.sales_orders import SalesOrderDetail, SalesOrderListItem, SalesOrderSummary
from app.services import so_aksi as A


def _tubuh(fn):
    s = textwrap.dedent(inspect.getsource(fn))
    f = ast.parse(s).body[0]
    isi = f.body[1:] if ast.get_docstring(f) is not None else f.body
    return "\n".join(ast.get_source_segment(s, n) for n in isi)


def _r(invoiced, dp):
    return {"invoiced": D(invoiced), "dp_unapplied": D(dp)}


def test_angka_satu_definisi_q011_dikurangi_dp():
    b = A.angka_belum_difaktur(D("1000"), _r("300", "200"))
    assert b == {"nilai_belum_difaktur": D("700"), "dp_belum_dipakai": D("200"), "sisa_belum_difaktur": D("500")}
    # DP menutup penuh: sisa 0 (tetap perlu faktur -> dihitung kartu); faktur > total: nol (belum_ditagih min 0)
    assert A.angka_belum_difaktur(D("1000"), _r("0", "1500"))["sisa_belum_difaktur"] == 0
    assert A.angka_belum_difaktur(D("1000"), _r("1200", "0"))["nilai_belum_difaktur"] == 0
    assert A.angka_belum_difaktur(D("1000"), None) == {k: 0 for k in A.MEDAN_BELUM_DIFAKTUR}  # SO tak berjalan
    # definisi = proforma_terbayar.belum_ditagih (bukan rumus kedua)
    assert "belum_ditagih(total_amount, r)" in _tubuh(A.angka_belum_difaktur)


def _f():
    """fakta(..., laporan=True) buatan: SO1 belum (DP sebagian), SO2 tertutup DP penuh, SO3 sudah difaktur penuh, SO4 arsip."""
    def v(aksi, total, bd, lap):
        e = {"aksi": aksi, "total": D(total), "nilai": {}, "belum_difaktur": bd, "laporan": lap}
        if lap:
            e["nilai"]["belum_difaktur"] = bd["sisa_belum_difaktur"]
        return e
    b1 = A.angka_belum_difaktur(D("1000"), _r("300", "200"))
    b2 = A.angka_belum_difaktur(D("800"), _r("0", "900"))
    b3 = A.angka_belum_difaktur(D("500"), _r("500", "0"))
    return {"so1": v(["perlu_dikirim"], "1000", b1, ["belum_difaktur"]), "so2": v([], "800", b2, ["belum_difaktur"]),
            "so3": v(["perlu_dikirim"], "500", b3, []), "so4": v([], "100", A.angka_belum_difaktur(D("100"), None), [])}


def test_kartu_filter_dan_total_satu_himpunan():
    f = _f()
    r = A.ringkasan(f, laporan=True)
    ids = A.id_aksi(f, "belum_difaktur")
    assert sorted(ids) == ["so1", "so2"] and r["belum_difaktur_count"] == len(ids) == 2  # tertutup DP penuh TETAP dihitung
    assert r["belum_difaktur_value"] == 500.0 == r["sisa_belum_difaktur"]  # angka kartu = SESUDAH DP (so2 menyumbang 0)
    assert r["nilai_belum_difaktur"] == 1500.0 and r["dp_belum_dipakai"] == 1100.0
    assert sum(A.medan_belum_difaktur(f[i])["sisa_belum_difaktur"] for i in ids) == r["belum_difaktur_value"]
    # kunci aksi lama tak berubah oleh kunci laporan
    assert r["perlu_dikirim_count"] == 2 and "belum_difaktur" not in A.AKSI
    assert A.ringkasan(f) == {k: v for k, v in r.items() if k in A.ringkasan(f)}  # laporan=False: kunci lama identik, tanpa kunci laporan
    assert "belum_difaktur_count" not in A.ringkasan(f)


def test_bukan_kolom_aksi_baris_dan_kunci_sah():
    f = _f()
    assert A.aksi_utama(A.aksi_baris(f["so2"]["aksi"])) is None  # so2 hanya belum difaktur -> aksi baris TETAP kosong
    assert A.kunci_sah("belum_difaktur") and not A.kunci_sah("piutang")
    assert A.medan_belum_difaktur(None) == {k: 0.0 for k in A.MEDAN_BELUM_DIFAKTUR}


def test_fakta_laporan_opt_in_dan_pemanggil_lama_tanpa_kueri_jurnal():
    src = _tubuh(A.fakta)
    assert "if laporan and jalan:" in src and src.index("if laporan and jalan:") < src.index("ringkasan_pesanan(conn, tenant_id, jalan)")
    sig = inspect.signature(A.fakta).parameters
    assert sig["laporan"].default is False
    from app.services import dashboard_v2, pengingat
    for mod in (dashboard_v2, pengingat):
        assert "laporan=True" not in inspect.getsource(mod), mod.__name__


def test_router_filter_baris_summary_detail_memakai_fakta_laporan():
    lst = _tubuh(SO.list_sales_orders)
    assert "laporan=aksi in so_aksi.KUNCI_LAPORAN" in lst  # filter: kueri jurnal hanya bila kunci laporan diminta
    assert "[row[\"id\"] for row in rows], laporan=True)" in lst and "**so_aksi.medan_belum_difaktur(_aksi_baris.get(row[\"id\"]))" in lst
    assert "laporan=True), laporan=True)" in _tubuh(SO.get_sales_order_summary)
    det = _tubuh(SO.get_sales_order_detail)
    assert "[order[\"id\"]], laporan=True)" in det and "**so_aksi.medan_belum_difaktur(_fakta_det)" in det


def test_skema_memuat_medan_tanpa_kata_piutang():
    for m in A.MEDAN_BELUM_DIFAKTUR:
        assert m in SalesOrderListItem.model_fields and m in SalesOrderDetail.model_fields and m in SalesOrderSummary.model_fields
    assert "belum_difaktur_count" in SalesOrderSummary.model_fields and "belum_difaktur_value" in SalesOrderSummary.model_fields
    medan = set(SalesOrderListItem.model_fields) | set(SalesOrderDetail.model_fields) | set(SalesOrderSummary.model_fields)
    assert not [m for m in medan if "piutang" in m]  # pemilik 24 Okt: istilah "piutang" dilarang
