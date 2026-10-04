"""U10 (5 Okt 2026, MASTER GO): pratinjau + idempotensi NK apply / unapply / refund, dan dua perbaikan cacat refund.

Penjaga: (1) rute apply/unapply/refund hanya membungkus inti (apply_/unapply_/refund_nota_kredit_core) dengan idempotensi
aksi CN_APPLY/CN_UNAPPLY/CN_REFUND, dan PRATINJAU memanggil inti YANG SAMA; (2) refund: empat kombinasi
account_id/bank_account_id -> jelas (keduanya cocok / salah satu / tak ada = 422 / keduanya beda = 422); jurnal mengkredit
akun TERPECAHKAN (dulu hanya bank_account_id -> UUID(None) -> 500); (3) refund ke akun bank-linked membuat cermin
bank_transactions (withdrawal, negatif, bulat) di transaksi yang sama, akun non-bank tanpa cermin; (4) penentu pratinjau
mengumpulkan SEMUA blok; (5) izin pratinjau = izin tulisnya (A/V/P) -- dijaga test_u5e_izin_nk."""
import asyncio
import inspect
import re
import uuid
from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.routers import credit_notes as C
from app.schemas.credit_notes import ApplyCreditNoteItem, ApplyCreditNoteRequest, RefundCreditNoteRequest

T = "t1"
USER = uuid.UUID("0bccdb25-fdf0-4e99-9024-b9a20846f76c")
CN = uuid.uuid4()
COA = uuid.uuid4()
COA_LAIN = uuid.uuid4()
BANK = uuid.uuid4()
AR = uuid.uuid4()
CTX = {"tenant_id": T, "user_id": USER}


def _cn(**o):
    d = {"id": CN, "credit_note_number": "CN-1", "customer_id": "c1", "customer_name": "X", "status": "posted",
         "total_amount": Decimal("100000"), "amount_applied": 0, "amount_refunded": 0, "original_invoice_id": None,
         "journal_id": uuid.uuid4(), "created_deposit_id": None, "credit_note_date": date(2026, 10, 1)}
    d.update(o)
    return d


class _Konn:
    """Koneksi tiruan: jawab per potongan SQL; catat semua tulisan. bank_by_coa = hasil reverse-lookup bank."""
    def __init__(self, cn=None, bank_coa=COA, bank_by_coa=None, akun_ada=True, periode=None):
        self.cn, self.bank_coa, self.bank_by_coa, self.akun_ada, self.periode = cn or _cn(), bank_coa, bank_by_coa, akun_ada, periode
        self.exec, self.sql = [], []

    async def execute(self, sql, *a):
        self.exec.append((" ".join(sql.split()), a))
        return "UPDATE 1"

    async def fetchval(self, sql, *a):
        self.sql.append((sql, a))
        if "get_next_journal_number" in sql:
            return "RF-0001"
        if "FROM bank_accounts WHERE tenant_id" in sql:
            return self.bank_by_coa
        return None

    async def fetchrow(self, sql, *a):
        self.sql.append((sql, a))
        if "FROM credit_notes" in sql:
            return self.cn
        if "FROM bank_accounts WHERE id" in sql:
            return {"coa_id": self.bank_coa} if self.bank_coa else None
        if "FROM chart_of_accounts" in sql:
            return {"id": a[0], "code": "1-1", "name": "Kas"} if self.akun_ada else None
        if "fiscal_periods" in sql:
            return {"status": self.periode} if self.periode else None
        return None

    async def fetch(self, sql, *a):
        self.sql.append((sql, a))
        return []


def _body(**o):
    d = dict(amount=Decimal("40000"), refund_date=date(2026, 10, 5), payment_method="transfer",
             account_id=None, bank_account_id=None)
    d.update(o)
    return RefundCreditNoteRequest(**d)


def _jalan(coro):
    return asyncio.run(coro)


# ---------------- (2) empat kombinasi akun ----------------

def test_akun_hanya_bank_account_id_dipecahkan_ke_coa_rekening():
    k = _Konn()
    coa, ba = _jalan(C.resolusi_akun_refund(k, T, _body(bank_account_id=str(BANK))))
    assert coa == COA and ba == BANK


def test_akun_hanya_account_id():
    k = _Konn()
    coa, ba = _jalan(C.resolusi_akun_refund(k, T, _body(account_id=str(COA))))
    assert coa == COA and ba is None


def test_akun_keduanya_cocok_lolos_keduanya_beda_422():
    k = _Konn()
    coa, ba = _jalan(C.resolusi_akun_refund(k, T, _body(account_id=str(COA), bank_account_id=str(BANK))))
    assert coa == COA and ba == BANK
    with pytest.raises(HTTPException) as e:
        _jalan(C.resolusi_akun_refund(k, T, _body(account_id=str(COA_LAIN), bank_account_id=str(BANK))))
    assert e.value.status_code == 422 and e.value.detail["code"] == "CN_REFUND_AKUN_TAK_COCOK"


def test_akun_tak_satu_pun_422_jelas():
    with pytest.raises(HTTPException) as e:
        _jalan(C.resolusi_akun_refund(_Konn(), T, _body()))
    assert e.value.status_code == 422 and e.value.detail["code"] == "CN_REFUND_AKUN_WAJIB"
    assert "rekening bank atau akun kas/bank" in e.value.detail["message"]


def test_akun_tak_valid_atau_bukan_milik_tenant_ditolak():
    with pytest.raises(HTTPException) as e:
        _jalan(C.resolusi_akun_refund(_Konn(), T, _body(account_id="bukan-uuid")))
    assert e.value.status_code == 422
    with pytest.raises(HTTPException) as e2:
        _jalan(C.resolusi_akun_refund(_Konn(bank_coa=None), T, _body(bank_account_id=str(BANK))))
    assert e2.value.status_code == 400
    with pytest.raises(HTTPException) as e3:
        _jalan(C.resolusi_akun_refund(_Konn(akun_ada=False), T, _body(account_id=str(COA))))
    assert e3.value.status_code == 400
    # tenant eksplisit di kedua pencarian
    k = _Konn()
    _jalan(C.resolusi_akun_refund(k, T, _body(bank_account_id=str(BANK))))
    assert all(a[-1] == T for s, a in k.sql if "bank_accounts WHERE id" in s or "FROM chart_of_accounts" in s)


# ---------------- inti refund: kredit akun terpecahkan + cermin bank ----------------

@pytest.fixture
def refund_env(monkeypatch):
    async def ar(conn, tid, kode):
        return AR
    monkeypatch.setattr(C, "resolve_account_id", ar)
    cermin = []

    async def mirror(conn, **kw):
        cermin.append(kw)
        return uuid.uuid4()
    import app.services.bank_sync as BS
    monkeypatch.setattr(BS, "create_bank_transaction_for_journal", mirror)
    return cermin


def _kredit_jurnal(k):
    """account_id baris jurnal yang di-KREDIT (baris INSERT journal_lines dgn debit 0)."""
    for s, a in k.exec:
        if "INSERT INTO journal_lines" in s and "0, $4" in s:
            return a[2]
    raise AssertionError("baris kredit jurnal tak ditemukan")


def test_refund_hanya_bank_account_id_tak_lagi_500_dan_mengkredit_coa_rekening(refund_env):
    k = _Konn(bank_by_coa=None)
    out = _jalan(C.refund_nota_kredit_core(k, CTX, CN, _body(bank_account_id=str(BANK))))
    assert out["success"] and _kredit_jurnal(k) == COA
    ins = next(a for s, a in k.exec if "INSERT INTO credit_note_refunds" in s)
    assert ins[6] == COA and ins[7] == BANK  # account_id & bank_account_id tersimpan konsisten
    assert len(refund_env) == 1  # rekening bank eksplisit -> cermin


def test_refund_cermin_bank_withdrawal_negatif_bulat_di_transaksi_yang_sama(refund_env):
    k = _Konn(bank_by_coa=BANK)  # account_id bank-linked lewat reverse-lookup CoA
    _jalan(C.refund_nota_kredit_core(k, CTX, CN, _body(account_id=str(COA))))
    assert len(refund_env) == 1
    m = refund_env[0]
    assert m["transaction_type"] == "withdrawal" and m["amount"] == -40000 and isinstance(m["amount"], int)
    assert m["bank_account_id"] == BANK and m["tenant_id"] == T and m["reference_type"] == "CREDIT_NOTE_REFUND"
    jid = next(a[0] for s, a in k.exec if "INSERT INTO journal_entries" in s)
    assert m["journal_id"] == jid  # terkait jurnal refund yang sama
    # urutan: jurnal POSTED dulu, baru cermin, baru catatan refund
    urut = [("post" if "SET status = 'POSTED'" in s else "ref" if "INSERT INTO credit_note_refunds" in s else "")
            for s, a in k.exec]
    assert urut.index("post") < urut.index("ref")
    # reverse-lookup bank: tenant + CoA + aktif
    s, a = next((s, a) for s, a in k.sql if "FROM bank_accounts WHERE tenant_id" in s)
    assert a == (T, COA) and "is_active = true" in s


def test_refund_akun_non_bank_tanpa_cermin(refund_env):
    k = _Konn(bank_by_coa=None)
    _jalan(C.refund_nota_kredit_core(k, CTX, CN, _body(account_id=str(COA), payment_method="cash")))
    assert refund_env == [] and _kredit_jurnal(k) == COA


def test_refund_jumlah_pecahan_lewat_bank_ditolak_sebelum_menulis(refund_env):
    k = _Konn(bank_by_coa=BANK)
    with pytest.raises(HTTPException) as e:
        _jalan(C.refund_nota_kredit_core(k, CTX, CN, _body(account_id=str(COA), amount=Decimal("100.50"))))
    assert e.value.status_code == 422 and e.value.detail["code"] == "CN_REFUND_BUKAN_BULAT"
    assert not [s for s, a in k.exec if "INSERT" in s]  # nol tulisan


def test_refund_pemeriksaan_lama_tetap(refund_env):
    with pytest.raises(HTTPException) as e:
        _jalan(C.refund_nota_kredit_core(_Konn(cn=_cn(status="draft")), CTX, CN, _body(account_id=str(COA))))
    assert e.value.status_code == 400 and "status 'draft'" in e.value.detail
    with pytest.raises(HTTPException) as e2:
        _jalan(C.refund_nota_kredit_core(_Konn(), CTX, CN, _body(account_id=str(COA), amount=Decimal("100001"))))
    assert "exceeds remaining" in e2.value.detail
    with pytest.raises(HTTPException) as e3:
        _jalan(C.refund_nota_kredit_core(_Konn(periode="CLOSED"), CTX, CN, _body(account_id=str(COA))))
    assert "CLOSED" in e3.value.detail
    k = _Konn()
    with pytest.raises(HTTPException) as e4:
        _jalan(C.refund_nota_kredit_core(k, CTX, CN, _body()))
    assert e4.value.status_code == 422 and not [s for s, a in k.exec if "INSERT" in s]
    assert k.exec[0][0].startswith("SELECT pg_advisory_xact_lock") and k.exec[0][1] == (f"CREDIT_NOTE_REFUND:{CN}",)


# ---------------- (1) rute = idempotensi + inti ----------------

@pytest.mark.parametrize("rute,kode,inti", [(C.apply_credit_note, "CN_APPLY", "apply_nota_kredit_core"),
                                            (C.unapply_credit_note, "CN_UNAPPLY", "unapply_nota_kredit_core"),
                                            (C.refund_credit_note, "CN_REFUND", "refund_nota_kredit_core")])
def test_rute_membungkus_inti_dengan_idempotensi_aksi(rute, kode, inti):
    src = " ".join(inspect.getsource(rute).split())
    assert f'_ib.kunci_dari(request), "{kode}", credit_note_id' in src
    assert f'_ib.simpan(conn, ctx, _kp, _sd, "{kode}", await {inti}(' in src
    assert "if _lama is not None: return _lama" in src
    assert "response: _Response = None" in src
    assert "INSERT" not in src  # rute tak lagi menulis sendiri: SATU penulis = inti


def test_inti_memegang_kunci_dan_pratinjau_memanggil_inti_yang_sama():
    for inti, kunci in ((C.apply_nota_kredit_core, "CREDIT_NOTE_APPLY"), (C.unapply_nota_kredit_core, "CREDIT_NOTE_APPLY"),
                        (C.refund_nota_kredit_core, "CREDIT_NOTE_REFUND")):
        assert f'f"{kunci}:{{credit_note_id}}"' in " ".join(inspect.getsource(inti).split())
    pr = " ".join(inspect.getsource(C._pratinjau_nk_aksi).split())
    for inti in ("apply_nota_kredit_core(conn, ctx, cn_id, body)", "unapply_nota_kredit_core(conn, ctx, cn_id, body)",
                 "refund_nota_kredit_core(conn, ctx, cn_id, body)"):
        assert inti in pr
    assert "raise _BatalkanPratinjauNk" in pr and "async with conn.transaction():" in pr  # savepoint + rollback


def test_replay_idempotensi_tak_memanggil_inti(monkeypatch):
    """Kunci sama = balasan pertama TANPA menjalankan inti lagi (satu uang keluar)."""
    from app.services import idem_buat as IB
    dipanggil = []

    async def inti(*a, **k):
        dipanggil.append(1)
        return {"x": 1}

    async def mulai(conn, ctx, kunci, prefix, doc, isi, response=None):
        return "k", "s", {"success": True, "replay": True}
    monkeypatch.setattr(C, "refund_nota_kredit_core", inti)
    monkeypatch.setattr(IB, "mulai_aksi", mulai)

    class _P:
        def acquire(s):
            class A:
                async def __aenter__(a):
                    class K:
                        def transaction(s2):
                            class Tx:
                                async def __aenter__(t): return None
                                async def __aexit__(t, *e): return False
                            return Tx()
                    return K()

                async def __aexit__(a, *e):
                    return False
            return A()

    async def gp():
        return _P()
    monkeypatch.setattr(C, "get_pool", gp)

    class _Req:
        headers = {"x-idempotency-key": "k1"}

        class state:
            user = {"tenant_id": T, "user_id": str(USER)}
    out = _jalan(C.refund_credit_note(_Req(), CN, _body(account_id=str(COA))))
    assert out == {"success": True, "replay": True} and dipanggil == []


# ---------------- (4) penentu pratinjau: SEMUA blok ----------------

def test_penentu_apply_mengumpulkan_semua_blok(monkeypatch):
    async def faktur(conn, tid, inv, cust):
        raise HTTPException(status_code=404, detail="Faktur tidak ditemukan")
    monkeypatch.setattr(C, "faktur_tenant_untuk_pelanggan", faktur)
    k = _Konn(cn=_cn(status="draft", journal_id=None, original_invoice_id=uuid.uuid4(), amount_refunded=5))
    body = ApplyCreditNoteRequest(applications=[ApplyCreditNoteItem(invoice_id=str(uuid.uuid4()), amount=Decimal("1"))])
    kode = [b["code"] for b in _jalan(C._rencana_nk_aksi(k, CTX, CN, "apply", body))]
    assert kode == ["CN_SUDAH_TERKAIT", "CN_BELUM_TERBIT", "CN_SUDAH_DIKEMBALIKAN", "CN_APPLY_PENUH", "CN_FAKTUR"]


def test_penentu_unapply_tanpa_alasan_dan_belum_diterapkan():
    kode = [b["code"] for b in _jalan(C._rencana_nk_aksi(_Konn(), CTX, CN, "unapply", ""))]
    assert kode == ["CN_ALASAN_WAJIB", "CN_BELUM_DITERAPKAN"]


def test_penentu_refund_semua_blok_dan_akun_wajib():
    k = _Konn(cn=_cn(status="draft"))
    assert [b["code"] for b in _jalan(C._rencana_nk_aksi(k, CTX, CN, "refund", _body()))] == ["CN_TAK_BISA_REFUND"]
    k2 = _Konn(periode="CLOSED")
    kode = [b["code"] for b in _jalan(C._rencana_nk_aksi(k2, CTX, CN, "refund", _body(amount=Decimal("999999"))))]
    assert kode == ["CN_REFUND_MELEBIHI_SISA", "CN_REFUND_AKUN_WAJIB", "PERIOD_CLOSED"]


def test_penentu_nk_tak_ada_404():
    k = _Konn()

    async def kosong(sql, *a):
        return None
    k.fetchrow = kosong
    with pytest.raises(HTTPException) as e:
        _jalan(C._rencana_nk_aksi(k, CTX, CN, "refund", _body(account_id=str(COA))))
    assert e.value.status_code == 404


def test_rute_pratinjau_terdaftar_post_dan_skema_unapply_opsional():
    jalur = {(r.path, tuple(sorted(r.methods))) for r in C.router.routes if hasattr(r, "methods")}
    for p in ("apply", "unapply", "refund"):
        assert (f"/{{credit_note_id}}/{p}/preview", ("POST",)) in jalur
    assert C.PratinjauUnapplyNk().reason is None  # alasan kosong = blok, bukan 422 skema
    assert re.search(r"def preview_unapply_credit_note\(.*Optional\[PratinjauUnapplyNk\] = None", inspect.getsource(C))
