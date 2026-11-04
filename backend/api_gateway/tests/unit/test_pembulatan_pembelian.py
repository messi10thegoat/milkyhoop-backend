"""c-2 PEMBULATAN dokumen PEMBELIAN (putusan pemilik LANGSUNG 5 Nov 2026, pola Accurate; V479): baris tagihan / nota debit tetap bersen,
TOTAL dibulatkan ke presisi tenant SAAT posting, selisih ke peran PURCHASE_ROUNDING (5-90100 Pembulatan Pembelian). SATU penentu
tagihan_aksi.pembulatan_dokumen untuk posting tagihan (create_bill_v2 + post_bill), posting ND, dan semua pratinjaunya. Kartu stok / HPP =
nilai baris asli (dasar_tagihan mengeluarkan pembulatan). Contoh angka = contoh yang disetujui pemilik."""
import asyncio
import inspect
from decimal import Decimal
from pathlib import Path

import pytest

from app.routers import bills as RB
from app.routers import vendor_credits as RVC
from app.services import bills_service as BS
from app.services import nota_debit_rencana as NDR
from app.services import ringkasan_cetak as RC
from app.services import role_resolver as RR
from app.services import tagihan_aksi as TA
from app.services import uang_presisi as UP

D = Decimal
MIG = Path(__file__).resolve().parents[3] / "migrations"


@pytest.mark.parametrize("v,p,total,selisih", [("55444.50", 0, "55445", "0.50"), ("18481.50", 0, "18482", "0.50"),
                                               ("55444.49", 0, "55444", "-0.49"), ("55445", 0, "55445", "0"),
                                               ("55444.50", 2, "55444.50", "0"), (None, 0, "0", "0")])
def test_pembulatan_murni(v, p, total, selisih):
    assert UP.pembulatan(v, p) == (D(total), D(selisih))


def _p(selisih, akun="R"):
    return {"total": D("0"), "selisih": D(selisih), "akun": akun, "blok": None}


def test_arah_baris_pembulatan_contoh_pemilik():
    # tagihan non-PKP: Dr Persediaan 55.444,50 · Dr Pembulatan 0,50 · Cr Hutang 55.445
    assert TA.baris_pembulatan(_p("0.50"), 1) == {"account_id": "R", "debit": D("0.50"), "credit": D("0"), "memo": "Pembulatan"}
    # ND sebagian 18.481,50: Dr Hutang 18.482 · Cr Persediaan 18.481,50 · Cr Pembulatan 0,50
    assert TA.baris_pembulatan(_p("0.50"), -1) == {"account_id": "R", "debit": D("0"), "credit": D("0.50"), "memo": "Pembulatan"}
    assert TA.baris_pembulatan(_p("-0.49"), 1)["credit"] == D("0.49")
    assert TA.baris_pembulatan(_p("0"), 1) is None


def test_dasar_tagihan_mengeluarkan_pembulatan():
    # 3 × 16.650 = 49.950 + PPN 11% 5.494,50 = 55.444,50 -> total 55.445, pembulatan 0,50
    assert TA.dasar_tagihan(D("55445"), D("5494.50"), None, D("0.50")) == (D("55444.50"), True)  # non-PKP: PPN dikapitalisasi
    assert TA.dasar_tagihan(D("55445"), D("5494.50"), "VAT", D("0.50")) == (D("49950.00"), False)  # PKP
    assert TA.dasar_tagihan(D("55444.50"), D("5494.50"), None) == (D("55444.50"), True)  # tanpa pembulatan = lama


class _Conn:
    def __init__(self, fitur=(), peran=True):
        self.fitur, self.peran, self.tanya = list(fitur), peran, []

    async def fetch(self, sql, *a):
        self.tanya.append(sql)
        return [{"feature": f} for f in self.fitur] if "tenant_features" in sql else []

    async def fetchrow(self, sql, *a):
        self.tanya.append(sql)
        if "account_roles" in sql:
            assert a[1] == "PURCHASE_ROUNDING"
            return {"account_id": "AKUN-BULAT"} if self.peran else None
        return None


def test_penentu_presisi_tenant_dan_akun():
    c = _Conn()
    r = asyncio.run(TA.pembulatan_dokumen(c, "t", D("55444.50")))
    assert (r["total"], r["selisih"], r["akun"], r["blok"]) == (D("55445"), D("0.50"), "AKUN-BULAT", None)
    r = asyncio.run(TA.pembulatan_dokumen(_Conn(peran=False), "t", D("55444.50")))
    assert r["akun"] is None and r["blok"] == {"code": "AKUN_PEMBULATAN_HILANG", "message": TA.PESAN_AKUN_PEMBULATAN}
    c = _Conn(peran=False)  # selisih 0 -> akun tak dibaca, tak memblok
    r = asyncio.run(TA.pembulatan_dokumen(c, "t", D("55445")))
    assert r["blok"] is None and not any("account_roles" in q for q in c.tanya)
    r = asyncio.run(TA.pembulatan_dokumen(_Conn(fitur=[UP.FLAG_BERSEN], peran=False), "t", D("55444.50")))  # uang_bersen
    assert (r["total"], r["selisih"], r["blok"]) == (D("55444.50"), D("0"), None)


def test_satu_penentu_di_semua_titik_posting_dan_pratinjau():
    titik = {"post_bill": inspect.getsource(BS.BillsService.post_bill), "create_bill_v2": inspect.getsource(BS.BillsService.create_bill_v2),
             "blok_posting": inspect.getsource(TA.blok_posting), "baris_posting_nd": inspect.getsource(NDR.baris_posting_nd),
             "rencana_posting_nd": inspect.getsource(NDR.rencana_posting), "pratinjau_form": inspect.getsource(RB),
             "nd_dari_tagihan": inspect.getsource(RVC)}
    for nama in ("create_bill_v2", "blok_posting", "baris_posting_nd", "rencana_posting_nd", "pratinjau_form", "nd_dari_tagihan"):
        assert "pembulatan_dokumen(" in titik[nama], nama
    assert '_akun["bulat"]' in titik["post_bill"]  # post_bill memakai hasil blok_posting (penentu SAMA dgn pratinjau posting)
    assert 'akun["bulat"]' in inspect.getsource(TA.rencana_posting)
    for nama in ("post_bill", "create_bill_v2"):
        s = titik[nama]
        assert s.index("pembulatan = $2") < s.index("journal_id = $2"), nama  # ditulis SEBELUM journal_id (beku Law 19)
        assert "TA.baris_pembulatan(_bulat, 1)" in s, nama
    assert "TA.baris_pembulatan(bulat, -1)" in titik["baris_posting_nd"]
    post_nd = inspect.getsource(RVC.post_vendor_credit)
    assert "NDR.baris_posting_nd(" in post_nd and "sum((Decimal(str(b[1])) for b in _baris_nd)" in post_nd  # header = Σ debit
    assert "original_lines), Decimal(\"0\"))" in inspect.getsource(RVC.void_vendor_credit)  # batal = Σ baris asli


def test_ringkasan_cetak_baris_pembulatan():
    rows = RC.susun(subtotal_bruto=D("49950"), diskon_baris=0, neto_baris=[D("49950")], pajak=("PPN 11%", D("5494.50")),
                    total=D("55445"), pembulatan=D("0.50"))
    assert [r["label"] for r in rows][-2:] == ["Pembulatan", "Total"] and rows[-2]["nilai"] == D("0.50")
    rows = RC.susun(subtotal_bruto=D("49950"), diskon_baris=0, neto_baris=[D("49950")], pajak=None, total=D("49950"))
    assert "Pembulatan" not in [r["label"] for r in rows]
    neg = RC.susun(subtotal_bruto=1, diskon_baris=0, neto_baris=[1], total=1, pembulatan=D("-0.49"))
    assert neg[-2] == {"label": "Pembulatan", "nilai": D("-0.49"), "kelas": ""}
    assert "pembulatan=bill.get(\"pembulatan\")" in inspect.getsource(RC.faktur_pembelian)
    assert "pembulatan=n.get(\"pembulatan\")" in inspect.getsource(RC.nota)


def test_peran_terdaftar_dan_migrasi_v479():
    assert RR.AccountRole.PURCHASE_ROUNDING == "PURCHASE_ROUNDING"
    assert RR.is_valid_role("PURCHASE_ROUNDING") and "PURCHASE_ROUNDING" in RR.PERAN_SEMAI
    m = (MIG / "V479__pembulatan_pembelian.sql").read_text()
    for kata in ("ADD COLUMN IF NOT EXISTS pembulatan", "'5-90100', 'Pembulatan Pembelian', 'OTHER_EXPENSE'", "'PURCHASE_ROUNDING'",
                 "trg_law19_bekukan_nominal", "seed_default_coa", "seed_default_account_roles", "jumlah jurnal berubah"):
        assert kata in m, kata
    assert "BEGIN;" not in m and "COMMIT;" not in m  # apply_mig.sh menjalankan -1
    assert (MIG / "V479__pembulatan_pembelian_ROLLBACK.sql").exists()
