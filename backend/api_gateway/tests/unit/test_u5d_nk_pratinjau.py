"""U5-D (4 Okt 2026): pratinjau post/void Nota Kredit + inti void yang diekstrak + idempotensi CN_POST/CN_VOID.

Penjaga: (1) handler void memanggil INTI yang sama dengan pratinjau, inti memuat semua langkah lama (kunci, jurnal balik,
companion COGS, ledger persediaan, saldo kredit, V312) -- paritas token-per-token dengan handler lama diukur di luar suite
(skrip paritas, 6921 = 6921); (2) penentu mengumpulkan SEMUA blok (bukan berhenti di pertama); (3) pratinjau SELALU rollback
(transaksi keluar dengan galat), tak menjalankan inti bila ada blok, galat inti ikut sebagai blok terakhir; (4) pratinjau
izinnya = izin tulisnya; (5) idempotensi memakai pola aksi (kunci {PREFIX}:{user}:{dok}:{kunci}, 409 bila isi beda)."""
import asyncio
import inspect
import uuid
from datetime import date

import pytest
from fastapi import HTTPException

from app.middleware import permission_middleware as PM
from app.routers import credit_notes as C


def _j(c):
    return asyncio.run(c)


# ── struktur ────────────────────────────────────────────────────────────────────────────────────────────────────

def test_handler_void_dan_pratinjau_memakai_inti_yang_sama():
    h = inspect.getsource(C.void_credit_note)
    p = inspect.getsource(C._pratinjau_nk)
    assert "void_nota_kredit_core" in h and "void_nota_kredit_core" in p
    assert "posting_nota_kredit" in inspect.getsource(C.post_credit_note) and "posting_nota_kredit" in p
    # handler tak lagi memuat isi void (tak ada jurnal/SQL inline): hanya idempotensi + pemanggilan inti
    assert "journal_entries" not in h and "journal_lines" not in h and "CREDIT_NOTE_VOID:" not in h


def test_inti_void_memuat_semua_langkah_lama():
    inti = inspect.getsource(C.void_nota_kredit_core)
    for penanda in ("CREDIT_NOTE_VOID:", "pg_advisory_xact_lock", "Draft credit note deleted", "amount_applied",
                    "amount_refunded", "created_deposit_id", "fiscal_periods", "pulihkan_saat_void", "get_next_journal_number",
                    "reversal_of_id", "UPDATE journal_entries SET status = 'POSTED'", "reversed_by_id", "CREDIT_NOTE_COGS",
                    "record_inventory_reversal", "segarkan_cache_piutang_faktur", "voided_reason = $3", "'void'"):
        assert penanda in inti, penanda
    kode_inti = inti.split('"""', 2)[2]  # tanpa docstring
    assert "body" not in kode_inti  # alasan datang sebagai parameter `reason`, bukan body request


def test_idempotensi_aksi_pada_post_dan_void():
    for fn, awalan in ((C.post_credit_note, '"CN_POST"'), (C.void_credit_note, '"CN_VOID"')):
        src = inspect.getsource(fn)
        rapat = " ".join(src.split())
        assert "idem_buat" in src and "mulai_aksi" in src and "_ib.simpan" in src
        # awalan kunci dipakai di KEDUA pemanggilan (mulai_aksi: kunci per-dokumen; simpan: source_type tercatat)
        assert f"{awalan}, credit_note_id" in rapat, "mulai_aksi tanpa awalan/dokumen yang benar"
        assert f"_ib.simpan(conn, ctx, _kp, _sd, {awalan}," in rapat
        assert "response" in inspect.signature(fn).parameters  # header X-Idempotent-Replay
    # urutan: kunci IDEM diambil SEBELUM inti (replay tak menulis ulang)
    src = inspect.getsource(C.void_credit_note)
    assert src.index("mulai_aksi") < src.index("void_nota_kredit_core")


@pytest.mark.parametrize("path,izin", [
    ("/api/credit-notes/X/post/preview", ("credit_note", "C")),
    ("/api/credit-notes/X/void/preview", ("credit_note", "C")),
    ("/api/credit-notes/X/post", ("credit_note", "C")),
    ("/api/credit-notes/X/void", ("credit_note", "C")),
])
def test_izin_pratinjau_sama_dengan_tulisnya(path, izin):
    assert PM.PermissionMiddleware(lambda *a: None, False)._find_permission(path, "POST") == izin


def test_rute_pratinjau_terdaftar():
    rute = {(r.path, tuple(sorted(r.methods))) for r in C.router.routes}
    assert ("/{credit_note_id}/post/preview", ("POST",)) in rute
    assert ("/{credit_note_id}/void/preview", ("POST",)) in rute


# ── tiruan koneksi ──────────────────────────────────────────────────────────────────────────────────────────────

class _Tx:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        self.conn.tx_masuk += 1
        return self

    async def __aexit__(self, et, ev, tb):
        self.conn.tx_keluar.append(et)
        return False


class _Conn:
    def __init__(self, cn, apps=(), deposit=None, ar=None):
        self.cn, self.deposit, self.ar = cn, deposit, ar
        self.tx_masuk, self.tx_keluar, self.sql = 0, [], []
        self.periode = None

    def transaction(self):
        return _Tx(self)

    async def fetchrow(self, sql, *a):
        self.sql.append(sql)
        if "FROM credit_notes" in sql:
            return self.cn if a[-1] == "t1" or a[1] == "t1" else None
        if "FROM fiscal_periods" in sql:
            return {"status": self.periode} if self.periode else None
        if "FROM customer_deposits" in sql:
            return self.deposit
        if "compute_ar_outstanding" in sql:
            return {"outstanding": self.ar} if self.ar is not None else None
        return None

    async def fetch(self, sql, *a):
        self.sql.append(sql)
        return []

    async def fetchval(self, sql, *a):
        self.sql.append(sql)
        return "posted"


def _cn(**u):
    d = {"id": uuid.uuid4(), "credit_note_number": "CN-1", "status": "posted", "total_amount": 100, "amount_applied": 0,
         "amount_refunded": 0, "original_invoice_id": None, "created_deposit_id": None, "customer_id": uuid.uuid4(),
         "credit_note_date": date(2026, 10, 4)}
    d.update(u)
    return d


@pytest.fixture(autouse=True)
def _patch(monkeypatch):
    async def hari(conn, tid):
        return date(2026, 10, 4)

    async def aman(*a, **k):
        return None
    monkeypatch.setattr(C, "tanggal_dokumen", hari)
    monkeypatch.setattr(C.cn_tertunda, "pulihkan_saat_void", aman)


CTX = {"tenant_id": "t1", "user_id": uuid.uuid4()}


def kode(blok):
    return [b["code"] for b in blok]


# ── penentu: kumpulkan SEMUA blok ─────────────────────────────────────────────────────────────────────────────────

def test_post_status_bukan_draf_diblok():
    cn = _cn(status="posted")
    b = _j(C._rencana_nk(_Conn(cn), CTX, cn["id"], "post", None))
    assert kode(b) == ["CN_NOT_DRAFT"] and "terbit" in b[0]["message"]


def test_post_draf_periode_tutup_dan_faktur_asal_keduanya_tampil(monkeypatch):
    cn = _cn(status="draft", original_invoice_id=uuid.uuid4())
    c = _Conn(cn)
    c.periode = "CLOSED"

    async def faktur(conn, tid, fid, pel):
        return {"id": fid}

    async def muat(*a, **k):
        raise HTTPException(status_code=400, detail={"code": "CN_EXCEEDS_INVOICE", "message": "Melebihi sisa faktur."})
    monkeypatch.setattr(C, "faktur_tenant_untuk_pelanggan", faktur)
    monkeypatch.setattr(C, "pastikan_cn_muat_faktur", muat)
    b = _j(C._rencana_nk(c, CTX, cn["id"], "post", None))
    assert kode(b) == ["CN_EXCEEDS_INVOICE", "PERIOD_CLOSED"]  # dua blok sekaligus, kode dari detail galat dipertahankan


def test_void_semua_blok_sekaligus(monkeypatch):
    dep_id = uuid.uuid4()
    cn = _cn(status="partial", amount_applied=40, amount_refunded=10, created_deposit_id=dep_id)
    c = _Conn(cn, deposit={"id": dep_id, "deposit_number": "DEP-1", "amount": 100, "status": "posted"})
    c.periode = "LOCKED"
    import app.routers.customer_deposits as CD

    async def sisa(conn, tid, did):
        return 30  # < amount 100 -> sudah terpakai
    monkeypatch.setattr(CD, "compute_deposit_remaining", sisa)
    b = _j(C._rencana_nk(c, CTX, cn["id"], "void", None))
    assert kode(b) == ["VOID_REASON_REQUIRED", "CN_HAS_APPLICATIONS", "CN_HAS_REFUNDS", "CN_CREDIT_USED", "PERIOD_CLOSED"]
    assert [x["status"] for x in b][0] == 422


def test_void_draf_tanpa_alasan_hanya_blok_alasan_dan_draf_valid():
    cn = _cn(status="draft")
    assert kode(_j(C._rencana_nk(_Conn(cn), CTX, cn["id"], "void", None))) == ["VOID_REASON_REQUIRED"]
    assert _j(C._rencana_nk(_Conn(cn), CTX, cn["id"], "void", "salah input")) == []


def test_void_sudah_void_dan_nk_tak_ada():
    cn = _cn(status="void")
    assert kode(_j(C._rencana_nk(_Conn(cn), CTX, cn["id"], "void", "x"))) == ["CN_ALREADY_VOID"]
    with pytest.raises(HTTPException) as e:
        _j(C._rencana_nk(_Conn(cn), {"tenant_id": "t2", "user_id": None}, cn["id"], "void", "x"))
    assert e.value.status_code == 404


# ── pratinjau: selalu rollback ───────────────────────────────────────────────────────────────────────────────────

class _Pool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        conn = self.conn

        class _A:
            async def __aenter__(s):
                return conn

            async def __aexit__(s, *a):
                return False
        return _A()


def _jalankan(monkeypatch, cn, aksi, reason=None, inti_galat=None, ar=(50.0, 0.0)):
    c = _Conn(cn)
    panggilan = []
    urut = iter(ar)

    async def pool():
        return _Pool(c)

    async def inti(conn, ctx, cid, *a):
        panggilan.append("inti")
        if inti_galat:
            raise inti_galat

    async def jurnal(conn, tid, cid):
        return [{"journal_number": "J1", "source_type": "CREDIT_NOTE", "lines": []}]

    async def sisa(conn, tid, fid):
        return next(urut)
    async def faktur_ok(conn, tid, fid, pel):
        return {"id": fid}

    async def nop(*a, **k):
        return None
    monkeypatch.setattr(C, "faktur_tenant_untuk_pelanggan", faktur_ok)
    monkeypatch.setattr(C, "pastikan_cn_muat_faktur", nop)
    monkeypatch.setattr(C, "periksa_bisa_dinotakan", nop)
    monkeypatch.setattr(C, "get_pool", pool)
    monkeypatch.setattr(C, "posting_nota_kredit", inti)
    monkeypatch.setattr(C, "void_nota_kredit_core", inti)
    monkeypatch.setattr(C, "_jurnal_tx_ini_nk", jurnal)
    monkeypatch.setattr(C, "_sisa_faktur_nk", sisa)
    out = _j(C._pratinjau_nk(CTX, cn["id"], aksi, reason))
    return c, panggilan, out


def test_pratinjau_sukses_menjalankan_inti_lalu_selalu_rollback(monkeypatch):
    cn = _cn(status="draft", original_invoice_id=uuid.uuid4())
    c, p, out = _jalankan(monkeypatch, cn, "post")
    assert p == ["inti"] and out["can_proceed"] is True and out["blocks"] == [] and out["preview"] is True
    assert out["journals"][0]["journal_number"] == "J1"
    assert out["invoice"]["remaining_before"] == 50.0 and out["invoice"]["remaining_after"] == 0.0
    # transaksi luar KELUAR DENGAN GALAT (rollback), savepoint dalam keluar bersih
    assert c.tx_keluar[-1] is C._BatalkanPratinjauNk and c.tx_keluar[0] is None


def test_pratinjau_dengan_blok_tak_menjalankan_inti_dan_tanpa_jurnal(monkeypatch):
    cn = _cn(status="posted")
    c, p, out = _jalankan(monkeypatch, cn, "post")
    assert p == [] and out["can_proceed"] is False and out["blocks"][0]["code"] == "CN_NOT_DRAFT" and out["journals"] == []
    assert out["before"] == out["after"] and c.tx_keluar[-1] is C._BatalkanPratinjauNk


def test_pratinjau_galat_inti_jadi_blok_terakhir(monkeypatch):
    cn = _cn(status="draft")
    c, p, out = _jalankan(monkeypatch, cn, "post", inti_galat=HTTPException(status_code=500, detail="Akun tak ada"))
    assert p == ["inti"] and out["can_proceed"] is False and out["blocks"][-1] == {"code": "POST_REJECTED", "message": "Akun tak ada"}
    assert out["journals"] == [] and c.tx_keluar[-1] is C._BatalkanPratinjauNk


def test_pratinjau_void_draf_membawa_alasan_di_payload(monkeypatch):
    cn = _cn(status="draft")
    _, p, out = _jalankan(monkeypatch, cn, "void", reason="salah input")
    assert out["payload"] == {"reason": "salah input"} and out["action"] == "void" and p == ["inti"]


def test_kontrol_merah_penentu_dan_pratinjau_bisa_gagal(monkeypatch):
    # tanpa alasan, void WAJIB terblok -> inti tak jalan (kalau penentu rusak, ujian ini merah)
    cn = _cn(status="draft")
    _, p, out = _jalankan(monkeypatch, cn, "void", reason="  ")
    assert p == [] and out["blocks"][0]["code"] == "VOID_REASON_REQUIRED"
