"""Pengunci GET /api/credit-notes/summary (MASTER 4 Okt 2026: "cukup tes pengunci sekarang"; WORKSPACE meminta medan baru saat U5).

Mengunci APA YANG SUDAH ADA supaya perubahan berikutnya sadar: (1) rute /summary terdaftar SEBELUM /{credit_note_id}
(kalau tidak, "summary" ditelan parameter UUID -> 422); (2) hitungan dari tabel, NILAI dari jurnal (Law 16): piutang
RECEIVABLE kredit, jurnal POSTED non-reversed, NK void dikecualikan, filter tenant eksplisit; (3) bentuk respons =
skema CreditNoteSummaryResponse dan None -> 0 (bukan null/palsu); (4) tak ada medan 'minggu/bulan ini' yang bisa Rp 0
palsu (kelas cacat kartu ringkasan)."""
import asyncio
import inspect
import uuid

from app.routers import credit_notes as C


MEDAN = {"total", "draft_count", "posted_count", "partial_count", "applied_count", "total_value", "total_applied",
         "total_refunded", "available_balance", "void_count"}  # void_count: U9 (5 Okt), kueri TERPISAH


def test_summary_terdaftar_sebelum_rute_parameter():
    jalur = [r.path for r in C.router.routes if "GET" in getattr(r, "methods", ())]
    assert "/summary" in jalur and "/{credit_note_id}" in jalur
    assert jalur.index("/summary") < jalur.index("/{credit_note_id}")
    # kontrol: urutan terbalik memang akan menelan "summary" sebagai parameter -- pengunci ini harus bisa merah
    assert not (jalur.index("/{credit_note_id}") < jalur.index("/summary"))


def test_sumber_angka_jurnal_bukan_kolom_dan_pagar_tenant():
    src = " ".join(inspect.getsource(C.get_credit_notes_summary).split())
    assert "coa.account_type = 'RECEIVABLE' AND jl.credit > 0" in src          # nilai dari kredit piutang jurnal
    assert "je.status = 'POSTED' AND je.reversed_by_id IS NULL" in src         # hanya jurnal efektif
    assert "je.source_type = 'CREDIT_NOTE'" in src
    assert src.count("cn.tenant_id = $1") == 2                                 # CTE jurnal + kueri utama
    assert src.count("cn.status != 'void'") == 2                               # NK void tak dihitung
    assert "FILTER (WHERE cn.status = 'draft')" in src and "FILTER (WHERE cn.status IN ('posted', 'partial'))" in src
    assert "ORDER BY" not in src and "current_balance" not in src


class _Konn:
    def __init__(self, baris, void=None):
        self.baris, self.args, self.void, self.args_void = baris, None, void, None

    async def fetchrow(self, sql, *a):
        self.args = a
        return self.baris

    async def fetchval(self, sql, *a):  # U9: hitungan void = kueri terpisah (kueri utama TANPA void)
        self.args_void = a
        return self.void


class _Pool:
    def __init__(self, k):
        self.k = k

    def acquire(self):
        k = self.k

        class _A:
            async def __aenter__(s):
                return k

            async def __aexit__(s, *a):
                return False
        return _A()


def _panggil(monkeypatch, baris, void=None):
    k = _Konn(baris, void)

    async def pool():
        return _Pool(k)
    monkeypatch.setattr(C, "get_pool", pool)

    class _Req:
        class state:
            user = {"tenant_id": "t1", "user_id": str(uuid.uuid4())}
    return k, asyncio.run(C.get_credit_notes_summary(_Req()))


def test_bentuk_respons_sama_dengan_skema_dan_none_jadi_nol(monkeypatch):
    kosong = {k: None for k in ("total", "draft_count", "posted_count", "partial_count", "applied_count", "total_value",
                                "total_applied", "total_refunded", "available_balance")}
    k, out = _panggil(monkeypatch, kosong)
    assert k.args == ("t1",)  # tenant dari JWT, satu-satunya parameter
    data = out["data"]
    # skema respons = Dict[str, Any] (TIDAK menegakkan medan) -> kumpulan medan dikunci di sini; medan baru = ubah tes dgn sadar
    assert set(data) == MEDAN
    assert all(v == 0 for v in data.values())
    assert all(isinstance(data[x], float) for x in ("total_value", "total_applied", "total_refunded", "available_balance"))
    assert all(isinstance(data[x], int) for x in ("total", "draft_count", "posted_count", "partial_count", "applied_count",
                                                  "void_count"))
    assert k.args_void == ("t1",)  # kueri void juga bertenant eksplisit


def test_angka_dilewatkan_apa_adanya(monkeypatch):
    from decimal import Decimal
    baris = {"total": 5, "draft_count": 1, "posted_count": 2, "partial_count": 1, "applied_count": 1,
             "total_value": Decimal("1234567.50"), "total_applied": Decimal("200000.25"), "total_refunded": Decimal("0"),
             "available_balance": Decimal("1034367.25")}
    _, out = _panggil(monkeypatch, baris, void=4)
    d = out["data"]
    assert d["void_count"] == 4 and d["total"] == 5 and d["total_value"] == 1234567.5 and d["available_balance"] == 1034367.25
    assert d["total_applied"] == 200000.25 and d["total_refunded"] == 0.0


def test_tak_ada_medan_periode_yang_bisa_nol_palsu():
    assert not {m for m in MEDAN if any(s in m for s in ("week", "month", "minggu", "bulan", "this_"))}, MEDAN
    # kontrol: pendeteksi harus BISA menemukan medan berbahaya
    assert {m for m in MEDAN | {"this_month_total"} if any(s in m for s in ("week", "month", "minggu", "bulan", "this_"))} == {"this_month_total"}
