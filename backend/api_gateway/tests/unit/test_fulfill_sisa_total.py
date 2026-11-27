"""Kirim faktur: sisa baris dinilai atas TOTAL diminta (semua potongan per batch) pada potongan pertama -- pesan jujur 'diminta 4', bukan potongan '2'."""
import inspect

from app.routers import sales_invoices as R


def test_total_dihitung_sebelum_pecah_dan_dinilai_di_potongan_pertama():
    src = inspect.getsource(R._execute_fulfillment)
    assert src.index("total_minta[") < src.index("pecah_kirim(")
    assert src.index("total_minta.pop(") < src.index("if req_qty > remaining_qty")
    assert "diminta {tg.qty(_total)}" in src
