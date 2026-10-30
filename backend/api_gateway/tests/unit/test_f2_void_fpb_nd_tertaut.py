"""F2 (30 Okt 2026, pagar uang; cermin SI VOID_HAS_CREDIT_NOTES): faktur pembelian TAK BOLEH dibatalkan selama punya nota debit TERTAUT lewat
vendor_credits.original_bill_id yang bukan draf/void (ND dari FPB tak punya baris vendor_credit_applications; jurnalnya tetap mendebit hutang
tagihan ini -> void tagihan = hutang vendor minus). Blok ADA_NOTA_DEBIT yang SAMA (penentu satu: pratinjau + tulis lewat TA.blok_void).
Bukti SQL di DB nyata: scripts/ops/uji-nyata-f2-void-fpb-nd.py (kaos, ROLLBACK)."""
import asyncio
import inspect

import pytest

from app.services import tagihan_aksi as TA

BILL = {"id": "b", "invoice_number": "PB-1", "status": "posted", "status_v2": "posted"}


class _K:
    """Konek boneka: baris ND dari kueri ADA_NOTA_DEBIT diberikan tes; kueri lain kosong."""
    def __init__(s, nd):
        s.nd, s.sql = nd, []

    async def fetch(s, sql, *a):
        s.sql.append((sql, a))
        return s.nd if "vendor_credits" in sql and "credit_number" in sql else []

    async def fetchval(s, sql, *a):
        return None


@pytest.mark.parametrize("nd,langkah", [
    ([{"credit_number": "VC-1", "tertaut": True}], "Batalkan nota debit itu dulu"),
    ([{"credit_number": "VC-1", "tertaut": False}], "Lepas nota debit itu dulu"),
    ([{"credit_number": "VC-1", "tertaut": False}, {"credit_number": "VC-2", "tertaut": True}], "Batalkan nota debit itu dulu"),
])
def test_blok_nd_sebut_nomor_dan_langkah(nd, langkah):
    b = asyncio.run(TA.blok_void(_K(nd), "t", BILL))
    assert [x["code"] for x in b] == ["ADA_NOTA_DEBIT"]
    m = b[0]["message"]
    assert all(r["credit_number"] in m for r in nd) and langkah in m and "PB-1" in m


def test_tanpa_nd_tak_memblok():
    assert asyncio.run(TA.blok_void(_K([]), "t", BILL)) == []


def _sql_nd():
    k = _K([])
    asyncio.run(TA.blok_void(k, "t", BILL))
    q = [(s, a) for s, a in k.sql if "vendor_credits" in s and "credit_number" in s]
    assert len(q) == 1, q
    return " ".join(q[0][0].split()), q[0][1]


def test_kueri_nd_tertaut_original_bill_id_bukan_draf_void_satu_tenant():
    s, a = _sql_nd()
    assert a == ("b", "t")
    tertaut = s.split("UNION ALL")[1]
    assert "vc.original_bill_id = $1" in tertaut and "vc.tenant_id = $2" in tertaut
    assert "vc.status NOT IN ('draft', 'void')" in tertaut and "true" in tertaut


def test_cabang_aplikasi_lama_tetap():
    s, _ = _sql_nd()
    lama = s.split("UNION ALL")[0]
    assert "vendor_credit_applications vca" in lama and "vca.bill_id = $1" in lama and "vca.status = 'active'" in lama
    assert "vca.tenant_id = $2" in lama and "false AS tertaut" in lama


def test_tulis_dan_pratinjau_lewat_penentu_satu():
    from app.routers import bills as BR
    from app.services import bills_service as BS
    assert "TA.blok_void(" in inspect.getsource(BS.BillsService.void_bill)
    assert "await blok_void(conn, tid" in inspect.getsource(TA.rencana_void) and "TA.rencana_void(" in inspect.getsource(BR)
