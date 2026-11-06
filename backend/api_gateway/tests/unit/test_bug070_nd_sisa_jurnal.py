"""BUG-070 (6 Nov 2026): remaining_amount nota debit = sisa JURNAL (Iron Law 1/16), bukan cache total − amount_applied − amount_received.
nyawit VC-2611-0001 (bertaut ke tagihan, amount_applied 0) tampil "Terpakai" tetapi sisa penuh 463.536. Kini satu penentu
NDD.sisa_nd_baris (draf = total, preseden NK; selain itu = nota_debit.sql_sisa_nd) lewat medan_baris -> daftar, detail, ekspor sama;
summary available_balance = Σ sql_sisa_nd (draf/void/bertaut 0)."""
import asyncio
import inspect
import os
from decimal import Decimal as D
from uuid import uuid4

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

import pytest  # noqa: E402

from app.services import nota_debit as ND  # noqa: E402
from app.services import nota_debit_daftar as NDD  # noqa: E402
from app.services import bulk_export_beli as BEB  # noqa: E402
from app.routers import vendor_credits as VR  # noqa: E402

BILL = "6b3c3b1e-0000-4000-8000-000000000001"


def _src(fn):
    return " ".join(inspect.getsource(fn).split())


def _v(status, total=1000, dipakai=0, kembali=0, bill=None):
    return {"id": uuid4(), "status": status, "total_amount": D(total), "amount_applied": D(dipakai), "amount_received": D(kembali),
            "original_bill_id": bill, "original_bill_number": None}


# cache trigger (applied/received) SENGAJA tak cocok dengan jurnal: remaining wajib ikut jurnal di SEMUA keadaan terbit
@pytest.mark.parametrize("status,dipakai,kembali,bill,sisa_jurnal", [
    ("posted", 0, 0, None, "1000.00"),        # utuh
    ("partial", 300, 0, None, "700.00"),      # sebagian dipakai
    ("partial", 0, 250, None, "750.00"),      # sebagian dikembalikan
    ("applied", 1000, 0, None, "0"),          # terpakai habis
    ("applied", 0, 1000, None, "0"),          # dikembalikan habis
    ("posted", 0, 0, BILL, "0"),              # BERTAUT (VC-2611-0001): amount_applied 0, rumus lama = total
    ("partial", 200, 0, None, "650.00"),      # cache basi vs jurnal -> jurnal menang
    ("void", 0, 0, None, "0"),
])
def test_paritas_remaining_sama_dengan_sisa_jurnal_semua_keadaan_terbit(status, dipakai, kembali, bill, sisa_jurnal):
    m = NDD.medan_baris(_v(status, 1000, dipakai, kembali, bill), D(sisa_jurnal), None, [], None)
    assert m["remaining_amount"] == D(sisa_jurnal)


def test_draf_remaining_sama_dengan_total_eksplisit():
    assert NDD.medan_baris(_v("draft", 463536), D("0"), None, [], None)["remaining_amount"] == D("463536")


def test_bertaut_terpakai_tanpa_sisa_kasus_nyawit():
    m = NDD.medan_baris(_v("posted", 463536, 0, 0, BILL), D("0"), None, [], None)
    assert (m["status_text"], m["remaining_amount"], m["aksi"]) == ("Terpakai", D("0"), None)


class _Conn:
    def __init__(self, sisa):
        self.sisa = sisa

    async def fetch(self, sql, *a):
        assert ND.sql_sisa_nd() in sql  # sisa dari ungkapan jurnal yang SAMA dengan ?aksi= / summary
        return [{"id": i, "sisa": self.sisa[i]} for i in a[1]]


def test_medan_banyak_mengisi_remaining_dari_kueri_sisa_jurnal(monkeypatch):
    async def _kosong(*a, **k):
        return {}
    for f in ("rincian_nd_many", "kelompok_nd", "po_asal"):
        monkeypatch.setattr(NDD, f, _kosong)
    rows = [_v("posted", 463536, 0, 0, BILL), _v("partial", 1000, 300), _v("draft", 500)]
    c = _Conn({rows[0]["id"]: D("0"), rows[1]["id"]: D("700"), rows[2]["id"]: D("0")})
    m = asyncio.run(NDD.medan_banyak(c, "t", rows))
    assert [m[r["id"]]["remaining_amount"] for r in rows] == [D("0"), D("700"), D("500")]


def test_daftar_detail_ekspor_mengambil_remaining_dari_medan_baris():
    daftar, detail, ekspor = _src(VR.list_vendor_credits), _src(VR.get_vendor_credit), _src(BEB._nota_debit)
    assert '"remaining_amount"' not in daftar and "it.update(_medan.get(row[\"id\"], {}))" in daftar
    assert '"remaining_amount": _medan["remaining_amount"]' in detail
    assert 'sisa = m["remaining_amount"]' in ekspor
    for s in (daftar, detail, ekspor):
        assert "sisa_lama" not in s and "amount_applied\"]) -" not in s
    assert not hasattr(NDD, "sisa_lama")


def test_summary_available_balance_sigma_sisa_jurnal():
    s = _src(VR.get_vendor_credits_summary)
    assert "COALESCE(SUM(__SISA__), 0) as available_balance" in s and 'query.replace("__SISA__", ND.sql_sisa_nd("vc"))' in s
    assert "journal_value - COALESCE(vc.amount_applied" not in s
    assert s.index('replace("__SISA__"') < s.index("await conn.fetchrow(query")
