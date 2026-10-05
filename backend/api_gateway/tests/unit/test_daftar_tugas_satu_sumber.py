"""Kartu dashboard 'Perlu dikerjakan' dan daftar tujuannya = SATU sumber (MASTER 5 Okt 2026, temuan pemilik).
Kartu (susun_tugas) dan filter daftar ?tugas= (/sales-invoices, /sales-orders) memanggil PEMILIH yang sama
(dashboard_v2.pilih_* / so_harus_kirim / so_dp_belum lewat id_tugas) -- bukan salinan predikat. Kesetaraan angka
kartu == baris daftar atas data nyata kaos/grapgrap dibuktikan harness tx-ROLLBACK (/root/uji_tugas.py)."""
import inspect
from datetime import date
from decimal import Decimal

from app.routers import sales_invoices as SI, sales_orders as SO
from app.services import dashboard_v2 as DV

HARI = date(2026, 10, 5)


def _ar(no, sisa, due):
    return {"invoice_id": no, "invoice_number": no, "customer_id": "c", "customer_name": "P",
            "outstanding": Decimal(sisa), "due_date": due}


def test_pemilih_murni():
    ar = [_ar("A", "100", date(2026, 10, 1)), _ar("B", "100", None), _ar("C", "0", date(2026, 9, 1)),
          _ar("D", "50", HARI), _ar("E", "50", date(2026, 10, 9))]
    assert [r["invoice_id"] for r in DV.pilih_faktur_telat(ar, HARI)] == ["A", "B"]  # due NULL = telat, sisa 0 bukan
    assert [r["invoice_id"] for r in DV.pilih_jatuh_tempo_hari_ini(ar, HARI)] == ["D"]
    assert DV.akhir_minggu(HARI) == date(2026, 10, 11) and DV.akhir_minggu(date(2026, 10, 6)) == date(2026, 10, 11)


def test_kartu_memakai_pemilih_yang_sama():
    s = inspect.getsource(DV.susun_tugas)
    assert "pilih_faktur_telat(ar, hari_ini)" in s and "pilih_jatuh_tempo_hari_ini(ar, hari_ini)" in s
    assert '_jatuh_tempo_lewat(r["due_date"]' not in s and 'r["due_date"] == hari_ini' not in s  # bukan salinan
    t = inspect.getsource(DV.tugas_tenant)
    assert "so_harus_kirim(conn, tenant_id, akhir_minggu(hari_ini))" in t and "so_dp_belum(conn, tenant_id)" in t
    i = inspect.getsource(DV.id_tugas)
    for x in ("pilih_faktur_telat", "pilih_jatuh_tempo_hari_ini", "so_harus_kirim(conn, tenant_id, akhir_minggu(hari_ini))",
              "so_dp_belum(conn, tenant_id)", 'compute_ar_outstanding'):
        assert x in i, x


def test_daftar_memanggil_id_tugas():
    for f, kolom in ((SI.list_invoices, "si.id"), (SO.list_sales_orders, "id")):
        s = inspect.getsource(f)
        assert f'conditions.append(f"{kolom} = ANY(${{param_idx}}::uuid[])")' in s and "await id_tugas(conn, ctx[\"tenant_id\"], tugas," in s
    assert inspect.signature(SI.list_invoices).parameters["tugas"].annotation is not None
    assert set(DV.TUGAS_FAKTUR) == {"telat", "jatuh_tempo_hari_ini", "belum_lunas"} and set(DV.TUGAS_SO) == {"harus_kirim", "dp_belum_diterima", "menunggu_tagih", "selesai"}


def test_aksi_kartu_membawa_kunci_tugas():
    t = DV.susun_tugas(hari_ini=HARI, ar_rows=[_ar("A", "100", date(2026, 10, 1)) | {"customer_id": "c"}], ap_rows=[],
                       so_kirim_rows=[], so_dp_rows=[{"id": "s1", "order_number": "SO-1", "dp_wajib": Decimal("10")}],
                       rekon_rows=[], kontak={}, rekening=None, usaha="U")
    payload = {x["type"]: [a["payload"] for a in x["actions"] if a["kind"] == "open_list"] for x in t}
    assert payload["ar_overdue"][0]["tugas"] == "telat" and payload["so_dp_pending"][0]["tugas"] == "dp_belum_diterima"
