"""BUG-193: galat post/void Biaya berkode + kalimat Indonesia (tulis dan pratinjau dari SATU penentu), pratinjau memuat keadaan/teks SESUDAH dari penentu detail (BD),
null bila ditolak. Paritas nyata = uji salinan."""
import asyncio
import uuid
from types import SimpleNamespace

import pytest
from starlette.requests import Request

import app.routers.expenses as EX
from app.services import biaya_daftar as BD
from app.services.galat_berkode import GalatBerkode

T = "kaos-biru-konveksi"
EID = uuid.uuid4()
CTX = {"tenant_id": T, "user_id": "00000000-0000-0000-0000-000000000001"}


def req():
    r = Request({"type": "http", "method": "POST", "path": "/", "headers": []})
    r.state.user = dict(CTX)
    return r


def jalan(aw):
    return asyncio.run(aw)


class Conn:
    """Koneksi palsu: fetchrow pertama = baris biaya (atau None); baris 'sesudah' (status, paid_through_name, jb) untuk pembacaan keadaan sesudah."""
    def __init__(self, baris=None, sesudah=None):
        self.baris, self.sesudah, self.sql = baris, sesudah, []

    async def execute(self, sql, *a):
        self.sql.append(sql)

    async def fetchrow(self, sql, *a):
        self.sql.append(sql)
        if "jb" in sql and "paid_through_name" in sql and self.sesudah is not None:
            return self.sesudah
        return self.baris

    async def fetch(self, *a):
        return []

    def transaction(self):
        c = self

        class Tx:
            async def start(s):
                return None

            async def rollback(s):
                c.sql.append("ROLLBACK")
        return Tx()


@pytest.fixture
def pasang(monkeypatch):
    kat = SimpleNamespace(conn=Conn())

    class Pool:
        def acquire(self):
            class Ac:
                async def __aenter__(s):
                    return kat.conn

                async def __aexit__(s, *e):
                    return False
            return Ac()

    async def gp():
        return Pool()

    async def ok(*a, **k):
        return None

    async def efek(conn, tid, eid, aksi):
        return [], []
    monkeypatch.setattr(EX, "get_pool", gp)
    monkeypatch.setattr(EX, "_ensure_role_preconditions", ok)
    monkeypatch.setattr(EX, "tolak_void_bila_terekonsiliasi", ok)
    monkeypatch.setattr(EX, "_efek_jurnal", efek)
    return kat


POST_GALAT = [(None, 404, "EXPENSE_TIDAK_DITEMUKAN", "Biaya tidak ditemukan."),
              ({"status": "posted", "journal_id": None}, 400, "BIAYA_BUKAN_DRAF", "Hanya biaya draf yang bisa diterbitkan."),
              ({"status": "void", "journal_id": None}, 400, "BIAYA_BUKAN_DRAF", "Hanya biaya draf yang bisa diterbitkan."),
              ({"status": "draft", "journal_id": uuid.uuid4()}, 409, "BIAYA_SUDAH_BERJURNAL", "Biaya ini sudah berjurnal.")]
VOID_GALAT = [(None, 404, "EXPENSE_TIDAK_DITEMUKAN", "Biaya tidak ditemukan."),
              ({"status": "void"}, 400, "BIAYA_SUDAH_BATAL", "Biaya ini sudah dibatalkan."),
              ({"status": "draft"}, 400, "BIAYA_BUKAN_TERBIT", "Hanya biaya yang sudah diterbitkan yang bisa dibatalkan; draf dihapus.")]


@pytest.mark.parametrize("baris,status,kode,kalimat", POST_GALAT)
def test_post_galat_berkode_tulis_dan_pratinjau_satu_penentu(pasang, baris, status, kode, kalimat):
    pasang.conn.baris = baris
    with pytest.raises(GalatBerkode) as e:
        jalan(EX._posting_biaya(pasang.conn, CTX, EID))
    assert (e.value.status_code, e.value.code) == (status, kode) and e.value.detail == kalimat
    d = jalan(EX.preview_post_expense(req(), EID))["data"]
    assert d["blocks"] == [{"code": kode, "message": kalimat}] and d["can_proceed"] is False and d["journals"] == []
    assert (d["biaya_keadaan_sesudah"], d["position_text_sesudah"]) == (None, None)       # ditolak -> tak ada keadaan sesudah
    assert "ROLLBACK" in pasang.conn.sql                                                    # pratinjau selalu dibatalkan


@pytest.mark.parametrize("baris,status,kode,kalimat", VOID_GALAT)
def test_void_galat_berkode_tulis_dan_pratinjau_satu_penentu(pasang, baris, status, kode, kalimat):
    pasang.conn.baris = baris
    with pytest.raises(GalatBerkode) as e:
        jalan(EX._batal_biaya(pasang.conn, CTX, EID, EX.VoidExpenseRequest(reason="x")))
    assert (e.value.status_code, e.value.code) == (status, kode) and e.value.detail == kalimat
    d = jalan(EX.preview_void_expense(req(), EID, EX.VoidExpensePreviewRequest(reason="salah catat")))["data"]
    assert d["blocks"] == [{"code": kode, "message": kalimat}] and d["can_proceed"] is False
    assert (d["biaya_keadaan_sesudah"], d["position_text_sesudah"]) == (None, None)


def test_tak_ada_pesan_inggris_tersisa_di_jalur_post_void():
    import inspect
    src = inspect.getsource(EX._posting_biaya) + inspect.getsource(EX._batal_biaya)
    for lama in ("Expense not found", "Only draft expenses", "Expense already", "Only posted expenses"):
        assert lama not in src


def test_pratinjau_post_sesudah_dari_penentu_detail(pasang, monkeypatch):
    async def tulis(conn, ctx, eid):
        return {}
    monkeypatch.setattr(EX, "_posting_biaya", tulis)
    pasang.conn.sesudah = {"status": "posted", "paid_through_name": "BCA", "jb": 0}
    d = jalan(EX.preview_post_expense(req(), EID))["data"]
    assert d["can_proceed"] is True and d["blocks"] == []
    assert d["biaya_keadaan_sesudah"] == BD.keadaan("posted") == "dibayar"
    assert d["position_text_sesudah"] == BD.position_text("posted", "BCA", False) == "Dibayar dari BCA · tanpa bukti"
    pasang.conn.sesudah = {"status": "posted", "paid_through_name": "BCA", "jb": 2}
    assert jalan(EX.preview_post_expense(req(), EID))["data"]["position_text_sesudah"] == "Dibayar dari BCA"


def test_pratinjau_void_sesudah_dibatalkan_dan_alasan_kosong_null(pasang, monkeypatch):
    async def batal(conn, ctx, eid, body):
        return {}
    monkeypatch.setattr(EX, "_batal_biaya", batal)
    pasang.conn.sesudah = {"status": "void", "paid_through_name": "BCA", "jb": 0}
    d = jalan(EX.preview_void_expense(req(), EID, EX.VoidExpensePreviewRequest(reason="salah catat")))["data"]
    assert (d["can_proceed"], d["biaya_keadaan_sesudah"], d["position_text_sesudah"]) == (True, "dibatalkan", "Dibatalkan")
    d2 = jalan(EX.preview_void_expense(req(), EID, None))["data"]                    # alasan kosong: blok BIAYA_REASON_REQUIRED -> ditolak -> null
    assert d2["can_proceed"] is False and [b["code"] for b in d2["blocks"]] == ["BIAYA_REASON_REQUIRED"]
    assert (d2["biaya_keadaan_sesudah"], d2["position_text_sesudah"]) == (None, None)


def test_keadaan_sesudah_dibaca_tersaring_tenant(pasang):
    pasang.conn.sesudah = {"status": "posted", "paid_through_name": None, "jb": 0}
    jalan(EX._keadaan_sesudah(pasang.conn, T, EID))
    assert "tenant_id = $2" in pasang.conn.sql[-1]
