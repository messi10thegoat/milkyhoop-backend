"""SO aksi_semua (21 Okt 2026, putusan MASTER A): baris + detail membawa SEMUA kunci aksi; aksi = aksi_semua[0]."""
import inspect
import os

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

from app.routers import sales_orders as SO  # noqa: E402
from app.schemas import sales_orders as SC  # noqa: E402
from app.services import so_aksi  # noqa: E402


def test_skema_baris_dan_detail_membawa_aksi_semua():
    assert SC.SalesOrderListItem.model_fields["aksi_semua"].default == []
    assert "aksi" in SC.SalesOrderDetail.model_fields and SC.SalesOrderDetail.model_fields["aksi_semua"].default == []
    # default tidak dibagi antar instans
    a, b = SC.SalesOrderListItem.model_fields["aksi_semua"], SC.SalesOrderDetail.model_fields["aksi_semua"]
    assert a.default is not None and b.default is not None


def test_router_memakai_fungsi_so_aksi_yang_sama_untuk_baris_dan_detail():
    lst = inspect.getsource(SO.list_sales_orders)
    det = inspect.getsource(SO.get_sales_order_detail)
    assert 'aksi_semua=list(_aksi_baris.get(row["id"], {}).get("aksi", []))' in lst
    assert "so_aksi.fakta(conn" in det and "aksi_semua=_aksi_det" in det and "so_aksi.aksi_utama(_aksi_det)" in det


def test_aksi_utama_adalah_elemen_pertama():
    assert so_aksi.aksi_utama(["telat_kirim", "perlu_ditagih"]) == "telat_kirim"
    assert so_aksi.aksi_utama([]) is None
    # urutan daftar mengikuti so_aksi.AKSI (utama = paling mendesak)
    semua = ["perlu_ditagih", "telat_kirim"]
    assert sorted(semua, key=so_aksi.AKSI.index)[0] == "telat_kirim"
