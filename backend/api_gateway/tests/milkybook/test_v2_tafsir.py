"""Milkybook v2 lapisan LLM cadangan (03 §4, P5): keluaran tertutup, tool baca saja, penjaga angka, pagar biaya tanpa
panggilan model, kunci HANYA MILKYBOOK_ANTHROPIC_API_KEY, log ketikan per tenant (V470). Nol panggilan API sungguhan."""
import ast
import inspect
import json
import logging
import pathlib
import re
import uuid
from contextlib import asynccontextmanager
from decimal import Decimal

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.milkybook.inti.audit import AuditMemori
from app.milkybook.inti.model import GalatModel, HasilModel
from app.milkybook.v2 import tafsir as T
from app.milkybook.v2.router import buat_router_v2

USER = {"tenant_id": "kaos", "user_id": "u1"}
CID = "11111111-2222-4333-8444-555555555555"
USAGE = {"input_tokens": 1000, "output_tokens": 100}


def _guna(id_, nama, masukan):
    return {"type": "tool_use", "id": id_, "name": nama, "input": masukan}


def _jawab(**kw):
    return _guna("j1", "jawab", {"intent": "answer_question", "slot": "none", "value": "", "reply": "Baik.", **kw})


class Pemanggil:
    """Mengembalikan HasilModel sesuai naskah; merekam setiap panggilan (messages disalin)."""
    model = "claude-sonnet-5-5"

    def __init__(self, *naskah, galat=None):
        self.naskah, self.galat, self.panggilan = list(naskah), galat, []

    async def __call__(self, *, system, messages, tools, effort):
        self.panggilan.append({"messages": json.loads(json.dumps(messages)), "tools": tools, "effort": effort,
                               "system": system})
        if self.galat:
            raise self.galat
        konten = self.naskah.pop(0) if self.naskah else [{"type": "text", "text": "..."}]
        return HasilModel(konten=konten, stop_reason="tool_use", usage=dict(USAGE), model=self.model)


class Conn:
    def __init__(self, pelanggan=None, tagihan=(), stok=None, cari=()):
        self.pelanggan, self.tagihan, self.stok, self.cari, self.log = pelanggan, list(tagihan), stok, list(cari), []

    @asynccontextmanager
    async def transaction(self):
        self.log.append("BEGIN")
        yield
        self.log.append("COMMIT")

    async def execute(self, sql, *a):
        self.log.append(("execute", sql, a))

    async def fetchrow(self, sql, *a):
        self.log.append(("fetchrow", sql, a))
        if sql == T.SQL_PELANGGAN_ADA:
            return self.pelanggan
        if sql == T.SQL_STOK:
            return self.stok
        return None

    async def fetch(self, sql, *a):
        self.log.append(("fetch", sql, a))
        return self.tagihan if sql == T.SQL_TAGIHAN else self.cari


class Audit(AuditMemori):
    def __init__(self, terpakai=0.0):
        super().__init__()
        self.terpakai = terpakai

    async def biaya_bulan_ini(self, tenant_id):
        return self.terpakai


def _body(teks="pelanggan rocky ada tagihan?", **kw):
    return T.MintaTafsir(teks=teks, tahap="items", **kw)


def _log_baris(conn):
    return [x for x in conn.log if isinstance(x, tuple) and x[1] == T.SQL_LOG]


async def _jalan(pemanggil, conn=None, audit=None, batas=None, body=None):
    conn = conn or Conn()
    hasil = await T.tafsir(conn, USER, body or _body(), pemanggil=pemanggil, audit=audit or Audit(),
                           batas_fn=lambda t: batas)
    return hasil, conn


# ---------------------------------------------------------------- pagar biaya + kunci
@pytest.mark.asyncio
async def test_batas_biaya_terlampaui_tanpa_panggilan_model():
    p = Pemanggil(_jawab())
    hasil, conn = await _jalan(p, audit=Audit(terpakai=15.0), batas=15.0)
    assert p.panggilan == [] and hasil["intent"] == "clarify" and hasil["sumber"] == "batas"
    assert _log_baris(conn)[0][2][5] == "batas"


@pytest.mark.asyncio
async def test_biaya_tak_terbaca_fail_open_seperti_v1():
    class AuditNone(Audit):
        async def biaya_bulan_ini(self, t):
            return None
    p = Pemanggil([_jawab()])
    hasil, _ = await _jalan(p, audit=AuditNone(), batas=5.0)
    assert len(p.panggilan) == 1 and hasil["sumber"] == "llm"


@pytest.mark.asyncio
async def test_tanpa_pemanggil_tanpa_kunci_tanya_balik():
    hasil, conn = await _jalan(None)
    assert hasil["sumber"] == "galat" and hasil["intent"] == "clarify" and len(_log_baris(conn)) == 1


def test_kunci_hanya_milkybook_bukan_anthropic_api_key(monkeypatch):
    from app.milkybook import pasang
    monkeypatch.delenv("MILKYBOOK_ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-uji-bukan-milik-milkybook")
    pasang._pemanggil_v2.cache_clear()
    try:
        assert pasang._pemanggil_v2() is None
    finally:
        pasang._pemanggil_v2.cache_clear()
    src = inspect.getsource(pasang._pemanggil_v2)
    assert 'os.environ.get("MILKYBOOK_ANTHROPIC_API_KEY")' in src and '"ANTHROPIC_API_KEY"' not in src


# ---------------------------------------------------------------- keluaran tertutup
@pytest.mark.asyncio
async def test_alur_tool_baca_lalu_jawab_angka_dari_tool():
    conn = Conn(pelanggan={"nama": "Rocky"}, tagihan=[{"invoice_number": "INV-1", "invoice_date": None,
                                                      "due_date": None, "sisa": Decimal("1500000")}])
    p = Pemanggil([_guna("t1", "open_invoices", {"customer_id": CID})],
                  [_jawab(reply="Rocky punya 1 tagihan, sisa Rp 1.500.000.")])
    hasil, conn = await _jalan(p, conn=conn)
    assert hasil["sumber"] == "llm" and hasil["intent"] == "answer_question"
    assert hasil["tool_calls"] == [{"name": "open_invoices", "args": {"customer_id": CID}}]
    m2 = p.panggilan[1]["messages"]
    assert [x["role"] for x in m2] == ["user", "assistant", "user"]                 # append-only
    assert m2[2]["content"][0]["type"] == "tool_result" and m2[2]["content"][0]["tool_use_id"] == "t1"
    assert '"total_sisa": 1500000' in m2[2]["content"][0]["content"]
    assert p.panggilan[0]["effort"] == "low" and p.panggilan[0]["tools"] == T.TOOLS


@pytest.mark.asyncio
async def test_penjaga_angka_angka_bukan_dari_tool_ditolak():
    p = Pemanggil([_jawab(reply="Totalnya kira-kira Rp 2.750.000 ya.")])
    hasil, conn = await _jalan(p)
    assert hasil["sumber"] == "penjaga" and hasil["intent"] == "clarify" and "2.750.000" not in hasil["reply"]
    assert _log_baris(conn)[0][2][5] == "penjaga"


@pytest.mark.asyncio
async def test_angka_dari_ketikan_pengguna_boleh_diulang():
    p = Pemanggil([_jawab(intent="fill_slot", slot="dp", value="30%", reply="DP 30% saya catat di draf.")])
    hasil, _ = await _jalan(p, body=_body("dp nya 30% aja"))
    assert hasil["sumber"] == "llm" and hasil["slot"] == "dp" and hasil["value"] == "30%"


@pytest.mark.asyncio
async def test_jawab_tak_sah_dan_teks_bebas_jadi_tanya_balik():
    hasil, _ = await _jalan(Pemanggil([_jawab(intent="hapus_semua")]))
    assert hasil["sumber"] == "tak_sah" and hasil["intent"] == "clarify"
    hasil, _ = await _jalan(Pemanggil([{"type": "text", "text": "Pelanggan itu ada."}]))
    assert hasil["sumber"] == "tak_sah"


@pytest.mark.asyncio
async def test_putaran_berbatas():
    p = Pemanggil(*[[_guna(f"t{i}", "search_items", {"q": "kaos"})] for i in range(10)])
    hasil, _ = await _jalan(p)
    assert len(p.panggilan) == T.MAKS_PUTARAN and hasil["sumber"] == "tak_sah"


@pytest.mark.asyncio
async def test_galat_model_ramah_tanpa_isi_ketikan_di_logger(caplog):
    caplog.set_level(logging.DEBUG)
    hasil, conn = await _jalan(Pemanggil(galat=GalatModel("sibuk", "x")), body=_body("rahasia 0812-9999-0000"))
    assert hasil["sumber"] == "galat" and hasil["reply"] == T.PESAN_SIBUK
    assert "0812" not in caplog.text and "rahasia" not in caplog.text


@pytest.mark.asyncio
async def test_audit_giliran_untuk_pagar_biaya_v1():
    audit = Audit()
    await _jalan(Pemanggil([_jawab()]), audit=audit)
    b = audit.baris[-1]
    assert b.peristiwa == "giliran" and b.kelas == "v2_tafsir" and b.tenant_id == "kaos" and b.biaya_usd > 0


# ---------------------------------------------------------------- tool baca + log
@pytest.mark.asyncio
async def test_tagihan_pelanggan_tenant_lain_ditolak_tanpa_query_ar():
    conn = Conn(pelanggan=None)
    isi = await T.jalankan_tool(conn, "kaos", "open_invoices", {"customer_id": CID})
    assert isi == {"galat": "pelanggan tidak ditemukan"}
    assert not any(isinstance(x, tuple) and x[1] == T.SQL_TAGIHAN for x in conn.log)
    assert conn.log[0][2] == (uuid.UUID(CID), "kaos")


@pytest.mark.asyncio
async def test_stok_barang_tak_dilacak_tanpa_angka():
    conn = Conn(stok={"nama": "Sablon", "jenis": "non_inventory", "satuan": "pcs", "stok": 0})
    assert (await T.jalankan_tool(conn, "kaos", "stock", {"item_id": CID}))["stok"] is None


@pytest.mark.asyncio
async def test_log_buang_basi_lalu_tulis_milik_token_satu_transaksi():
    _, conn = await _jalan(Pemanggil([_jawab()]))
    langkah = [x if isinstance(x, str) else x[1] for x in conn.log if x in ("BEGIN", "COMMIT") or
               (isinstance(x, tuple) and x[1] in (T.SQL_BUANG_LOG, T.SQL_LOG))]
    assert langkah == ["BEGIN", T.SQL_BUANG_LOG, T.SQL_LOG, "COMMIT"]
    log = _log_baris(conn)[0][2]
    assert log[:5] == ("kaos", "u1", "items", "pelanggan rocky ada tagihan?", "answer_question")
    assert "tenant_id = $1" in T.SQL_BUANG_LOG and "INTERVAL '30 days'" in T.SQL_BUANG_LOG


def _sql_konstanta(modul):
    pohon = ast.parse(inspect.getsource(modul))
    return {n.targets[0].id: ast.literal_eval(n.value) if isinstance(n.value, ast.Constant) else None
            for n in pohon.body if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "").startswith("SQL")}


def test_pemindai_tool_baca_tanpa_jalur_tulis():
    from app.milkybook.v2 import cari_barang, cari_pelanggan
    tulis = re.compile(r"\b(INSERT|UPDATE|DELETE|TRUNCATE|ALTER|DROP|CREATE)\b", re.I)
    for modul in (cari_pelanggan, cari_barang):
        for nama, sql in _sql_konstanta(modul).items():
            assert sql and not tulis.search(sql), (modul.__name__, nama)
    for nama in ("SQL_TAGIHAN", "SQL_PELANGGAN_ADA", "SQL_STOK"):
        assert not tulis.search(getattr(T, nama)), nama
    src = inspect.getsource(T.jalankan_tool)
    assert "execute(" not in src and "transaction(" not in src
    assert set(T.TOOL_BACA) == {t["name"] for t in T.TOOLS} - {"jawab"}
    assert all(t["strict"] and t["input_schema"]["additionalProperties"] is False for t in T.TOOLS)


def test_log_ketikan_tak_pernah_dibaca_aplikasi():
    """Tenant lain tak bisa membaca log: tak ada satu pun jalur BACA atas tabel ini di app/ (hanya buang + tulis)."""
    app = pathlib.Path(T.__file__).resolve().parents[2]
    pemakai = [str(f.relative_to(app)) for f in app.rglob("*.py") if "milkybook_v2_tafsir_log" in f.read_text()]
    assert pemakai == ["milkybook/v2/tafsir.py"]
    sql_tabel = {n: getattr(T, n) for n in dir(T) if n.startswith("SQL") and "milkybook_v2_tafsir_log" in getattr(T, n)}
    assert sorted(sql_tabel) == ["SQL_BUANG_LOG", "SQL_LOG"]
    assert T.SQL_BUANG_LOG.startswith("DELETE ") and T.SQL_LOG.startswith("INSERT ")
    # nama tabel di sumber hanya di dua konstanta itu: tak ada kueri lain yang menyebutnya
    assert pathlib.Path(T.__file__).read_text().count("milkybook_v2_tafsir_log") == 2


def test_migrasi_v470_tanpa_kendali_transaksi():
    mig = (pathlib.Path(T.__file__).resolve().parents[4] / "migrations" / "V470__milkybook_v2_tafsir_log.sql").read_text()
    for kata in ("BEGIN;", "COMMIT;", "CONCURRENTLY"):
        assert kata not in mig
    for s in ("'llm'", "'batas'", "'galat'", "'tak_sah'", "'penjaga'"):
        assert s in mig, s


# ---------------------------------------------------------------- rute
def _app(flag=True, dep=None):
    conn = Conn()

    @asynccontextmanager
    async def koneksi():
        yield conn

    async def flag_fn(t):
        return flag

    app = FastAPI()

    @app.middleware("http")
    async def pengguna(request: Request, lanjut):
        t = request.headers.get("X-Uji-Tenant")
        if t:
            request.state.user = {"tenant_id": t, "user_id": "u1"}
        return await lanjut(request)

    app.include_router(buat_router_v2(flag_fn=flag_fn, conn_fn=koneksi, izin_fn=None, tafsir_fn=dep),
                       prefix="/api/milkybook/v2")
    return TestClient(app), conn


def test_rute_flag_mati_404_tanpa_token_401_nol_model():
    p = Pemanggil([_jawab()])
    dep = lambda: {"pemanggil": p, "audit": Audit(), "batas_fn": lambda t: None}
    c, conn = _app(flag=False, dep=dep)
    assert c.post("/api/milkybook/v2/tafsir", headers={"X-Uji-Tenant": "kaos"}, json={"teks": "x"}).status_code == 404
    assert c.post("/api/milkybook/v2/tafsir", json={"teks": "x"}).status_code == 401
    assert p.panggilan == [] and conn.log == []


def test_rute_validasi_dan_jawaban():
    p = Pemanggil([_jawab()])
    c, conn = _app(dep=lambda: {"pemanggil": p, "audit": Audit(), "batas_fn": lambda t: None})
    h = {"X-Uji-Tenant": "kaos"}
    assert c.post("/api/milkybook/v2/tafsir", headers=h, json={"teks": "x" * 501}).status_code == 422
    assert c.post("/api/milkybook/v2/tafsir", headers=h, json={"teks": "x", "riwayat": [{"peran": "bot", "teks": "a"}] * 7}
                  ).status_code == 422
    assert p.panggilan == []
    r = c.post("/api/milkybook/v2/tafsir", headers=h, json={"teks": "halo", "tahap": "items"})
    assert r.status_code == 200 and r.json()["sumber"] == "llm"
