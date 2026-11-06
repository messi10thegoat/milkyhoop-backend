"""BUG-053 (6 Nov 2026, putusan pemilik LANGSUNG: tanpa flag, semua tenant; selisih -> 5-10400): retur pembelian barang dilacak = pola
PERPETUAL (Accurate PRT/JV, SAP/NetSuite vendor return). SATU jurnal VENDOR_CREDIT: Dr Hutang / Cr Persediaan qty x WAC / Cr-Dr 5-10400 selisih
(porsi nilai retur barang itu − biaya WAC) / Cr 5-10300 hanya sisa baris non-stok. Dulu: Cr 5-10300 seluruhnya + jurnal VENDOR_CREDIT_COGS
Dr HPP / Cr Persediaan (pola periodik; HPP & Retur sama-sama membengkak, nyawit VC-2611-0001 463.536). Kartu stok keluar bertaut jurnal posting;
void membaliknya bertaut jurnal pembalik (ND lama tetap lewat jurnal HPP-nya). Data lama tak diubah (Law 8)."""
import asyncio
import inspect
import os
from decimal import Decimal as D
from uuid import uuid4

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

import pytest  # noqa: E402

from app.services import inventory_helpers as IH  # noqa: E402
from app.services import nota_debit as ND  # noqa: E402
from app.services import nota_debit_rencana as NDR  # noqa: E402
from app.routers import vendor_credits as RVC  # noqa: E402

AP, RETUR, PERS, PPV, PPN = "AP", "5-10300", "1-10600", "5-10400", "PPN"


def _src(fn):
    return " ".join(inspect.getsource(fn).split())


def _retur(baris, nilai, akun_selisih=PPV):
    """baris = [(id, total_wac, akun)]; nilai = {id: nilai baris dokumen}."""
    b = [{"item_row_id": i, "total": D(t), "akun": a} for i, t, a in baris]
    stok = {i for i, _, a in baris if a}
    return {"baris": b, "bagian": sum((D(v) for i, v in nilai.items() if i in stok), D(0)), "semua": sum((D(v) for v in nilai.values()), D(0)),
            "akun_selisih": akun_selisih}


class _K:
    pkp = False


def _vc(total, pajak=0):
    return {"id": uuid4(), "credit_number": "VC-1", "total_amount": total, "tax_amount": pajak, "vendor_name": "V"}


def _posting(monkeypatch, vc, retur, potongan=False, pkp=False):
    async def akun_pkp(conn, tid, role):
        return PPN if pkp else None
    monkeypatch.setattr(NDR, "resolve_account_id_by_role_if_pkp", akun_pkp)
    b = asyncio.run(NDR.baris_posting_nd(_K(), "t", vc, AP, RETUR, potongan, retur))
    assert sum(D(str(x[1])) for x in b) == sum(D(str(x[2])) for x in b)   # Law 4: baris seimbang
    return {(x[0], x[4]): (D(str(x[1])), D(str(x[2]))) for x in b}


def test_nyawit_vc_2611_0001_hutang_lawan_persediaan_tanpa_hpp(monkeypatch):
    j = _posting(monkeypatch, _vc(463536), _retur([("a", "463536.00", PERS)], {"a": 463536}))
    assert j == {(AP, "hutang"): (D(463536), D(0)), (PERS, "persediaan"): (D(0), D(463536))}   # == Accurate PRT.2026.11.00001


@pytest.mark.parametrize("wac,arah", [("400000.00", "kredit"), ("500000.00", "debit")])
def test_selisih_nilai_retur_vs_wac_ke_5_10400(monkeypatch, wac, arah):
    j = _posting(monkeypatch, _vc(463536), _retur([("a", wac, PERS)], {"a": 463536}))
    sel = D(463536) - D(wac)
    assert j[(PERS, "persediaan")] == (D(0), D(wac))
    assert j[(PPV, "selisih")] == ((D(0), sel) if arah == "kredit" else (-sel, D(0)))
    assert (RETUR, "kredit") not in j


def test_baris_non_stok_tetap_retur_pembelian_proporsional(monkeypatch):
    j = _posting(monkeypatch, _vc(400), _retur([("a", "250.00", PERS)], {"a": 300, "jasa": 100}))
    assert j[(PERS, "persediaan")] == (D(0), D(250)) and j[(PPV, "selisih")] == (D(0), D(50)) and j[(RETUR, "kredit")] == (D(0), D(100))


def test_dua_akun_persediaan_dikelompokkan(monkeypatch):
    j = _posting(monkeypatch, _vc(300), _retur([("a", "100.00", PERS), ("b", "150.00", "1-10601"), ("c", "50.00", PERS)],
                                               {"a": 100, "b": 150, "c": 50}))
    assert j[(PERS, "persediaan")] == (D(0), D(150)) and j[("1-10601", "persediaan")] == (D(0), D(150)) and (PPV, "selisih") not in j


@pytest.mark.parametrize("retur,potongan", [(None, False), (_retur([("a", "100.00", None)], {"a": 100}), False), (None, True)])
def test_tanpa_barang_ber_akun_atau_potongan_perilaku_lama(monkeypatch, retur, potongan):
    j = _posting(monkeypatch, _vc(100), retur, potongan)
    assert set(j) == {(AP, "hutang"), (RETUR, "kredit")} and j[(RETUR, "kredit")] == (D(0), D(100))


def test_pkp_ppn_tetap_baris_sendiri(monkeypatch):
    j = _posting(monkeypatch, _vc(1110, 110), _retur([("a", "1000.00", PERS)], {"a": 1110}), pkp=True)
    assert j[(PPN, "pajak")] == (D(0), D(110)) and j[(PERS, "persediaan")] == (D(0), D(1000)) and (PPV, "selisih") not in j


class _KH:
    def __init__(self):
        self.exec = []

    async def fetchval(self, sql, *a):
        return D(10) if "SUM(quantity_in - quantity_out)" in sql else D("33.33")

    async def execute(self, sql, *a):
        self.exec.append((sql, a))


def test_outbound_jurnal_pemanggil_tanpa_jurnal_hpp_dan_kartu_bertaut(monkeypatch):
    async def biaya(conn, tid, pid):
        return {"unit_cost": D("33.33"), "cost_source": "wac"}
    monkeypatch.setattr(IH, "get_cost_for_sale", biaya)
    k, jid = _KH(), uuid4()
    r = asyncio.run(IH.record_inventory_outbound(k, "t", uuid4(), "X", "X", uuid4(), 3, "VENDOR_CREDIT", uuid4(), "VC-1", None, "uji",
                                                 receipt_date=__import__("datetime").date(2026, 11, 6), jurnal_pemanggil=jid))
    assert r["journal_id"] == jid and r["total_cost"] == D("99.99")
    assert not any("INSERT INTO journal_entries" in s or "INSERT INTO journal_lines" in s for s, _ in k.exec)
    assert ("UPDATE inventory_ledger SET journal_id = $1 WHERE id = $2", (jid, r["ledger_id"])) in [(" ".join(s.split()), a) for s, a in k.exec]


def test_penulis_satu_jurnal_kartu_bertaut_dan_pagar_gl_sama_kartu():
    s = _src(RVC.post_vendor_credit)
    assert 'NDR.baris_posting_nd(conn, ctx["tenant_id"], vc, ap_account_id, purchase_return_account_id, potongan, _r["retur"])' in s
    assert 'for _b in (_r["retur"] or {}).get("baris", [])' in s and "jurnal_pemanggil=journal_id" in s
    assert 'if _b["akun"] and _g["total_cost"] != _b["total"]' in s
    assert s.index("UPDATE journal_entries SET status = 'POSTED'") < s.index("record_inventory_outbound(")


def test_pratinjau_dan_penulis_penentu_sama_tanpa_jurnal_hpp():
    s = _src(NDR.rencana_posting)
    assert "retur = await rencana_retur(conn, tid, keluar, baris_dok)" in s and "baris_posting_nd(conn, tid, vc, ap, akun_kredit, potongan, retur)" in s
    assert "VENDOR_CREDIT_COGS" not in s and "stok = stok_retur(retur)" in s and "PURCHASE_PRICE_VARIANCE" in _src(NDR.rencana_retur)


def test_void_membalik_kartu_bertaut_jurnal_posting():
    v = _src(RVC.void_vendor_credit)
    assert 'ND.balik_hpp_stok_pajak(conn, ctx, vc, await tanggal_dokumen(conn, ctx["tenant_id"]), reversal_journal_id)' in v
    b = _src(ND.balik_hpp_stok_pajak)
    assert 'reversal_journal_id=jurnal_balik' in b and 'journal_id=vc["journal_id"]' in b
    assert b.index("source_type = 'VENDOR_CREDIT_COGS'") < b.index("if jurnal_balik and vc[\"journal_id\"]")   # ND lama: jalur HPP tetap
