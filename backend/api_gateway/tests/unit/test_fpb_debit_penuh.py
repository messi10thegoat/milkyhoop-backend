"""FPB-DEBIT-PENUH + RECLASS-COGS-CLASS (30 Okt 2026; aturan + koreksi riwayat disetujui LANGSUNG pemilik).

(1) Jalur buat-langsung-posted (create_bill_v2) memakai penentu SATU tagihan_aksi.hitung_debit (== jalur draf->posting) untuk SEMUA baris:
    non-stok & tanpa produk -> COGS_SALES, jasa -> COGS_SERVICE, purchase_account_id produk dihormati, barang stok -> Persediaan.
(2) POST /api/journals/reclassify-bill-cogs-class: reklas kelas beban (COGS_SALES <-> COGS_SERVICE) dgn calon DITURUNKAN server; dry_run =
    pratinjau nol tulis; idempoten (penanda + 409); periode wajib terbuka; ADJUSTMENT (bisa dibalik). OWNER + izin journal C.
Bukti DB nyata (kedua jalur identik per jenis baris; reklas -> GL -> 409 -> reverse; kontrol merah di pohon dasar):
scripts/ops/uji-nyata-fpb-debit-penuh.py."""
import asyncio
import inspect
import textwrap
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.middleware import permission_middleware as pm
from app.routers import journals as RJ
from app.services import bills_service as BS
from app.services import reklas_kelas_beban as RKB
from app.services import tagihan_aksi as TA

S, J, INV, OV = uuid4(), uuid4(), uuid4(), uuid4()


# ------------------------------------------------------------------ (1) penentu satu (karakterisasi per jenis: test_fpb_debit_karakterisasi.py)
def _tubuh_create():
    return textwrap.dedent(inspect.getsource(BS.BillsService.create_bill_v2))


def test_create_posted_memakai_hitung_debit_bukan_salinan():
    src = _tubuh_create()
    assert "await TA.hitung_debit(conn, tenant_id, bill_id, subtotal_dec, debit_acct_id)" in src
    assert "TA.memo_debit(acct_id, is_inv, vendor_name, None)" in src
    for salinan in ("_accumulate(", "acct_totals", "AccountRole.COGS_SERVICE", "debit_rows"):
        assert salinan not in src, salinan


# ------------------------------------------------------------------ (2) reklas kelas beban
def test_rute_reklas_izin_journal_c_dan_owner_di_handler():
    assert pm.izin_untuk_jalur("/api/journals/reclassify-bill-cogs-class", "POST") == ("journal", "C")
    src = inspect.getsource(RJ.reclassify_bill_cogs_class_endpoint)
    assert 'business_role_code", None) != "OWNER"' in src and "RKB.jalankan(" in src
    assert RJ.ReclassifyBillCogsClassRequest().dry_run is True   # bawaan pratinjau


def test_sql_posisi_efektif_tenant_dan_tiga_sumber():
    q = " ".join(RKB.SQL_POSISI.split())
    assert "je.tenant_id = $1" in q and "je.status = 'POSTED' AND je.reversed_by_id IS NULL AND je.reversal_of_id IS NULL" in q
    assert "je.id = $4" in q
    assert "je.source_type = 'RECLASSIFY_BILL_INVENTORY' AND starts_with(jl.memo, 'Reklas ' || $3 || ' ')" in q
    assert "starts_with(je.description, 'RECLASS-COGS-CLASS')" in q and "strpos(jl.memo, '(' || $2::text || ')') > 0" in q
    f = " ".join(RKB.SQL_FPB.split())
    assert "b.tenant_id = $1" in f and "j.reversed_by_id IS NULL" in f and "is_opening_balance" in f


def test_baris_jurnal_dr_benar_cr_salah_memo_penanda_dan_id():
    bid = str(uuid4())
    m = [{"bill_id": bid, "invoice_number": "PB-1", "dari": J, "ke": S, "dari_kode": "5-10110", "ke_kode": "5-10100", "amount": Decimal("5")}]
    (a1, d1, k1, m1), (a2, d2, k2, m2) = RKB.baris_jurnal(m)
    assert (a1, d1, k1) == (S, Decimal("5"), Decimal("0")) and (a2, d2, k2) == (J, Decimal("0"), Decimal("5"))
    assert m1 == m2 == f"RECLASS-COGS-CLASS PB-1 ({bid}) 5-10110->5-10100"


class _KR:
    """Konek boneka untuk RKB.jalankan: rencana dipalsukan lewat monkeypatch; catat tulis."""
    def __init__(s, periode="OPEN", penanda=0):
        s.periode, s.penanda, s.tulis = periode, penanda, []

    def transaction(s):
        class T:
            async def __aenter__(t): return None
            async def __aexit__(t, *e): return False
        return T()

    async def execute(s, sql, *a):
        if not sql.lstrip().upper().startswith("SELECT"):
            s.tulis.append(sql)

    async def fetchval(s, sql, *a):
        if "INSERT INTO journal_entries" in sql:
            s.tulis.append(sql)
            return uuid4()
        if "get_next_journal_number" in sql:
            return "JV-2610-9999"
        if "count(*)" in sql:
            return s.penanda
        return None

    async def fetchrow(s, sql, *a):
        return {"id": uuid4(), "status": s.periode} if "fiscal_periods" in sql else None


def _rencana(moves):
    async def r(conn, tid):
        return {"moves": moves, "lewati": [], "akun": {}}
    return r


MOVE = [{"bill_id": str(uuid4()), "invoice_number": "PB-1", "dari": J, "ke": S, "dari_kode": "5-10110", "ke_kode": "5-10100",
         "amount": Decimal("5")}]


def test_dry_run_nol_tulis_baris_persis(monkeypatch):
    monkeypatch.setattr(RKB, "rencana", _rencana(MOVE))
    k = _KR()
    out = asyncio.run(RKB.jalankan(k, "t", uuid4(), dry_run=True, tanggal="2026-10-30"))
    assert out["status"] == "dry_run" and out["total"] == 5.0 and len(out["lines"]) == 2 and k.tulis == []


def test_tulis_periode_tertutup_ditolak(monkeypatch):
    monkeypatch.setattr(RKB, "rencana", _rencana(MOVE))
    with pytest.raises(HTTPException) as e:
        asyncio.run(RKB.jalankan(_KR(periode="CLOSED"), "t", uuid4(), dry_run=False, tanggal="2026-10-30"))
    assert e.value.status_code == 400 and e.value.detail["code"] == "PERIODE_TERTUTUP"


def test_tulis_tanpa_calon_dan_sudah_direklas_409(monkeypatch):
    monkeypatch.setattr(RKB, "rencana", _rencana([]))
    with pytest.raises(HTTPException) as e:
        asyncio.run(RKB.jalankan(_KR(penanda=1), "t", uuid4(), dry_run=False, tanggal="2026-10-30"))
    assert e.value.status_code == 409 and e.value.detail["code"] == "SUDAH_DIREKLAS"
    assert asyncio.run(RKB.jalankan(_KR(penanda=0), "t", uuid4(), dry_run=False, tanggal="2026-10-30"))["status"] == "nothing_to_reclassify"


def test_tulis_draft_lalu_posted_adjustment_berpenanda(monkeypatch):
    monkeypatch.setattr(RKB, "rencana", _rencana(MOVE))
    k = _KR()
    out = asyncio.run(RKB.jalankan(k, "t", uuid4(), dry_run=False, tanggal="2026-10-30"))
    assert out["status"] == "posted" and out["journal_number"] == "JV-2610-9999"
    src = inspect.getsource(RKB.jalankan)
    assert "'DRAFT'" in src and "SET status = 'POSTED'" in src and 'SUMBER = "ADJUSTMENT"' in inspect.getsource(RKB)
    assert src.index("pg_advisory_xact_lock") < src.index("await rencana(conn, tid)")


def test_calon_hanya_bila_total_kelas_sama():
    src = inspect.getsource(RKB.rencana)
    assert "if tgt[sales] + tgt[servis] != pos[sales] + pos[servis]:" in src and "lewati.append" in src
    assert "if tgt[sales] == pos[sales] and tgt[servis] == pos[servis]:" in src
    assert "await TA.hitung_debit(conn, tid, b[\"id\"]" in src


class _KB(_KR):
    """Konek boneka untuk RKB.balik: satu jurnal dgn isi yang diatur tes."""
    def __init__(s, jurnal, periode="OPEN"):
        super().__init__(periode=periode)
        s.jurnal = jurnal

    async def fetchrow(s, sql, *a):
        if "FROM journal_entries" in sql:
            return s.jurnal
        return await super().fetchrow(sql, *a)

    async def fetch(s, sql, *a):
        return [{"account_id": S, "memo": "m", "debit": Decimal("5"), "credit": Decimal("0")},
                {"account_id": J, "memo": "m", "debit": Decimal("0"), "credit": Decimal("5")}]


def _jurnal(**ubah):
    j = {"id": uuid4(), "journal_number": "JV-1", "status": "POSTED", "reversed_by_id": None, "reversal_of_id": None,
         "source_type": "ADJUSTMENT", "description": "RECLASS-COGS-CLASS: x"}
    j.update(ubah)
    return j


@pytest.mark.parametrize("ubah,kode,status", [
    ({"description": "Jurnal manual lain"}, "JURNAL_TAK_ADA", 404),          # bukan jurnal berpenanda -> tak boleh dibalik lewat sini
    ({"source_type": "MANUAL"}, "JURNAL_TAK_ADA", 404),
    ({"reversal_of_id": uuid4()}, "JURNAL_TAK_ADA", 404),                     # jurnal pembalik tak dibalik lagi
    ({"reversed_by_id": uuid4()}, "SUDAH_DIBALIK", 409),
])
def test_balik_hanya_jurnal_berpenanda_belum_dibalik(ubah, kode, status):
    with pytest.raises(HTTPException) as e:
        asyncio.run(RKB.balik(_KB(_jurnal(**ubah)), "t", uuid4(), uuid4(), "uji", tanggal="2026-10-30"))
    assert e.value.status_code == status and e.value.detail["code"] == kode


def test_balik_periode_tertutup_ditolak_dan_jalur_sah():
    with pytest.raises(HTTPException) as e:
        asyncio.run(RKB.balik(_KB(_jurnal(), periode="LOCKED"), "t", uuid4(), uuid4(), "uji", tanggal="2026-10-30"))
    assert e.value.detail["code"] == "PERIODE_TERTUTUP"
    k = _KB(_jurnal())
    out = asyncio.run(RKB.balik(k, "t", uuid4(), uuid4(), "uji", tanggal="2026-10-30"))
    assert out["status"] == "reversed" and out["total"] == 5.0
    assert any("reversed_by_id" in w for w in k.tulis)
    src = inspect.getsource(RKB.balik)
    assert "reversal_of_id" in src and "b[\"credit\"], b[\"debit\"]" in src and "FOR UPDATE" in src


def test_rute_meneruskan_reverse_journal_id_ke_balik():
    src = inspect.getsource(RJ.reclassify_bill_cogs_class_endpoint)
    assert "if body.reverse_journal_id:" in src and "RKB.balik(" in src
