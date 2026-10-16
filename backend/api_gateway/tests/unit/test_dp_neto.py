"""KONTRAK-dp-neto-20261016: uang muka pesanan NETO refund, turunan jurnal, SATU sumber (services/uang_muka_pesanan).
Harness nyata (grapgrap lama == baru, kaos SO-2609-0331 / -0003, lengan tulis ROLLBACK): scripts/ops/uji-nyata-dp-neto.sh."""
import inspect
import uuid
from decimal import Decimal as D

from app.routers import customer_deposits as CD
from app.routers import proformas as PF
from app.services import proforma_atribusi as PA
from app.services import proforma_terbayar as PT
from app.services import so_ubah_terkonfirmasi as SU
from app.services import uang_muka_pesanan as U

S1, S2 = uuid.uuid4(), uuid.uuid4()


def _dp(so, status="posted", amount="0", terima="0", terap="0", lain="0", proforma=None):
    d = {"id": uuid.uuid4(), "so_id": so, "status": status, "amount": D(amount), "proforma_id": proforma,
         "terima": D(terima), "terap": D(terap), "lain": D(lain)}
    d["neto"] = d["terima"] - d["lain"]
    return d


def test_jumlahkan_neto_refund_draf_terpisah():
    b = [_dp(S1, amount="50000", terima="50000", lain="10000"),
         _dp(S1, amount="20000", terima="20000", terap="20000", proforma=uuid.uuid4()),
         _dp(S1, status="draft", amount="1000"),
         _dp(S2, amount="5", terima="5")]
    u = U.jumlahkan(b, [S1, S2])[S1]
    assert (u["diterima"], u["dikembalikan"], u["neto"], u["diterapkan"], u["draf"], u["neto_tanpa_proforma"]) == \
        (D("70000"), D("10000"), D("60000"), D("20000"), D("1000"), D("40000"))
    assert U.jumlahkan(b, [S1], exclude_id=b[0]["id"])[S1]["neto"] == D("20000")


def test_pembungkus_memakai_satu_sumber_tanpa_sum_amount():
    for fn in (CD.received_total_for_order, PF.deposit_totals_for_order):
        src = inspect.getsource(fn)
        assert "uang_muka_pesanan" in src and "FROM customer_deposits" not in src, fn.__name__
    assert "per_uang_muka" in inspect.getsource(PA.muat_atribusi)
    assert "FROM customer_deposits" not in inspect.getsource(PA.muat_atribusi)
    assert "per_uang_muka" in inspect.getsource(PT.ringkasan_pesanan)
    b = inspect.getsource(SU.batas_bawah_total)
    assert 'uang_muka_pesanan(conn, tenant_id, [so_id]))[so_id]["neto"]' in b and "received_total_for_order" not in b


def test_plafon_neto_tambah_draf_kwitansi_neto_saja():
    assert 'return float(u["neto"] + u["draf"])' in inspect.getsource(CD.received_total_for_order)
    src = inspect.getsource(CD.muat_pdf_kwitansi_uang_muka)
    assert '["neto"]' in src and "received_total_for_order" not in src


def test_atribusi_nominal_neto_tertaut_tetap_lepas_nol_keluar():
    src = inspect.getsource(PA.muat_atribusi)
    assert '"amount": d["neto"]' in src
    assert '(d["neto"] > 0 or d["proforma_id"] is not None)' in src
