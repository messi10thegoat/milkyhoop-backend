"""Jurnal Umum standar SO (kontrak + putusan MASTER 7 Nov 2026): SATU penentu (services/jurnal_daftar) untuk daftar ?aksi=,
GET /api/journals/summary ({k}_count/_value dari BARIS, pill_tampil), position_text, aksi_tersedia (bentuk bersama
schemas/aksi_tersedia), idempotensi opsional POST, periksa ulang sesudah lock (posting/balik), tanpa SET app.tenant_id.
Paritas SQL nyata + benih timpang + dua "Balik" bersamaan diuji di salinan: scripts/ops/uji-salinan-jurnal-so.*"""
import ast
import asyncio
import inspect
import pathlib
import re
import uuid
from datetime import date, datetime
from decimal import Decimal as D
from typing import Optional

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.routers import journals as RJ
from app.schemas import journals as S
from app.schemas.aksi_tersedia import AksiTersedia, AksiTersediaPeta
from app.services import jurnal_daftar as JD
from app.utils import idempotency as IDEM

ROOT = pathlib.Path(__file__).resolve().parents[2]
RUTE = ROOT / "app" / "routers" / "journals.py"


def _j(**kw):
    b = {"status": "POSTED", "baris_debit": D("1000"), "baris_kredit": D("1000"), "total_debit": D("1000"), "total_credit": D("1000"),
         "reversed_by_id": None, "reversal_of_id": None, "source_type": "MANUAL", "nomor_pembalik": None, "nomor_asal": None}
    b.update(kw)
    return b


# --- penentu ----------------------------------------------------------------------------------------------------------------

def test_aksi_semua_dan_timpang_baris_vs_header():
    assert JD.aksi_semua(_j()) == []
    assert JD.aksi_semua(_j(status="DRAFT")) == ["draf"]
    assert JD.aksi_semua(_j(status="DRAFT", baris_kredit=D("900"))) == ["tidak_seimbang", "draf"]   # urut desak
    assert JD.aksi_semua(_j(total_debit=D("1001"), total_credit=D("1001"))) == ["tidak_seimbang"]  # baris != header (BP-2608-0001)
    assert JD.aksi_semua(_j(baris_debit=D("1000.004"))) == []                                     # di bawah sen


def test_sql_tidak_seimbang_baris_dan_header():
    sql = " ".join(JD.sql_aksi("tidak_seimbang", "$2").split())
    for bagian in ("ABS(jb.d - jb.k) >= 0.01", "ABS(jb.d - je.total_debit) >= 0.01", "ABS(jb.k - je.total_credit) >= 0.01"):
        assert bagian in sql, bagian
    assert "journal_lines l WHERE l.journal_id = je.id" in JD.SQL_BARIS


def test_position_text():
    assert JD.position_text(_j()) == "Diposting"
    assert JD.position_text(_j(status="DRAFT")) == "Draf · belum diposting"
    assert JD.position_text(_j(status="DRAFT", baris_kredit=D("0"))) == "Tidak seimbang · selisih Rp 1.000"
    assert JD.position_text(_j(reversed_by_id="x", nomor_pembalik="JV-2611-0002")) == "Diposting · dibalik oleh JV-2611-0002"
    assert JD.position_text(_j(reversal_of_id="x", nomor_asal="JV-2611-0001")) == "Pembalik JV-2611-0001"
    assert JD.position_text(_j(status="VOID", reversed_by_id="x")) == "Dibatalkan"


def test_manual_tak_menghitung_pembalik():
    sql = JD.sql_aksi("manual", "$2")
    assert "je.source_type IN ('MANUAL', 'ADJUSTMENT')" in sql and "je.reversal_of_id IS NULL" in sql


def test_alasan_balik_satu_penentu_dan_dipakai_router():
    assert JD.alasan_tak_bisa_balik(_j()) is None
    assert JD.alasan_tak_bisa_balik(_j(reversed_by_id="x", nomor_pembalik="JV-1")) == "Jurnal sudah dibalik oleh JV-1."
    assert "pembalikan atas pembalikan" in JD.alasan_tak_bisa_balik(_j(reversal_of_id="x"))
    assert JD.alasan_tak_bisa_balik(_j(source_type="INVOICE")).endswith("batalkan fakturnya (void invoice).")
    assert JD.alasan_tak_bisa_balik(_j(status="DRAFT")).startswith("Jurnal draf")
    src = inspect.getsource(RJ.reverse_journal)
    assert src.count("JD.alasan_tak_bisa_balik(") == 2      # sebelum DAN sesudah lock
    assert RJ.REVERSAL_ALLOWLIST is JD.REVERSAL_ALLOWLIST


def test_aksi_tersedia_bentuk_bersama():
    a = JD.aksi_tersedia(_j(status="DRAFT"))
    assert list(a) == ["posting", "hapus", "balik"]
    for v in a.values():
        AksiTersedia.model_validate(v)
    assert a["posting"] == {"tersedia": True, "alasan": None} and a["hapus"]["tersedia"] and not a["balik"]["tersedia"]
    assert JD.aksi_tersedia(_j(status="DRAFT"), periode_tutup=True)["posting"]["alasan"] == "Periode akuntansi tanggal jurnal ini sudah ditutup."
    assert "tidak seimbang" in JD.aksi_tersedia(_j(status="DRAFT", baris_kredit=D("1")))["posting"]["alasan"]
    assert S.JournalResponse.model_fields["aksi_tersedia"].annotation == Optional[AksiTersediaPeta]


# --- P3: tanpa SET app.tenant_id, semua kueri journal_entries ber-tenant ------------------------------------------------------

def _sql_literal(pohon):
    anak_f = {id(v) for n in ast.walk(pohon) if isinstance(n, ast.JoinedStr) for v in n.values}
    for n in ast.walk(pohon):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in anak_f:
            yield n.value, False
        elif isinstance(n, ast.JoinedStr):
            teks = "".join(v.value if isinstance(v, ast.Constant) else "{" + ast.unparse(v.value) + "}" for v in n.values)
            yield teks, True


def test_tanpa_set_app_tenant_id_dan_kueri_ber_tenant():
    src = RUTE.read_text()
    assert "app.tenant_id" not in src
    kurang = []
    for teks, _ in _sql_literal(ast.parse(src)):
        t = " ".join(teks.split())
        if re.search(r"(FROM|UPDATE|INTO|DELETE FROM) journal_entries\b", t) and "tenant_id" not in t and "{where_clause}" not in t:
            kurang.append(t[:120])
    assert kurang == []


# --- rute daftar / summary ----------------------------------------------------------------------------------------------------

class _Conn:
    def __init__(self, rows=(), ring=None):
        self.rows, self.ring, self.sql = list(rows), ring or {}, []

    def _cek(self, sql, a):
        n = max([int(x) for x in re.findall(r"\$(\d+)", sql)] or [0])
        assert n == len(a), (n, len(a), sql[-300:])
        self.sql.append(sql)

    async def fetchrow(self, sql, *a):
        self._cek(sql, a)
        if "draf_count" in sql:
            return self.ring
        return {"total": len(self.rows), "draft_count": 0, "posted_count": len(self.rows), "void_count": 0, "reversed_count": 0,
                "total_amount": D("1500"), "total_kredit": D("1500")}

    async def fetch(self, sql, *a):
        self._cek(sql, a)
        return self.rows


class _Acq:
    def __init__(self, c):
        self.c = c

    async def __aenter__(self):
        return self.c

    async def __aexit__(self, *a):
        return False


def _pasang(monkeypatch, c):
    class P:
        def acquire(self):
            return _Acq(c)

    async def pool():
        return P()

    async def hari(conn, tid):
        return date(2026, 11, 7)
    monkeypatch.setattr(RJ, "get_pool", pool)
    monkeypatch.setattr(RJ, "tanggal_dokumen", hari)


def _req():
    r = Request({"type": "http", "method": "GET", "path": "/api/journals", "headers": []})
    r.state.user = {"tenant_id": "t1", "user_id": "00000000-0000-0000-0000-000000000001"}
    return r


def _baris(i, **kw):
    r = {"id": uuid.UUID(int=i + 1), "journal_number": f"JV-2611-{i:04d}", "journal_date": date(2026, 11, 7), "description": "x",
         "source_type": "MANUAL", "total_debit": D("500"), "total_credit": D("500"), "status": "POSTED", "created_at": datetime(2026, 11, 7),
         "reversed_by_id": None, "reversal_of_id": None, "baris_debit": D("500"), "baris_kredit": D("500"), "nomor_pembalik": None,
         "nomor_asal": None}
    r.update(kw)
    return r


def _daftar(monkeypatch, rows, **kw):
    c = _Conn(rows)
    _pasang(monkeypatch, c)
    a = dict(period_id=None, start_date=None, end_date=None, status=None, source_type=None, account_id=None, search=None, sort_by=None,
             sort_order="desc", page=1, limit=20, aksi=None, with_totals=False)
    a.update(kw)
    return asyncio.run(RJ.list_journals(_req(), **a)), c


@pytest.mark.parametrize("kw", [
    {}, {"search": "gaji"}, {"aksi": "bulan_ini", "search": "x"}, {"aksi": "tidak_seimbang", "status": "posted"},
    {"status": "reversed", "with_totals": True, "start_date": date(2026, 11, 1), "aksi": "manual"},
])
def test_placeholder_daftar_sesuai_argumen(monkeypatch, kw):
    _daftar(monkeypatch, [_baris(0)], **kw)


def test_status_reversed_dipetakan_ke_dibalik(monkeypatch):
    _, c = _daftar(monkeypatch, [], status="reversed")
    assert JD.sql_aksi("dibalik", "") in c.sql[0] and "je.status = $" not in c.sql[0]


def test_baris_membawa_posisi_dan_with_totals_dari_baris(monkeypatch):
    r, c = _daftar(monkeypatch, [_baris(0, status="DRAFT", baris_kredit=D("400")), _baris(1, reversed_by_id="x", nomor_pembalik="JV-9")],
                   with_totals=True)
    a, b = r.data
    assert a.aksi_semua == ["tidak_seimbang", "draf"] and a.position_text == "Tidak seimbang · selisih Rp 100"
    assert b.position_text == "Diposting · dibalik oleh JV-9"
    assert (r.sum_debit, r.sum_kredit) == (D("1500"), D("1500"))
    assert "COALESCE(SUM(jb.d), 0) as total_amount" in c.sql[0] and "SUM(je.total_debit)" not in c.sql[0]   # P2: baris, bukan header
    r, _ = _daftar(monkeypatch, [])
    assert r.sum_debit is None


def test_aksi_tak_dikenal_422(monkeypatch):
    with pytest.raises(HTTPException) as e:
        _daftar(monkeypatch, [], aksi="timpang")
    assert e.value.status_code == 422


def _ring(**kw):
    r = {"total": 5, **{f"{k}_count": 0 for k in JD.SEMUA_KUNCI}, **{f"{k}_value": D("0") for k in JD.SEMUA_KUNCI}}
    r.update(kw)
    return r


@pytest.mark.parametrize("timpang, harap", [(0, ["draf"]), (2, ["tidak_seimbang", "draf"])])
def test_summary_pill_tidak_seimbang_hanya_bila_ada(monkeypatch, timpang, harap):
    c = _Conn(ring=_ring(tidak_seimbang_count=timpang, tidak_seimbang_value=D("10")))
    _pasang(monkeypatch, c)
    r = asyncio.run(RJ.journals_summary(_req(), search="sewa toko"))
    d = S.JournalKunciSummaryResponse.model_validate(r).data
    assert d.pill_tampil == harap and d.tidak_seimbang_count == timpang
    assert "je.description ILIKE $3 OR je.journal_number ILIKE $3" in c.sql[0] and "SUM(jb.d)" in c.sql[0]


def test_skema_summary_sama_dengan_semua_kunci():
    medan = set(S.JournalKunciSummary.model_fields)
    assert medan == {"total", "pill_tampil"} | {f"{k}_{x}" for k in JD.SEMUA_KUNCI for x in ("count", "value")}


def _kwargs(fn_nama, kelas):
    pohon = ast.parse(RUTE.read_text())
    f = next(n for n in pohon.body if isinstance(n, ast.AsyncFunctionDef) and n.name == fn_nama)
    return [{k.arg for k in n.keywords} for n in ast.walk(f) if isinstance(n, ast.Call) and getattr(n.func, "id", None) == kelas]


def test_skema_menyatakan_semua_medan():
    for fn, kelas, model in (("list_journals", "JournalListItem", S.JournalListItem), ("list_journals", "JournalListResponse", S.JournalListResponse),
                             ("get_journal", "JournalResponse", S.JournalResponse)):
        semua = _kwargs(fn, kelas)
        assert semua, (fn, kelas)
        for ks in semua:
            assert ks - set(model.model_fields) == set(), (fn, kelas)
    assert {"aksi_semua", "position_text", "aksi_tersedia"} <= _kwargs("get_journal", "JournalResponse")[0]


def test_rute_bertipe_dan_summary_sebelum_id():
    jalur = [(r.path, getattr(r, "response_model", None)) for r in RJ.router.routes if r.methods & {"GET", "POST"}]
    p = [x for x, _ in jalur]
    assert p.index("/summary") < p.index("/{journal_id}")
    model = dict(jalur)
    for x in ("/{journal_id}", "/{journal_id}/post", "/{journal_id}/reverse", "/by-source/{source_type}/{source_id}"):
        assert model[x] is S.JournalDetailResponse, x
    from app.middleware.permission_middleware import izin_untuk_jalur
    assert izin_untuk_jalur("/api/journals/summary", "GET") == ("journal", "R")


# --- idempotensi + periksa ulang sesudah lock --------------------------------------------------------------------------------

def test_idempotensi_buat_opsional_sebelum_tulis():
    s = " ".join(inspect.getsource(RJ.create_journal).split())
    assert s.index("kunci_idempotensi_klien(request)") < s.index("MANUAL_CREATE:") < s.index("ambil_replay_klien(") \
        < s.index("_tulis_jurnal_manual(") < s.index("simpan_replay_klien(")
    assert "IDEMPOTENCY_KEY_REUSED" in s and "X-Idempotent-Replay" in s and "if kunci_klien:" in s
    assert IDEM.SUMBER_BUAT["JOURNAL_CREATE"][0] == "journal_entries"


@pytest.mark.parametrize("fn, kunci", [("post_journal", "MANUAL_POST:"), ("reverse_journal", "MANUAL_REVERSE:")])
def test_periksa_ulang_sesudah_lock(fn, kunci):
    s = " ".join(inspect.getsource(getattr(RJ, fn)).split())
    assert s.index(kunci) < s.index("FOR UPDATE")


def test_entity_resolver_memakai_satu_allowlist():
    src = (ROOT / "app" / "services" / "unified_agent" / "entity_resolver.py").read_text()
    assert "from ..jurnal_daftar import REVERSAL_ALLOWLIST" in src and "routers.journals import REVERSAL_ALLOWLIST" not in src
