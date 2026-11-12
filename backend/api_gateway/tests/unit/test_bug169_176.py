"""BUG-169 (Tinjau terbit faktur: 2-10750 D/K sama = DUA jurnal sungguhan INVOICE + INVOICE_REVENUE, neto 0, salah baca bukan salah uang) -> `ringkas_per_akun` + `akun_nol_netto`
(tampilan saja; posting + `journals` tak berubah; putusan MASTER 12 Nov 'ya A'). BUG-176 (nomor jurnal biaya JV-EXP-EXP-...) -> nomor jurnal = nomor dokumen, cadangan JV-EXP,
data lama tak diubah. Paritas data nyata (jurnal terbit nyawit/kaos, neto per akun == SQL) = uji salinan scripts/ops/uji-salinan-bug169-176.sh."""
import asyncio
import inspect
import random
import uuid
from decimal import Decimal as D
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from app.routers import expenses as EX
from app.routers import sales_invoices as SI
from app.services import faktur_terbit_rencana as FTR
from app.services import nomor_jurnal as NJ

T = "pt-kita-nyawit-bersama"


def L(kode, nama, d=0, k=0):
    return {"account_code": kode, "account_name": nama, "debit": float(d), "credit": float(k), "memo": "x"}


AR, DM, PPN, JUAL, ONGKIR, DP = ("1-10200", "Piutang Usaha"), ("2-10750", "Pendapatan Diterima Dimuka"), ("2-10400", "PPN Keluaran"), ("4-10100", "Penjualan"), \
    ("4-20100", "Pendapatan Ongkos Kirim"), ("2-10500", "Uang Muka Pelanggan")


def jv(total, pajak=0, ongkir=0):
    baris = [L(*AR, d=total), L(*DM, k=total - pajak - ongkir)]
    if ongkir:
        baris.append(L(*ONGKIR, k=ongkir))
    if pajak:
        baris.append(L(*PPN, k=pajak))
    return {"source_type": "INVOICE", "lines": baris}


def recog(jumlah):
    return {"source_type": "INVOICE_REVENUE", "lines": [L(*DM, d=jumlah), L(*JUAL, k=jumlah)]}


# ---------------- BUG-169
def test_faktur_10rb_tanpa_ppn_dimuka_nol_netto_disembunyikan():
    ringkas, nol = FTR.ringkas_per_akun([jv(10000), recog(10000)])
    assert ringkas == [{"account_code": "1-10200", "account_name": "Piutang Usaha", "debit": 10000.0, "credit": 0.0},
                       {"account_code": "4-10100", "account_name": "Penjualan", "debit": 0.0, "credit": 10000.0}]
    assert nol == [{"account_code": "2-10750", "account_name": "Pendapatan Diterima Dimuka"}]       # bukan uang muka; dilaporkan supaya FE memberi catatan


def test_ppn_dan_ongkir_tetap_terlihat_seimbang():
    ringkas, nol = FTR.ringkas_per_akun([jv(11100 + 500, pajak=1100, ongkir=500), recog(10000)])
    per = {r["account_code"]: (r["debit"], r["credit"]) for r in ringkas}
    assert per == {"1-10200": (11600.0, 0.0), "2-10400": (0.0, 1100.0), "4-20100": (0.0, 500.0), "4-10100": (0.0, 10000.0)}
    assert sum(r["debit"] for r in ringkas) == sum(r["credit"] for r in ringkas) and [n["account_code"] for n in nol] == ["2-10750"]


def test_penerapan_uang_muka_mengurangi_piutang_dan_akun_dp_muncul():
    dep = {"source_type": "DEPOSIT_APPLICATION", "lines": [L(*DP, d=5000), L(*AR, k=5000)]}
    ringkas, nol = FTR.ringkas_per_akun([jv(10000), recog(10000), dep])
    per = {r["account_code"]: (r["debit"], r["credit"]) for r in ringkas}
    assert per == {"1-10200": (5000.0, 0.0), "2-10500": (5000.0, 0.0), "4-10100": (0.0, 10000.0)}     # DP dipakai: akun uang muka MUNCUL (beda dgn 2-10750)
    assert [n["account_code"] for n in nol] == ["2-10750"]


def test_urutan_kemunculan_pertama_dan_kosong():
    ringkas, _ = FTR.ringkas_per_akun([jv(10000), recog(10000)])
    assert [r["account_code"] for r in ringkas] == ["1-10200", "4-10100"]
    assert FTR.ringkas_per_akun([]) == ([], [])
    assert FTR.ringkas_per_akun([{"lines": []}]) == ([], [])


def test_pembulatan_float_dan_akun_tanpa_kode():
    ringkas, nol = FTR.ringkas_per_akun([{"lines": [L(*AR, d=0.1), L(*AR, d=0.2), L(*JUAL, k=0.3), L(*DM, d=0.004), L(*DM, k=0.004)]}])
    assert [(r["account_code"], r["debit"], r["credit"]) for r in ringkas] == [("1-10200", 0.3, 0.0), ("4-10100", 0.0, 0.3)] and len(nol) == 1   # derau float tak membuat baris
    ringkas, nol = FTR.ringkas_per_akun([{"lines": [L(None, "Akun X", d=5), L(None, "Akun X", k=5), L(None, "Akun Y", d=2)]}])
    assert [(r["account_name"], r["debit"]) for r in ringkas] == [("Akun Y", 2.0)] and nol == [{"account_code": None, "account_name": "Akun X"}]


def test_properti_jurnal_seimbang_ringkasan_seimbang():
    rnd = random.Random(7)
    for _ in range(200):
        j = []
        for _ in range(rnd.randint(1, 4)):
            nilai = D(rnd.randint(1, 5_000_000)) / D(rnd.choice([1, 100]))
            akun = rnd.sample([AR, DM, PPN, JUAL, ONGKIR, DP], 2)
            j.append({"lines": [L(*akun[0], d=nilai), L(*akun[1], k=nilai)]})
        ringkas, _ = FTR.ringkas_per_akun(j)
        assert round(sum(r["debit"] for r in ringkas) - sum(r["credit"] for r in ringkas), 2) == 0
        assert all((r["debit"] > 0) != (r["credit"] > 0) for r in ringkas)           # tiap baris satu sisi saja


def test_kunci_baru_di_rencana_dan_respons_post_preview_rincian_tetap(monkeypatch):
    src = inspect.getsource(FTR.rencana_terbit_ro)
    assert "ringkas_per_akun(journals + deposit_journals)" in src and '"journals": journals' in src and '"deposit_application_journals": deposit_journals' in src
    r = {"invoice": {"invoice_number": "INV-1"}, "blocks": [], "warnings": [], "notes": [], "complete": True, "journals": [jv(10000), recog(10000)],
         "deposit_applications": [], "deposit_application_journals": []}
    r["ringkas_per_akun"], r["akun_nol_netto"] = FTR.ringkas_per_akun(r["journals"])

    class K:
        def transaction(self, readonly=False):
            class Tx:
                async def start(s):
                    return None

                async def rollback(s):
                    return None
            return Tx()

        async def execute(self, *a):
            return None

        def acquire(self):
            k = self

            class Ac:
                async def __aenter__(s):
                    return k

                async def __aexit__(s, *e):
                    return False
            return Ac()

    async def gp():
        return K()

    async def pre(pool, tid=None):
        return None

    async def rencana(conn, ctx, iid, skip):
        return r
    monkeypatch.setattr(SI, "get_pool", gp)
    monkeypatch.setattr(SI, "_ensure_role_preconditions", pre)
    monkeypatch.setattr(FTR, "rencana_terbit_ro", rencana)
    q = Request({"type": "http", "method": "POST", "path": "/", "headers": []})
    q.state.user = {"tenant_id": T, "user_id": "u"}
    d = asyncio.run(SI.preview_post_invoice(q, uuid.uuid4(), []))["data"]
    assert d["ringkas_per_akun"] == r["ringkas_per_akun"] and d["akun_nol_netto"] == r["akun_nol_netto"]
    assert d["journals"] == r["journals"] and len(d["journals"]) == 2                  # rincian TETAP lengkap (Law 1: yang ditulis = yang tampil)


# ---------------- BUG-176
class KonNomor:
    def __init__(self, terpakai):
        self.terpakai, self.sql = terpakai, []

    async def fetchval(self, sql, *a):
        self.sql.append((sql, a))
        if "FROM journal_entries WHERE tenant_id = $1 AND journal_number = $2" in sql:
            return 1 if a[1] in self.terpakai else None
        if "get_next_journal_number" in sql:
            return f"{a[1]}-2611-0001"
        raise AssertionError(sql[:60])


def test_nomor_jurnal_biaya_sama_dengan_nomor_dokumen_bila_bebas():
    k = KonNomor(terpakai=set())
    assert asyncio.run(NJ.nomor_jurnal_dokumen(k, T, "EXP-2611-0010", "JV-EXP")) == "EXP-2611-0010"          # bukan JV-EXP-EXP-2611-0010
    k = KonNomor(terpakai={"EXP-2611-0010"})
    assert asyncio.run(NJ.nomor_jurnal_dokumen(k, T, "EXP-2611-0010", "JV-EXP")) == "JV-EXP-2611-0001"        # cadangan seri JV-EXP
    assert all(a[0] == T for _, a in k.sql)                                                                  # tersaring tenant


def test_create_expense_journal_memakai_penomor_bersama_tanpa_awalan_ganda():
    src = inspect.getsource(EX.create_expense_journal)
    assert 'nomor_jurnal_dokumen(conn, tenant_id, expense_number, "JV-EXP")' in src
    assert 'f"JV-EXP-{expense_number}"' not in src and "JV-EXP-{" not in src
