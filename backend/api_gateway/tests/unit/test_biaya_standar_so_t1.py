"""Biaya standar SO tahap 1 (kontrak MASTER 7 Nov): kunci aksi draf/tanpa_bukti (pill) + bulan_ini (kartu), position_text, documents[]
per baris, with_totals sum_total, /summary {k}_count/_value satu predikat dengan daftar, X-Idempotency-Key opsional di POST /api/expenses.
Perilaku SQL nyata (paritas daftar == summary, replay/409) diuji di salinan: scripts/ops/uji-salinan-biaya-t1.*"""
import ast
import inspect
import pathlib

from app.routers import expenses as EX
from app.schemas import expenses as S
from app.services import biaya_daftar as BD
from app.utils import idempotency as IDEM

RUTE = pathlib.Path(__file__).resolve().parents[2] / "app" / "routers" / "expenses.py"


def test_aksi_semua_urut_desak():
    assert BD.aksi_semua("draft", False) == ["draf"] and BD.aksi_semua("draft", True) == ["draf"]
    assert BD.aksi_semua("posted", False) == ["tanpa_bukti"] and BD.aksi_semua("posted", True) == []
    assert BD.aksi_semua("void", False) == []


def test_position_text_ringkas_tanpa_bukti_ikut():
    assert BD.position_text("draft", "Kas Kecil", False) == "Draf"
    assert BD.position_text("posted", "Kas Kecil", True) == "Dibayar dari Kas Kecil"
    assert BD.position_text("posted", "Kas Kecil", False) == "Dibayar dari Kas Kecil · tanpa bukti"
    assert BD.position_text("void", "Kas Kecil", False) == "Dibatalkan"


def test_documents_baris():
    assert BD.documents_baris("j1", 2) == [{"type": "expense"}, {"type": "journal"}, {"type": "attachment"}, {"type": "attachment"}]
    assert BD.documents_baris(None, 0) == [{"type": "expense"}]


def test_sql_aksi_bertenant_dan_bukti_dari_lampiran():
    assert "ea.tenant_id = expenses.tenant_id" in BD.sql_aksi("tanpa_bukti", "$2") and "has_receipt" not in BD.sql_aksi("tanpa_bukti", "$2")
    assert "status <> 'void'" in BD.sql_aksi("bulan_ini", "$2") and "date_trunc('month', $2::date)" in BD.sql_aksi("bulan_ini", "$2")
    assert BD.SEMUA_KUNCI == ("draf", "tanpa_bukti", "bulan_ini")


def _kamus(fn_nama, penanda):
    pohon = ast.parse(RUTE.read_text())
    f = next(n for n in pohon.body if isinstance(n, ast.AsyncFunctionDef) and n.name == fn_nama)
    hasil = [[k.value for k in n.keys if isinstance(k, ast.Constant)] for n in ast.walk(f) if isinstance(n, ast.Dict)]
    return [ks for ks in hasil if penanda in ks]


def test_skema_menyatakan_semua_kunci_daftar_dan_ringkasan():
    baris = _kamus("list_expenses", "expense_number")
    assert baris and any("position_text" in ks and "documents" in ks for ks in baris)
    for ks in baris:
        assert [k for k in ks if k not in S.ExpenseListItem.model_fields] == []
    assert "sum_total" in S.ExpenseListResponse.model_fields
    for k in BD.SEMUA_KUNCI:
        assert {f"{k}_count", f"{k}_value"} <= set(S.ExpenseSummaryData.model_fields)
    assert any("sum_total" in ks for ks in _kamus("list_expenses", "has_more"))


def test_summary_dan_daftar_satu_predikat_cari():
    assert "_syarat_cari(search, 3)" in inspect.getsource(EX.get_expenses_summary)
    assert "_syarat_cari(search, param_idx)" in inspect.getsource(EX.list_expenses)
    assert "BD.sql_aksi(" in inspect.getsource(EX.get_expenses_summary) and "BD.sql_aksi(aksi" in inspect.getsource(EX.list_expenses)


def test_idempotensi_buat_opsional_sebelum_tulis():
    s = " ".join(inspect.getsource(EX.create_expense).split())
    assert s.index("kunci_idempotensi_klien(request)") < s.index("ambil_replay_klien(") < s.index("buat_biaya_inti(") < s.index("simpan_replay_klien(")
    assert 'f"IDEM:{ctx[\'tenant_id\']}:{kunci_penuh}"' in s and "IDEMPOTENCY_KEY_REUSED" in s and 'X-Idempotent-Replay' in s
    assert 'if kunci_penuh: await simpan_replay_klien(conn, ctx["tenant_id"], kunci_penuh, "EXPENSE_CREATE"' in s  # replay benar-benar disimpan
    assert "if kunci_klien:" in s   # opsional (putusan MASTER 7 Nov; tiket wajibkan sesudah FE Biaya CW live)
    assert IDEM.SUMBER_BUAT["EXPENSE_CREATE"][0] == "expenses"


class _Conn:
    """Fake meniru asyncpg: jumlah argumen WAJIB == placeholder tertinggi (asyncpg menolak kelebihan/kurang)."""
    def __init__(self):
        self.kueri = []

    def _cek(self, sql, a):
        import re
        n = max([int(x) for x in re.findall(r"\$(\d+)", sql)] or [0])
        assert n == len(a), (n, len(a), sql[:120])
        self.kueri.append(sql)

    async def execute(self, sql, *a):
        return None

    async def fetchval(self, sql, *a):
        self._cek(sql, a)
        return 0

    async def fetch(self, sql, *a):
        self._cek(sql, a)
        return []


class _Acq:
    def __init__(self, c):
        self.c = c

    async def __aenter__(self):
        return self.c

    async def __aexit__(self, *a):
        return False


def test_daftar_per_kunci_argumen_sama_dengan_placeholder(monkeypatch):
    import asyncio
    from datetime import date
    from starlette.requests import Request

    async def pool():
        class P:
            def acquire(self):
                return _Acq(c)
        return P()

    async def hari(conn, tid):
        return date(2026, 11, 7)
    monkeypatch.setattr(EX, "get_pool", pool)
    monkeypatch.setattr(EX, "tanggal_dokumen", hari)
    for k in (None,) + BD.SEMUA_KUNCI:
        for cari in (None, "kopi susu"):
            c = _Conn()
            r = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
            r.state.user = {"tenant_id": "t1", "user_id": "00000000-0000-0000-0000-000000000001"}
            h = asyncio.run(EX.list_expenses(r, skip=0, limit=5, status="all", vendor_id=None, account_id=None, date_from=None, date_to=None,
                                             search=cari, sort_by="expense_date", sort_order="desc", is_billable=None, billing_status=None,
                                             aksi=k, with_totals=True))
            assert h["sum_total"] == 0.0 and len(c.kueri) == 3, (k, cari)
