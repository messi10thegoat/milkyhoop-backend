"""P1 (26 Sep 2026): tanggal default dokumen INTAKE = tanggal BISNIS tenant, bukan UTC.

Unggahan/form tanpa tanggal OCR dulu memakai date.today() server (UTC) -> dokumen 00:00-07:00 WIB
bertanggal KEMARIN (tgl 1 -> bulan lalu). Tiga modul jalur intake: payload_transformers (6 transformer
+ jatuh tempo), kernel_document_executor (_parse_date + nomor jurnal), document_action_resolver
(tanggal bayar/beban tanpa OCR). Jam DISUNTIK ke tanggal_tenant: 2026-09-25 18:00Z (= 26 Sep WIB).
"""
import ast
import inspect
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from app.utils import tanggal_tenant as tt

APP = Path(tt.__file__).resolve().parents[1]
MODUL = ["services/payload_transformers.py", "services/kernel_document_executor.py",
         "services/unified_agent/document_action_resolver.py"]
TENANT = "kaos-biru-konveksi"
MALAM = datetime(2026, 9, 25, 18, 0, tzinfo=timezone.utc)   # 01:00 WIB 26 Sep
SIANG = datetime(2026, 9, 25, 10, 0, tzinfo=timezone.utc)   # 17:00 WIB 25 Sep


def jam_utc(src: str):
    """Baris pemanggilan tanggal-server, sadar ALIAS impor (termasuk impor lokal di dalam fungsi)."""
    t = ast.parse(src)
    date_n, dt_n, mod_n = set(), set(), set()
    for n in ast.walk(t):
        if isinstance(n, ast.ImportFrom) and n.module == "datetime":
            for a in n.names:
                if a.name == "date":
                    date_n.add(a.asname or "date")
                if a.name == "datetime":
                    dt_n.add(a.asname or "datetime")
        if isinstance(n, ast.Import):
            for a in n.names:
                if a.name == "datetime":
                    mod_n.add(a.asname or "datetime")

    def nama(b):
        if isinstance(b, ast.Name):
            return b.id
        if isinstance(b, ast.Attribute) and isinstance(b.value, ast.Name) and b.value.id in mod_n:
            return b.value.id + "." + b.attr
        return None
    out = []
    for n in ast.walk(t):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
            f, bn = n.func, nama(n.func.value)
            if f.attr == "today" and bn and (bn in date_n | dt_n or bn.endswith((".date", ".datetime"))):
                out.append(n.lineno)
            if f.attr == "date" and isinstance(f.value, ast.Call) and isinstance(f.value.func, ast.Attribute) \
                    and f.value.func.attr in ("now", "utcnow") and not f.value.args:
                b2 = nama(f.value.func.value)
                if b2 and (b2 in dt_n or b2.endswith(".datetime")):
                    out.append(n.lineno)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and "CURRENT_DATE" in n.value.upper() \
                and "SELECT" in n.value.upper():
            out.append(n.lineno)
    return sorted(out)


@pytest.mark.parametrize("berkas", MODUL)
def test_modul_intake_tanpa_tanggal_server(berkas):
    assert jam_utc((APP / berkas).read_text()) == [], berkas


def test_penjaga_bisa_merah_termasuk_alias_dan_impor_lokal():
    contoh = ("from datetime import date, datetime\nimport datetime as dtm\n"
              "def f():\n    from datetime import date as _dt\n    a = _dt.today()\n    b = date.today()\n"
              "    c = datetime.now().date()\n    d = dtm.date.today()\n    q = 'SELECT CURRENT_DATE'\n"
              "    ok = datetime.now(timezone.utc)\n")
    assert jam_utc(contoh) == [5, 6, 7, 8, 9]


@pytest.fixture
def jam(monkeypatch):
    def pasang(instant):
        class _DT(datetime):
            @classmethod
            def now(cls, tz=None):
                return instant if tz else instant.replace(tzinfo=None)
        monkeypatch.setattr(tt, "datetime", _DT)
        tt._cache.clear()
    yield pasang
    tt._cache.clear()


class _Conn:
    async def fetchval(self, q, *a):
        assert 'FROM "Tenant"' in q, q
        return "Asia/Jakarta"


class _Pool:
    def acquire(self):
        class _Ctx:
            async def __aenter__(self):
                return _Conn()

            async def __aexit__(self, *e):
                return False
        return _Ctx()


@pytest.mark.parametrize("instant,harap", [(MALAM, date(2026, 9, 26)), (SIANG, date(2026, 9, 25))])
@pytest.mark.asyncio
async def test_kernel_parse_date_tanpa_tanggal_pakai_tanggal_bisnis(jam, instant, harap):
    from app.services.kernel_document_executor import KernelDocumentExecutor

    jam(instant)
    k = KernelDocumentExecutor.__new__(KernelDocumentExecutor)
    assert await k._parse_date(_Conn(), TENANT, {}) == harap
    assert await k._parse_date(_Conn(), TENANT, {"journal_draft": {}}) == harap
    # tanggal yang ADA di draf tetap menang
    assert await k._parse_date(_Conn(), TENANT, {"date": "2026-08-03"}) == date(2026, 8, 3)


@pytest.mark.parametrize("instant,harap", [(MALAM, date(2026, 9, 26)), (SIANG, date(2026, 9, 25))])
@pytest.mark.asyncio
async def test_resolver_hari_ini_tanggal_bisnis(jam, instant, harap):
    from app.services.unified_agent.document_action_resolver import DocumentActionResolver

    jam(instant)
    r = DocumentActionResolver(_Pool(), TENANT)
    assert await r._hari_ini() == harap


def test_semua_transformer_wajib_hari_ini_dan_memakainya():
    from app.services import payload_transformers as PT

    fns = [PT.transform_to_bill_payload, PT.transform_to_sales_invoice_payload, PT.transform_to_expense_payload,
           PT.transform_to_bill_payment_payload, PT.transform_to_receive_payment_payload,
           PT.transform_to_journal_payload]
    for fn in fns:
        p = inspect.signature(fn).parameters["hari_ini"]
        assert p.kind is inspect.Parameter.KEYWORD_ONLY and p.default is inspect.Parameter.empty, fn.__name__
        with pytest.raises(TypeError):
            fn({}, {})
    # rute memetakan ke transformer yang sama (tak ada transformer tanpa hari_ini)
    for route in PT.ACTION_ROUTES.values():
        assert "hari_ini" in inspect.signature(route[2]).parameters, route


def test_transformer_draf_tanpa_tanggal_dan_jatuh_tempo():
    from app.services import payload_transformers as PT

    hari = date(2026, 9, 26)
    bank = {"bank_account_id": "00000000-0000-0000-0000-000000000001"}
    baris = [{"account_code": "6-10100", "debit": "50000", "credit": "0", "description": "Bensin"},
             {"account_code": "1-10100", "debit": "0", "credit": "50000"}]
    draf = {"journal_draft": {"lines": baris}, "bank_draft": bank}
    exp = PT.transform_to_expense_payload(draf, {}, hari_ini=hari)
    assert "2026-09-26" in {v for v in exp.values() if isinstance(v, str)}
    # jatuh tempo cadangan: issue tak terbaca -> hari_ini + 30
    assert PT._extract_due_date({}, {}, "bukan-tanggal", hari) == "2026-10-26"
    # tanggal draf menang atas hari_ini
    exp2 = PT.transform_to_expense_payload({"journal_draft": {"journal_date": "2026-08-03", "lines": baris}, "bank_draft": bank},
                                           {}, hari_ini=hari)
    assert "2026-08-03" in {v for v in exp2.values() if isinstance(v, str)}


def test_jalur_rest_kernel_mengoper_hari_ini_dari_tanggal_dokumen():
    src = (APP / "services/kernel_document_executor.py").read_text()
    t = ast.parse(src)
    (fn,) = [n for n in ast.walk(t) if isinstance(n, ast.AsyncFunctionDef) and n.name == "_execute_via_rest"]
    teks = ast.unparse(fn)
    assert "hari_ini_rest = await tanggal_dokumen(conn, tenant_id)" in teks
    panggil = [c for c in ast.walk(fn) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
               and c.func.id == "transformer"]
    assert panggil
    for c in panggil:
        (kw,) = [k for k in c.keywords if k.arg == "hari_ini"]
        # nilainya WAJIB variabel dari tanggal_dokumen, bukan tanggal karangan/konstan
        assert isinstance(kw.value, ast.Name) and kw.value.id == "hari_ini_rest", ast.unparse(kw.value)
