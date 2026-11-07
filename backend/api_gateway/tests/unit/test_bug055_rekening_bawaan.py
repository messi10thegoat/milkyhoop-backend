"""BUG-055: rekening bayar bawaan form biaya = penentu SERVER (services/rekening_bawaan), disampaikan GET /api/expenses/summary
sebagai `paid_through_bawaan`. Dulu FE memakai rekening pertama abjad ("BCA Giro Utama") -> biaya tunai tercatat dari bank.
Cabang: terakhir -> utama -> kas -> None; rekening nonaktif dilewati (SQL; diuji nyata di uji-salinan-bug055). Penjaga pola BUG-097:
kunci yang dirakit rute summary WAJIB dideklarasikan skema (field tak dideklarasikan DIBUANG DIAM-DIAM oleh response_model)."""
import ast
import asyncio
import pathlib
import uuid

from app.schemas import expenses as S
from app.services import rekening_bawaan as RB

RUTE = pathlib.Path(__file__).resolve().parents[2] / "app" / "routers" / "expenses.py"
A, B, C = (uuid.uuid4() for _ in range(3))


def r(i, nama):
    return {"id": i, "account_name": nama}


class Conn:
    """fetchrow palsu: jawaban per SQL (identitas konstanta modul) + catat urutan panggilan."""

    def __init__(self, jawab):
        self.jawab, self.panggil = jawab, []

    async def fetchrow(self, sql, *args):
        self.panggil.append(sql)
        return self.jawab.get(sql)


def jalan(jawab):
    c = Conn(jawab)
    return asyncio.run(RB.paid_through_bawaan(c, "t1")), c.panggil


def test_terakhir_menang_dan_berhenti():
    h, p = jalan({RB.SQL_TERAKHIR: r(A, "Kas Kecil"), RB.SQL_UTAMA: r(B, "BCA"), RB.SQL_KAS: r(C, "Kas Toko")})
    assert h == {"id": str(A), "nama": "Kas Kecil", "sumber": "terakhir"} and p == [RB.SQL_TERAKHIR]


def test_utama_bila_belum_ada_biaya():
    h, p = jalan({RB.SQL_UTAMA: r(B, "BCA"), RB.SQL_KAS: r(C, "Kas Toko")})
    assert h == {"id": str(B), "nama": "BCA", "sumber": "utama"} and p == [RB.SQL_TERAKHIR, RB.SQL_UTAMA]


def test_kas_bila_tanpa_utama():
    h, p = jalan({RB.SQL_KAS: r(C, "Kas Toko")})
    assert h == {"id": str(C), "nama": "Kas Toko", "sumber": "kas"} and p == [RB.SQL_TERAKHIR, RB.SQL_UTAMA, RB.SQL_KAS]


def test_pilih_urutan_terakhir_utama_kas():
    t, u, k = r(A, "Kas Kecil"), r(B, "BCA"), r(C, "Kas Toko")
    assert [(RB.pilih(*x) or {}).get("sumber") for x in ((t, u, k), (None, u, k), (None, None, k), (None, None, None))] == [
        "terakhir", "utama", "kas", None]


def test_none_bila_tak_ada_apa_pun():
    assert jalan({})[0] is None


def test_sql_melewati_rekening_nonaktif_dan_hanya_biaya_posted():
    for sql in (RB.SQL_TERAKHIR, RB.SQL_UTAMA, RB.SQL_KAS):
        assert "is_active" in sql and "tenant_id = $1" in sql
    assert "e.status = 'posted'" in RB.SQL_TERAKHIR and set(RB.TIPE_KAS) == {"cash", "petty_cash"}


def _kamus_summary():
    pohon = ast.parse(RUTE.read_text())
    f = next(n for n in pohon.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "get_expenses_summary")
    hasil = [[k.value for k in n.keys if isinstance(k, ast.Constant)] for n in ast.walk(f) if isinstance(n, ast.Dict)]
    return [ks for ks in hasil if "top_accounts" in ks]


def test_semua_kunci_summary_dideklarasikan_skema():
    kamus = _kamus_summary()
    assert kamus and any("paid_through_bawaan" in ks for ks in kamus)  # kontrol: penjaga menemukan kamus data summary
    for ks in kamus:
        assert [k for k in ks if k not in S.ExpenseSummaryData.model_fields] == []


def test_respons_menyampaikan_bawaan_dan_null():
    dasar = {"period": "month", "total_count": 0, "total_amount": 0, "total_tax": 0, "vendor_count": 0, "billable_count": 0,
             "billable_amount": 0, "top_accounts": []}
    d = S.ExpenseSummaryResponse.model_validate({"data": {**dasar, "paid_through_bawaan": {"id": str(A), "nama": "Kas Kecil",
                                                                                         "sumber": "terakhir"}}}).model_dump(mode="json")
    assert d["data"]["paid_through_bawaan"] == {"id": str(A), "nama": "Kas Kecil", "sumber": "terakhir"}
    assert S.ExpenseSummaryResponse.model_validate({"data": {**dasar, "paid_through_bawaan": None}}).model_dump()["data"]["paid_through_bawaan"] is None
