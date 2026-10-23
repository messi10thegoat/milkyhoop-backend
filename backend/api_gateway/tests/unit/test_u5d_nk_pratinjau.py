"""U5-D (4 Okt 2026) + NK-RENCANA-B (23 Okt 2026): pratinjau post/void Nota Kredit + inti void + idempotensi CN_POST/CN_VOID.

Penjaga: (1) inti post/void memanggil PENENTU BERSAMA (services/nota_kredit_rencana) sesudah kunci lama; pratinjau = penentu yang sama di
transaksi READ ONLY (dulu: inti penulis di savepoint lalu rollback); inti void memuat semua langkah lama; (2) penentu mengumpulkan SEMUA
blok (pratinjau) / melempar blok pertama persis (tulis); (3) pratinjau nol tulis/kunci (koneksi tiruan menolak execute), jurnal rencana
tanpa nomor; (4) pratinjau izinnya = izin tulisnya; (5) idempotensi memakai pola aksi (kunci {PREFIX}:{user}:{dok}:{kunci})."""
import asyncio
import inspect
import uuid
from datetime import date

import pytest
from fastapi import HTTPException

from app.middleware import permission_middleware as PM
from app.routers import credit_notes as C
from app.services import nota_kredit_rencana as NKR


def _j(c):
    return asyncio.run(c)


# ── struktur ────────────────────────────────────────────────────────────────────────────────────────────────────

def test_handler_void_dan_pratinjau_memakai_penentu_yang_sama():
    h = inspect.getsource(C.void_credit_note)
    p = " ".join(inspect.getsource(C._pratinjau_nk).split())
    assert "void_nota_kredit_core" in h and "posting_nota_kredit" in inspect.getsource(C.post_credit_note)
    assert "conn.transaction(readonly=True)" in p and "NKR.pratinjau_post_void_nk(conn, ctx, cn_id, aksi, reason)" in p
    assert "posting_nota_kredit" not in p and "void_nota_kredit_core" not in p
    assert "NKR.rencana_batal_nk(conn, ctx, credit_note_id, reason)" in inspect.getsource(C.void_nota_kredit_core)
    assert "NKR.rencana_posting_nk(conn, ctx, credit_note_id)" in inspect.getsource(C.posting_nota_kredit)
    # handler tak lagi memuat isi void (tak ada jurnal/SQL inline): hanya idempotensi + pemanggilan inti
    assert "journal_entries" not in h and "journal_lines" not in h and "CREDIT_NOTE_VOID:" not in h


def test_inti_void_memuat_semua_langkah_lama():
    inti = inspect.getsource(C.void_nota_kredit_core)
    for penanda in ("CREDIT_NOTE_VOID:", "pg_advisory_xact_lock", "Draf nota kredit dihapus", "NKR.rencana_batal_nk(",
                    "pulihkan_saat_void", "get_next_journal_number",
                    "reversal_of_id", "UPDATE journal_entries SET status = 'POSTED'", "reversed_by_id", "CREDIT_NOTE_COGS",
                    "record_inventory_reversal", "segarkan_cache_piutang_faktur", "voided_reason = $3", "'void'"):
        assert penanda in inti, penanda
    assert inti.index("CREDIT_NOTE_VOID:") < inti.index("NKR.rencana_batal_nk(") < inti.index("get_next_journal_number")
    pen = inspect.getsource(NKR.rencana_batal_nk)
    for penanda in ("amount_applied", "amount_refunded", "created_deposit_id", "fiscal_periods", "pulihkan_saat_void"):
        assert penanda in pen, penanda
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
    ("/api/credit-notes/X/post/preview", ("credit_note", "P")),   # U5-E: post=P, void=V (dulu semua C)
    ("/api/credit-notes/X/void/preview", ("credit_note", "V")),
    ("/api/credit-notes/X/post", ("credit_note", "P")),
    ("/api/credit-notes/X/void", ("credit_note", "V")),
])
def test_izin_pratinjau_sama_dengan_tulisnya(path, izin):
    assert PM.PermissionMiddleware(lambda *a: None, False)._find_permission(path, "POST") == izin


def test_rute_pratinjau_terdaftar():
    rute = {(r.path, tuple(sorted(r.methods))) for r in C.router.routes}
    assert ("/{credit_note_id}/post/preview", ("POST",)) in rute
    assert ("/{credit_note_id}/void/preview", ("POST",)) in rute


# ── tiruan koneksi (NK-RENCANA-B: penentu bersama, pratinjau READ ONLY -- execute = galat) ──────────────────────────

class _Conn:
    def __init__(self, cn, deposit=None, ar=0):
        self.cn, self.deposit, self.ar = cn, deposit, ar
        self.sql, self.periode = [], None

    async def execute(self, sql, *a):
        raise AssertionError(f"pratinjau/penentu menulis atau mengunci: {sql}")

    async def fetchrow(self, sql, *a):
        self.sql.append(sql)
        if "FROM credit_notes" in sql:
            return self.cn if a[-1] == "t1" or a[1] == "t1" else None
        if "FROM fiscal_periods" in sql:
            return {"status": self.periode} if self.periode else None
        if "FROM customer_deposits" in sql:
            return self.deposit
        return None

    async def fetch(self, sql, *a):
        self.sql.append(sql)
        return []

    async def fetchval(self, sql, *a):
        self.sql.append(sql)
        if "compute_ar_outstanding" in sql:
            return self.ar
        return None


def _cn(**u):
    d = {"id": uuid.uuid4(), "credit_note_number": "CN-1", "status": "posted", "total_amount": 100, "tax_amount": 0, "amount_applied": 0,
         "reason": "return", "amount_refunded": 0, "original_invoice_id": None, "original_invoice_number": None, "created_deposit_id": None,
         "customer_id": uuid.uuid4(), "customer_name": "X", "journal_id": None, "credit_note_date": date(2026, 10, 4)}
    d.update(u)
    return d


AKUN = uuid.uuid4()


@pytest.fixture(autouse=True)
def _patch(monkeypatch):
    async def hari(conn, tid):
        return date(2026, 10, 4)

    async def aman(*a, **k):
        return None

    async def akun(conn, tid, kode):
        return AKUN
    monkeypatch.setattr(C, "tanggal_dokumen", hari)
    monkeypatch.setattr(C.cn_tertunda, "pulihkan_saat_void", aman)
    monkeypatch.setattr(C, "resolve_account_id", akun)


CTX = {"tenant_id": "t1", "user_id": uuid.uuid4()}


def kode(r):
    return [b["code"] for b in r["blocks"].respons()]


# ── penentu: kumpulkan SEMUA blok (pratinjau), nol tulis/kunci ───────────────────────────────────────────────────────

def test_post_status_bukan_draf_diblok():
    cn = _cn(status="posted")
    r = _j(NKR.rencana_posting_nk(_Conn(cn), CTX, cn["id"], pratinjau=True))
    assert kode(r) == ["CN_NOT_DRAFT"] and "diterbitkan" in r["blocks"].respons()[0]["message"]


def test_post_draf_periode_tutup_dan_faktur_asal_keduanya_tampil(monkeypatch):
    cn = _cn(status="draft", original_invoice_id=uuid.uuid4())
    c = _Conn(cn, ar=1000)  # sisa faktur >= NK -> tanpa kelebihan KRD (peran Uang Muka tak diperlukan)
    c.periode = "CLOSED"

    async def faktur(conn, tid, fid, pel):
        return {"id": fid}

    async def muat(*a, **k):
        raise HTTPException(status_code=400, detail={"code": "CN_EXCEEDS_INVOICE", "message": "Melebihi sisa faktur."})
    monkeypatch.setattr(C, "faktur_tenant_untuk_pelanggan", faktur)
    monkeypatch.setattr(C, "pastikan_cn_muat_faktur", muat)
    r = _j(NKR.rencana_posting_nk(c, CTX, cn["id"], pratinjau=True))
    assert kode(r) == ["CN_EXCEEDS_INVOICE", "PERIOD_CLOSED"]  # dua blok sekaligus, kode dari detail galat dipertahankan


def test_post_tulis_blok_pertama_dilempar_persis():
    cn = _cn(status="posted")
    with pytest.raises(HTTPException) as e:
        _j(NKR.rencana_posting_nk(_Conn(cn), CTX, cn["id"]))
    assert e.value.status_code == 400 and "diterbitkan" in e.value.detail


def test_void_semua_blok_sekaligus(monkeypatch):
    dep_id = uuid.uuid4()
    cn = _cn(status="partial", amount_applied=40, amount_refunded=10, created_deposit_id=dep_id)
    c = _Conn(cn, deposit={"id": dep_id, "deposit_number": "DEP-1", "amount": 100, "status": "posted"})
    c.periode = "LOCKED"
    import app.routers.customer_deposits as CD

    async def sisa(conn, tid, did):
        return 30  # < amount 100 -> sudah terpakai
    monkeypatch.setattr(CD, "compute_deposit_remaining", sisa)
    r = _j(NKR.rencana_batal_nk(c, CTX, cn["id"], None, pratinjau=True))
    assert kode(r) == ["VOID_REASON_REQUIRED", "CN_HAS_APPLICATIONS", "CN_HAS_REFUNDS", "CN_CREDIT_USED", "PERIOD_CLOSED"]
    assert r["blocks"].daftar[0]["status"] == 422
    assert not any("FOR UPDATE" in q for q in c.sql)  # pratinjau: saldo KRD dibaca TANPA kunci


def test_void_draf_tanpa_alasan_hanya_blok_alasan_dan_draf_valid():
    cn = _cn(status="draft")
    assert kode(_j(NKR.rencana_batal_nk(_Conn(cn), CTX, cn["id"], None, pratinjau=True))) == ["VOID_REASON_REQUIRED"]
    r = _j(NKR.rencana_batal_nk(_Conn(cn), CTX, cn["id"], "salah input", pratinjau=True))
    assert kode(r) == [] and r["hapus"] is True


def test_void_sudah_void_dan_nk_tak_ada():
    cn = _cn(status="void")
    assert kode(_j(NKR.rencana_batal_nk(_Conn(cn), CTX, cn["id"], "x", pratinjau=True))) == ["CN_ALREADY_VOID"]
    with pytest.raises(HTTPException) as e:
        _j(NKR.rencana_batal_nk(_Conn(cn), {"tenant_id": "t2", "user_id": None}, cn["id"], "x", pratinjau=True))
    assert e.value.status_code == 404


# ── pratinjau: READ ONLY, jurnal rencana tanpa nomor ─────────────────────────────────────────────────────────────────

def test_pratinjau_terbit_jurnal_rencana_tanpa_nomor_dan_keadaan_sesudah():
    cn = _cn(status="draft", reason="other")
    out = _j(NKR.pratinjau_post_void_nk(_Conn(cn), CTX, cn["id"], "post", None))
    assert out["can_proceed"] is True and out["blocks"] == [] and out["preview"] is True
    j = out["journals"][0]
    assert j["journal_number"] is None and j["source_type"] == "CREDIT_NOTE"
    assert [(ln["debit"], ln["credit"]) for ln in j["lines"]] == [(100.0, 0.0), (0.0, 100.0)]  # Dr Retur / Cr Piutang (NK bebas)
    assert out["after"]["status"] == "posted" and out["after"]["remaining"] == 100.0 and out["invoice"] is None


def test_pratinjau_dengan_blok_tanpa_jurnal_dan_after_sama():
    cn = _cn(status="posted")
    out = _j(NKR.pratinjau_post_void_nk(_Conn(cn), CTX, cn["id"], "post", None))
    assert out["can_proceed"] is False and out["blocks"][0]["code"] == "CN_NOT_DRAFT" and out["journals"] == []
    assert out["before"] == out["after"]


def test_pratinjau_void_draf_membawa_alasan_dan_akan_dihapus():
    cn = _cn(status="draft")
    out = _j(NKR.pratinjau_post_void_nk(_Conn(cn), CTX, cn["id"], "void", "salah input"))
    assert out["payload"] == {"reason": "salah input"} and out["action"] == "void" and out["after"] == {"exists": False}


def test_kontrol_merah_penentu_dan_pratinjau_bisa_gagal():
    # tanpa alasan, void WAJIB terblok (kalau penentu rusak, ujian ini merah)
    cn = _cn(status="draft")
    out = _j(NKR.pratinjau_post_void_nk(_Conn(cn), CTX, cn["id"], "void", "  "))
    assert out["can_proceed"] is False and out["blocks"][0]["code"] == "VOID_REASON_REQUIRED"
