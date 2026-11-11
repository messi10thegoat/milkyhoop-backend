"""Biaya BE gelombang 1 (12 Nov 2026; RANCANGAN-fe-biaya.md B1/B3/B4/B5/B7): keadaan + status_text dari SERVER (B1), pratinjau batal tanpa alasan (B3, pola G0f),
lingkup angka summary (B4: teks, makna tak berubah), detail vendor objek + position_text + nama pelanggan + billed_to_customer_name di daftar + idempotensi opsional
post/void/PATCH (B5), pratinjau hapus draf dgn SATU penentu (B7). Paritas nyata (kaos+nyawit) = uji salinan."""
import asyncio
import datetime as dt
import inspect
import uuid
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from starlette.requests import Request

import app.routers.expenses as EX
from app.schemas import expenses as S
from app.services import biaya_daftar as BD
from app.services.galat_berkode import GalatBerkode

T = "kaos-biru-konveksi"
EID, VID, CID, JID = (uuid.uuid4() for _ in range(4))


def req():
    r = Request({"type": "http", "method": "POST", "path": "/", "headers": []})
    r.state.user = {"tenant_id": T, "user_id": "00000000-0000-0000-0000-000000000001"}
    return r


def jalan(aw):
    return asyncio.run(aw)


# ---------------- B1
@pytest.mark.parametrize("status,kel,teks", [("draft", "draf", "Draf"), ("posted", "dibayar", "Dibayar"), ("void", "dibatalkan", "Dibatalkan")])
def test_keadaan_dan_status_text(status, kel, teks):
    assert BD.keadaan(status) == kel and BD.status_text(status) == teks


def test_status_tak_dikenal_tak_ditebak():
    assert BD.keadaan("aneh") is None and BD.status_text("aneh") is None and BD.keadaan(None) is None
    # position_text TETAP teks ringkas terpisah dari status_text
    assert BD.position_text("posted", "BCA", False) == "Dibayar dari BCA · tanpa bukti" and BD.status_text("posted") == "Dibayar"


def test_skema_tak_membuang_medan_baru_diam_diam():
    item = S.ExpenseListItem.model_validate({"id": EID, "expense_number": "EXP-1", "expense_date": dt.date(2026, 11, 1), "subtotal": 1, "tax_amount": 0,
                                             "total_amount": 1, "is_itemized": False, "status": "posted", "is_billable": False, "created_at": dt.datetime(2026, 11, 1),
                                             "biaya_keadaan": "dibayar", "status_text": "Dibayar", "billed_to_customer_name": "PT X"}).model_dump()
    assert (item["biaya_keadaan"], item["status_text"], item["billed_to_customer_name"]) == ("dibayar", "Dibayar", "PT X")
    a = S.ExpenseDocAnchor.model_validate({"id": "1", "position_text": "Draf", "total": "1", "biaya_keadaan": "draf", "status_text": "Draf"}).model_dump()
    assert (a["biaya_keadaan"], a["status_text"]) == ("draf", "Draf")
    sm = S.ExpenseSummaryData.model_validate({"period": "month", "total_count": 0, "total_amount": 0, "total_tax": 0, "vendor_count": 0, "billable_count": 0,
                                              "billable_amount": 0, "top_accounts": [], "period_label": "Bulan ini", "cakupan": {"draf": "Semua"}}).model_dump()
    assert sm["period_label"] == "Bulan ini" and sm["cakupan"] == {"draf": "Semua"}


# ---------------- B4
def test_cakupan_dan_label_periode():
    assert [BD.period_label(p) for p in ("week", "month", "quarter", "year")] == ["7 hari terakhir", "Bulan ini", "Kuartal ini", "Tahun ini"]
    assert BD.cakupan("quarter") == {"total": "Kuartal ini", "draf": "Semua", "tanpa_bukti": "Semua", "bulan_ini": "Bulan ini"}
    src = inspect.getsource(EX.get_expenses_summary)
    assert '"period_label": BD.period_label(period)' in src and '"cakupan": BD.cakupan(period)' in src


# ---------------- B7
class Pool:
    def __init__(self, baris=None, ro=None):
        self.baris, self.ro, self.exe, self.fetchrow_args = baris, ro if ro is not None else [], [], []

    def acquire(self):
        pool = self

        class Ac:
            async def __aenter__(s):
                return pool

            async def __aexit__(s, *e):
                return False
        return Ac()

    def transaction(self, readonly=False):
        self.ro.append(readonly)
        pool = self

        class Tx:
            async def __aenter__(s):
                return None

            async def __aexit__(s, *e):
                return False
        return Tx()

    async def fetchrow(self, sql, *a):
        self.fetchrow_args.append((sql, a))
        return self.baris

    async def execute(self, sql, *a):
        self.exe.append(sql)


@pytest.fixture
def pasang(monkeypatch):
    kat = SimpleNamespace(pool=Pool())

    async def gp():
        return kat.pool
    monkeypatch.setattr(EX, "get_pool", gp)
    return kat


@pytest.mark.parametrize("baris,kode,status", [(None, "EXPENSE_TIDAK_DITEMUKAN", 404), ({"status": "posted", "expense_number": "EXP-1"}, "BIAYA_BUKAN_DRAF", 400),
                                                ({"status": "void", "expense_number": "EXP-1"}, "BIAYA_BUKAN_DRAF", 400)])
def test_hapus_preview_dan_delete_satu_penentu(pasang, baris, kode, status):
    pasang.pool.baris = baris
    d = jalan(EX.preview_delete_expense(req(), EID))["data"]
    assert (d["can_proceed"], d["action"], d["journals"], d["bank_transactions"], d["preview"]) == (False, "hapus", [], [], True)
    assert d["blocks"] == [{"code": kode, "message": BD.blok_hapus(baris)[2]}] and pasang.pool.ro == [True]          # nol tulis: transaksi READ ONLY
    assert "tenant_id = $2" in pasang.pool.fetchrow_args[0][0] and pasang.pool.fetchrow_args[0][1][1] == T
    with pytest.raises(GalatBerkode) as e:                                                                             # DELETE memakai penentu yang sama
        jalan(EX.delete_expense(req(), EID))
    assert (e.value.status_code, e.value.code) == (status, kode) and not any("DELETE FROM expenses" in s for s in pasang.pool.exe)


def test_hapus_preview_draf_boleh(pasang):
    pasang.pool.baris = {"status": "draft", "expense_number": "EXP-9"}
    d = jalan(EX.preview_delete_expense(req(), EID))["data"]
    assert d["can_proceed"] is True and d["blocks"] == [] and "EXP-9" in d["note"]
    r = jalan(EX.delete_expense(req(), EID))
    assert r["success"] is True and any("DELETE FROM expenses" in s for s in pasang.pool.exe)


# ---------------- B3
@pytest.fixture
def void_pratinjau(monkeypatch):
    kat = SimpleNamespace(alasan=None, hasil=None)

    async def prat(request, eid, nama, jalankan):
        await jalankan(None, {})
        return kat.hasil()

    async def batal(conn, ctx, eid, body):
        kat.alasan = body.reason
        return {}
    kat.hasil = lambda: {"success": True, "data": {"action": "batalkan", "can_proceed": True, "blocks": [], "journals": [{"source_type": "EXPENSE"}],
                                                   "bank_transactions": [], "preview": True}}
    monkeypatch.setattr(EX, "_pratinjau_biaya", prat)
    monkeypatch.setattr(EX, "_batal_biaya", batal)
    return kat


@pytest.mark.parametrize("body", [None, S.VoidExpensePreviewRequest(), S.VoidExpensePreviewRequest(reason="   ")])
def test_void_preview_tanpa_alasan_200_dengan_blok(void_pratinjau, body):
    d = jalan(EX.preview_void_expense(req(), EID, body))["data"]
    assert d["can_proceed"] is False and d["blocks"] == [{"code": "BIAYA_REASON_REQUIRED", "message": "Isi alasan pembatalan."}]
    assert d["journals"] == [{"source_type": "EXPENSE"}]                      # jurnal pembalik tetap terlihat (pola G0f: Tinjau dulu, alasan wajib)
    assert void_pratinjau.alasan == "(alasan belum diisi)"                    # penentu tulis tetap dijalankan dgn alasan pengganti


def test_void_preview_dengan_alasan_dan_blok_lain_tetap_muncul(void_pratinjau):
    d = jalan(EX.preview_void_expense(req(), EID, S.VoidExpensePreviewRequest(reason="salah catat")))["data"]
    assert d["can_proceed"] is True and d["blocks"] == [] and void_pratinjau.alasan == "salah catat"
    void_pratinjau.hasil = lambda: {"success": True, "data": {"action": "batalkan", "can_proceed": False, "preview": True,
                                                              "blocks": [{"code": "PERIODE_TERTUTUP", "message": "Periode ditutup."}], "journals": []}}
    d = jalan(EX.preview_void_expense(req(), EID, None))["data"]
    assert [b["code"] for b in d["blocks"]] == ["PERIODE_TERTUTUP", "BIAYA_REASON_REQUIRED"] and d["can_proceed"] is False


def test_void_tulis_tetap_wajib_alasan_dan_batas_panjang():
    with pytest.raises(ValidationError):
        S.VoidExpenseRequest(reason="")
    with pytest.raises(ValidationError):
        S.VoidExpenseRequest.model_validate({})
    with pytest.raises(ValidationError):
        S.VoidExpensePreviewRequest(reason="x" * 501)


# ---------------- B5d idempotensi opsional post/void/PATCH (penyambungan)
@pytest.fixture
def idem(monkeypatch):
    kat = SimpleNamespace(lama=None, mulai=[], simpan=[], tulis=[])

    async def mulai(conn, ctx, kunci, prefix, doc_id, isi, response=None):
        kat.mulai.append((prefix, doc_id, isi))
        return "KP", "SD", kat.lama

    async def simpan(conn, ctx, kp, sd, src, resp, rid):
        kat.simpan.append((src, rid, resp))
        return resp

    async def posting(conn, ctx, eid):
        kat.tulis.append("post")
        return {"success": True, "data": {"id": str(eid)}}

    async def batal(conn, ctx, eid, body):
        kat.tulis.append("void")
        return {"success": True, "data": {"id": str(eid)}}

    async def pre(pool, tid=None):
        return None
    monkeypatch.setattr(EX.idem_buat, "mulai_aksi", mulai)
    monkeypatch.setattr(EX.idem_buat, "simpan", simpan)
    monkeypatch.setattr(EX, "_posting_biaya", posting)
    monkeypatch.setattr(EX, "_batal_biaya", batal)
    monkeypatch.setattr(EX, "_ensure_role_preconditions", pre)
    return kat


def test_post_dan_void_idempoten_replay_tanpa_menulis(pasang, idem):
    r = jalan(EX.post_expense(req(), EID))
    assert idem.mulai[-1][:2] == ("EXPENSE_POST", EID) and idem.simpan[-1][0] == "EXPENSE_POST" and idem.tulis == ["post"] and r["success"]
    r = jalan(EX.void_expense(req(), EID, S.VoidExpenseRequest(reason="salah")))
    assert idem.mulai[-1][0] == "EXPENSE_VOID" and idem.mulai[-1][2] == {"reason": "salah"} and idem.tulis == ["post", "void"]
    idem.lama = {"success": True, "data": {"id": "lama"}}                             # kunci sama + isi sama: respons lama, NOL tulisan
    assert jalan(EX.post_expense(req(), EID)) == idem.lama and jalan(EX.void_expense(req(), EID, S.VoidExpenseRequest(reason="salah"))) == idem.lama
    assert idem.tulis == ["post", "void"]


def test_patch_idempoten_disambung_sebelum_tulis():
    src = inspect.getsource(EX.update_expense)
    assert '"EXPENSE_UPDATE", expense_id' in src and src.index("idem_buat.mulai_aksi(") < src.index("ubah_biaya_inti(")   # idempotensi SEBELUM penulis (gelombang 2: inti diekstrak)
    assert 'exclude_unset=True' in src and "idem_buat.simpan(conn, ctx, _kp, _sd, \"EXPENSE_UPDATE\"" in src
    for fn in (EX.post_expense, EX.void_expense, EX.update_expense):
        assert "response" in inspect.signature(fn).parameters           # penerima header X-Idempotent-Replay


# ---------------- B1/B5 detail + daftar
def test_detail_memuat_keadaan_posisi_vendor_objek_dan_nama_pelanggan(monkeypatch):
    exp = {"id": EID, "status": "posted", "is_itemized": False, "vendor_id": VID, "vendor_name": "PLN", "billed_to_customer_id": CID,
           "paid_through_name": "BCA", "journal_id": JID, "expense_number": "EXP-1"}

    class K(Pool):
        async def fetchrow(s, sql, *a):
            return exp

        async def fetch(s, sql, *a):
            return [{"id": uuid.uuid4(), "file_name": "nota.jpg"}] if "expense_attachments" in sql else []

        async def fetchval(s, sql, *a):
            assert "FROM customers WHERE id = $1 AND tenant_id = $2" in sql and a == (CID, T)    # tersaring tenant
            return "PT Pelanggan"

    async def gp():
        return K()

    async def tersedia(conn, tid, e):
        return {}
    import app.services.biaya_aksi as BA
    monkeypatch.setattr(EX, "get_pool", gp)
    monkeypatch.setattr(BA, "aksi_tersedia_biaya", tersedia)
    d = jalan(EX.get_expense_detail(req(), EID))["data"]
    assert (d["biaya_keadaan"], d["status_text"], d["position_text"]) == ("dibayar", "Dibayar", "Dibayar dari BCA")         # ada bukti: tanpa "· tanpa bukti"
    assert d["vendor"] == {"id": str(VID), "name": "PLN"} and d["vendor_id"] == VID and d["billed_to_customer_name"] == "PT Pelanggan"


def test_daftar_isi_nama_pelanggan_dan_keadaan_tersaring_tenant(monkeypatch):
    baris = {"id": EID, "expense_number": "EXP-1", "expense_date": dt.date(2026, 11, 1), "paid_through_id": None, "paid_through_name": "BCA", "vendor_id": None,
             "vendor_name": None, "account_id": None, "account_name": "Listrik", "subtotal": 1, "tax_amount": 0, "total_amount": 1, "is_itemized": False,
             "status": "draft", "is_billable": True, "billed_invoice_id": None, "billed_to_customer_id": CID, "accounting_status": None, "reference": None,
             "notes": None, "has_receipt": False, "created_at": dt.datetime(2026, 11, 1), "journal_id": None, "jumlah_bukti": 0, "billed_to_customer_name": "PT Pelanggan"}
    sql_dilihat = []

    class K(Pool):
        async def fetch(s, sql, *a):
            sql_dilihat.append(sql)
            return [baris]

        async def fetchval(s, sql, *a):
            return 1

    async def gp():
        return K()
    monkeypatch.setattr(EX, "get_pool", gp)
    r = jalan(EX.list_expenses(req(), skip=0, limit=20, status="all", vendor_id=None, account_id=None, date_from=None, date_to=None, search=None,
                               sort_by="expense_date", sort_order="desc", is_billable=None, billing_status=None, aksi=None, with_totals=False))
    it = r["items"][0]
    assert (it["biaya_keadaan"], it["status_text"], it["billed_to_customer_name"], it["position_text"]) == ("draf", "Draf", "PT Pelanggan", "Draf")
    assert "cu.tenant_id = expenses.tenant_id" in sql_dilihat[0]


def test_rute_delete_preview_terdaftar_dengan_izin_dan_inventaris():
    from pathlib import Path
    from app.middleware import permission_middleware as PM
    jalur = {getattr(x, "path", "") for x in EX.router.routes}
    assert "/{expense_id}/delete/preview" in jalur
    inv = Path(__file__).with_name("inventaris_rute_tulis.txt").read_text()
    assert "POST /api/expenses/{expense_id}/delete/preview" in inv
    assert 'r"^/api/expenses/[^/]+/delete/preview$"' in Path(PM.__file__).read_text()
