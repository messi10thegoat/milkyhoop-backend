"""Dokumen tagihan di bundel PO / uang muka vendor == TD.dok_tagihan (22 Okt 2026; kontrak bills BACKEND). Satu tagihan = satu status
di semua bundel: draf | batal | lunas | diposting + paid. Dulu po_daftar.dok_bill memberi "diposting" untuk tagihan lunas."""
import inspect
import uuid
from datetime import date

import pytest

from app.services import po_daftar as PD, tagihan_daftar as TD, uang_muka_vendor_daftar as UMD


def _b(keadaan):
    return {"id": uuid.uuid4(), "invoice_number": "BILL-1", "issue_date": date(2026, 10, 22), "amount": 1000, "keadaan": keadaan}


@pytest.mark.parametrize("keadaan,status", [("draft", "draf"), ("void", "batal"), ("paid", "lunas"), ("terbuka", "diposting")])
def test_dok_bill_sama_dengan_dok_tagihan(keadaan, status):
    b = _b(keadaan)
    d = PD.dok_bill(b)
    assert d == TD.dok_tagihan(b, keadaan) and d["status"] == status and d["paid"] == (keadaan == "paid")


def test_sql_dok_bill_memakai_keadaan_bundel_tagihan():
    q = PD.sql_dok_bill()
    assert TD.sql_keadaan_dok("b") in q and "b.amount" in q and "AS keadaan" in q


def test_pemanggil_memakai_join_rincian_dan_sql_dok_bill():
    for fn in (PD.dokumen_po, UMD.kelompok_untuk):
        src = inspect.getsource(fn)
        assert "sql_dok_bill()" in src and "join_rincian()" in src, fn.__name__
        assert "status_v2, b.status" not in src and "grand_total, status_v2" not in src
    assert "a.status = 'active'" in inspect.getsource(UMD.kelompok_untuk)  # penerapan dilepas bukan dokumen terkait (V437)
