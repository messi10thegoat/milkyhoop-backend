"""DP-PRATINJAU (3 Nov 2026): POST /customer-deposits/preview = penentu BERSAMA rencana_buat_dp dengan POST /customer-deposits.

Spek:
  * penulis (pratinjau=False) me-raise galat PERTAMA apa adanya -- kode HTTP + kalimat == blok pertama pratinjau, per kasus;
  * pratinjau mengumpulkan SEMUA blok, urutan penulis; tanpa FOR UPDATE; nol tulis;
  * auto_post: periode tutup / peran hilang ditolak SEBELUM INSERT (urutan baru penulis, respons sama);
  * nomor uang muka = PERKIRAAN dari penghitung (dibaca, tak dinaikkan) -- pelanggan DEP-YYMM-NNNN, vendor VD-YYYY-NNNNN;
  * kode order = kode_order.perkiraan_spk, syarat == terbitkan_spk, baca saja.
Koneksi tiruan menjawab per pola SQL dan MENCATAT tiap SQL (untuk memeriksa FOR UPDATE / INSERT)."""
import ast
import uuid
from datetime import date
from pathlib import Path

import pytest
from fastapi import HTTPException

import app.routers.customer_deposits as CD
import app.routers.vendor_deposits as VD
from app.schemas.customer_deposits import CreateCustomerDepositRequest
from app.services import kode_order as KO
from app.services.role_resolver import AccountRoleUnmappedError

T = "kaos-biru-konveksi"
AKUN, AKUN_LIAB, AKUN_BEBAN = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
SO, CUST, PF = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


class Kon:
    def __init__(self, so_status="confirmed", so_total=1_000_000, sudah=0, periode="OPEN", pf_status="issued",
                 pf_so=SO, pf_cust=CUST, pelanggan=True):
        self.sql = []
        self.akun = {AKUN: {"id": AKUN, "account_code": "1-10100", "account_type": "ASSET", "name": "Kas"},
                     AKUN_LIAB: {"id": AKUN_LIAB, "account_code": "2-10400", "account_type": "LIABILITY", "name": "Uang Muka Pelanggan"},
                     AKUN_BEBAN: {"id": AKUN_BEBAN, "account_code": "6-10000", "account_type": "EXPENSE", "name": "Beban"}}
        self.so = {"order_number": "SO-2611-0001", "total_amount": so_total, "status": so_status, "dp_amount": 300_000}
        self.sudah, self.periode, self.pelanggan = sudah, periode, pelanggan
        self.pf = {"id": PF, "proforma_number": "PRO-2611-0001", "amount": 500_000, "sales_order_id": pf_so, "customer_id": pf_cust,
                   "status": pf_status, "purpose": "DP"}
        self.rekening = {"account_name": "BCA Operasional"}

    def _catat(self, sql, a):
        self.sql.append(" ".join(sql.split()))
        return self.sql[-1]

    async def fetchrow(self, sql, *a):
        q = self._catat(sql, a)
        if "FROM chart_of_accounts" in q:
            return self.akun.get(a[0])
        if "FROM sales_orders" in q:
            return self.so if a[0] == SO else None
        if "FROM proformas" in q:
            return self.pf if a[0] == PF else None
        if "FROM fiscal_periods" in q:
            return {"status": self.periode}
        if "tanggal_bisnis" in q:
            return {"ym": "2026-11", "yymm": "2611"}
        if "FROM bank_accounts" in q:
            return self.rekening
        return None

    async def fetchval(self, sql, *a):
        q = self._catat(sql, a)
        if "FROM customers" in q:
            return 1 if self.pelanggan else None
        if "FROM proformas" in q and "purpose" in q:
            return self.pf["purpose"]
        if "FROM customer_deposit_sequences" in q:
            return 8
        return None

    async def fetch(self, sql, *a):
        q = self._catat(sql, a)
        if "FROM chart_of_accounts" in q and "ANY" in q:
            return [self.akun[i] for i in a[1] if i in self.akun]
        return []

    async def execute(self, sql, *a):
        self._catat(sql, a)


@pytest.fixture(autouse=True)
def _palsu(monkeypatch):
    async def diterima(conn, tid, so_id, exclude_id=None):
        return float(conn.sudah)

    async def peran(conn, tid, role):
        if getattr(conn, "peran_hilang", False):
            raise AccountRoleUnmappedError(role)
        return AKUN_LIAB
    monkeypatch.setattr(CD, "received_total_for_order", diterima)
    monkeypatch.setattr(CD, "resolve_account_id_by_role", peran)


def badan(**ubah):
    b = {"customer_id": str(CUST), "customer_name": "Toko Melati", "amount": 200_000, "deposit_date": date(2026, 11, 3),
         "payment_method": "transfer", "account_id": str(AKUN), "sales_order_id": str(SO), "auto_post": True}
    b.update(ubah)
    return CreateCustomerDepositRequest(**{k: v for k, v in b.items() if v is not None})


CTX = {"tenant_id": T, "user_id": str(uuid.uuid4())}

KASUS = {
    "dp_tanpa_so": (dict(sales_order_id=None, quote_id=str(uuid.uuid4())), {}, "DP_WAJIB_LEWAT_SO", 422),
    "akun_tak_ada": (dict(account_id=str(uuid.uuid4())), {}, "AKUN_TIDAK_DITEMUKAN", 400),
    "akun_bukan_aset": (dict(account_id=str(AKUN_BEBAN)), {}, "AKUN_BUKAN_KAS_BANK", 400),
    "so_tak_ada": (dict(sales_order_id=str(uuid.uuid4())), {}, "SO_TIDAK_DITEMUKAN", 400),
    "so_draf": ({}, dict(so_status="draft"), "SO_NOT_ACCEPTING_DEPOSIT", 400),
    "so_batal": ({}, dict(so_status="cancelled"), "SO_NOT_ACCEPTING_DEPOSIT", 400),
    "so_tutup": ({}, dict(so_status="completed"), "SO_NOT_ACCEPTING_DEPOSIT", 400),
    "plafon": (dict(amount=900_000), dict(sudah=200_000), "PLAFON_SO_TERLAMPAUI", 400),
    "pelanggan_tak_ada": ({}, dict(pelanggan=False), "PELANGGAN_TIDAK_DITEMUKAN", 400),
    "pelanggan_bukan_uuid": (dict(customer_id="Toko Melati"), {}, "PELANGGAN_TIDAK_VALID", 400),
    "proforma_so_lain": (dict(proforma_id=str(PF)), dict(pf_so=uuid.uuid4()), "PROFORMA_BEDA_PESANAN", 422),
    "proforma_draf": (dict(proforma_id=str(PF)), dict(pf_status="draft"), "PROFORMA_BUKAN_TERBIT", 422),
    "proforma_pelanggan_lain": (dict(proforma_id=str(PF)), dict(pf_cust=uuid.uuid4()), "PROFORMA_BEDA_PELANGGAN", 422),
    "proforma_tak_ada": (dict(proforma_id=str(uuid.uuid4())), {}, "PROFORMA_TIDAK_VALID", 422),
    "periode_tutup": ({}, dict(periode="CLOSED"), "PERIOD_CLOSED", 400),
}


def _pesan(detail):
    return detail if isinstance(detail, str) else detail.get("message")


@pytest.mark.asyncio
@pytest.mark.parametrize("nama", sorted(KASUS))
async def test_pratinjau_blok_pertama_sama_persis_dengan_galat_penulis(nama):
    ubah_badan, ubah_kon, kode, status = KASUS[nama]
    with pytest.raises(HTTPException) as e:
        await CD.rencana_buat_dp(Kon(**ubah_kon), CTX, badan(**ubah_badan))
    r = await CD.rencana_buat_dp(Kon(**ubah_kon), CTX, badan(**ubah_badan), pratinjau=True)
    assert r["blocks"], nama
    b = r["blocks"][0]
    assert (b["code"], b["status"], b["message"]) == (kode, e.value.status_code, _pesan(e.value.detail))
    assert e.value.status_code == status


@pytest.mark.asyncio
async def test_peran_hilang_penulis_dan_pratinjau_status_dan_kalimat_sama_dengan_handler_global():
    """MASTER 3 Nov (opsi A): 422 ACCOUNT_DEFAULT_UNMAPPED berkalimat handler main.py di KEDUA jalur (dulu penulis 500 umum)."""
    from app import main as M
    import inspect
    k = Kon()
    k.peran_hilang = True
    with pytest.raises(HTTPException) as e:
        await CD.rencana_buat_dp(k, CTX, badan())
    assert e.value.status_code == 422
    assert e.value.detail == {"error_code": "ACCOUNT_DEFAULT_UNMAPPED", "message": CD.AKUN_DEFAULT_BELUM_DIATUR}
    assert CD.AKUN_DEFAULT_BELUM_DIATUR.split(" Atur")[0] in inspect.getsource(M._account_role_unmapped_handler)
    k2 = Kon()
    k2.peran_hilang = True
    r = await CD.rencana_buat_dp(k2, CTX, badan(), pratinjau=True)
    assert [(b["code"], b["status"], b["message"]) for b in r["blocks"]] == [
        ("ACCOUNT_DEFAULT_UNMAPPED", 422, CD.AKUN_DEFAULT_BELUM_DIATUR)]


@pytest.mark.asyncio
@pytest.mark.parametrize("cust,kode_pel", [("Toko Melati", "PELANGGAN_TIDAK_VALID"), (str(uuid.uuid4()), "PELANGGAN_TIDAK_DITEMUKAN")])
async def test_pelanggan_tak_sah_berproforma_blok_kalimat_penulis_tanpa_blok_proforma_palsu(cust, kode_pel):
    kon = dict(pelanggan=kode_pel != "PELANGGAN_TIDAK_DITEMUKAN")
    with pytest.raises(HTTPException) as e:
        await CD.rencana_buat_dp(Kon(**kon), CTX, badan(customer_id=cust, proforma_id=str(PF)))
    r = await CD.rencana_buat_dp(Kon(**kon), CTX, badan(customer_id=cust, proforma_id=str(PF)), pratinjau=True)
    assert [(b["code"], b["status"], b["message"]) for b in r["blocks"]] == [(kode_pel, e.value.status_code, e.value.detail)]
    # proforma yang memang bermasalah TETAP diblok walau pelanggan tak sah
    r2 = await CD.rencana_buat_dp(Kon(pf_status="draft", **kon), CTX, badan(customer_id=cust, proforma_id=str(PF)), pratinjau=True)
    assert [b["code"] for b in r2["blocks"]] == [kode_pel, "PROFORMA_BUKAN_TERBIT"]


@pytest.mark.asyncio
async def test_draf_tanpa_auto_post_tak_memeriksa_periode_dan_peran():
    k = Kon(periode="CLOSED")
    k.peran_hilang = True
    r = await CD.rencana_buat_dp(k, CTX, badan(auto_post=False), pratinjau=True)
    assert r["blocks"] == [] and r["akun_dp"] is None
    assert not any("fiscal_periods" in q for q in k.sql)


@pytest.mark.asyncio
async def test_pratinjau_mengumpulkan_semua_blok_urutan_penulis():
    r = await CD.rencana_buat_dp(Kon(so_status="draft", pelanggan=False, periode="CLOSED"), CTX,
                                 badan(account_id=str(AKUN_BEBAN)), pratinjau=True)
    assert [b["code"] for b in r["blocks"]] == ["AKUN_BUKAN_KAS_BANK", "SO_NOT_ACCEPTING_DEPOSIT",
                                               "PELANGGAN_TIDAK_DITEMUKAN", "PERIOD_CLOSED"]


@pytest.mark.asyncio
async def test_penulis_mengunci_baris_so_pratinjau_tidak():
    k = Kon()
    await CD.rencana_buat_dp(k, CTX, badan())
    assert any("FROM sales_orders" in q and "FOR UPDATE" in q for q in k.sql)
    k2 = Kon()
    await CD.rencana_buat_dp(k2, CTX, badan(), pratinjau=True)
    assert any("FROM sales_orders" in q for q in k2.sql)
    assert not any("FOR UPDATE" in q or "pg_advisory" in q or q.startswith(("INSERT", "UPDATE")) for q in k2.sql)


@pytest.mark.asyncio
async def test_hasil_penentu_untuk_penulis():
    r = await CD.rencana_buat_dp(Kon(), CTX, badan(proforma_id=str(PF)))
    assert r == {"blocks": [], "pelanggan_dp": str(CUST), "order_id": SO, "jenis": "DP", "akun_dp": AKUN_LIAB}
    r2 = await CD.rencana_buat_dp(Kon(), CTX, badan(sales_order_id=None, proforma_id=str(PF), jenis="PELUNASAN",
                                                    auto_post=False))
    assert r2["order_id"] == SO and r2["jenis"] == "PELUNASAN" and r2["akun_dp"] is None


@pytest.mark.asyncio
async def test_perkiraan_nomor_dp_dibaca_tanpa_tulis():
    k = Kon()
    assert await CD.perkiraan_nomor_dp(k, T) == "DEP-2611-0009"
    assert not any(q.startswith(("INSERT", "UPDATE")) or "generate_customer_deposit_number" in q for q in k.sql)


class KonVD:
    def __init__(self, last):
        self.last, self.sql = last, []

    async def fetchval(self, sql, *a):
        self.sql.append(" ".join(sql.split()))
        if "tanggal_bisnis" in sql:
            return "2026"
        return self.last


@pytest.mark.asyncio
async def test_perkiraan_nomor_uang_muka_vendor_rumus_fungsi_db():
    assert await VD.perkiraan_nomor_umv(KonVD(41), T) == "VD-2026-00042"
    assert await VD.perkiraan_nomor_umv(KonVD(None), T) == "VD-2026-00001"


class KonKO:
    def __init__(self, enabled=True, so=None, last=4, terpakai=()):
        self.s = dict(KO.BAWAAN, enabled=enabled, template="SPK-{YY}{MM}-{SEQ}", min_digits=3, reset="monthly")
        self.so, self.last, self.terpakai, self.sql = so, last, set(terpakai), []

    async def fetchrow(self, sql, *a):
        self.sql.append(" ".join(sql.split()))
        if "order_code_settings" in sql:
            return self.s
        if "FROM sales_orders" in sql:
            return self.so
        return None

    async def fetchval(self, sql, *a):
        self.sql.append(" ".join(sql.split()))
        if "order_code_counters" in sql:
            return self.last
        if "order_code = $2" in sql:
            return 1 if a[1] in self.terpakai else None
        return None


@pytest.mark.asyncio
async def test_perkiraan_spk_syarat_sama_dengan_terbitkan_spk():
    tgl = date(2026, 11, 3)
    so = {"id": SO, "status": "confirmed", "order_code": None}
    assert await KO.perkiraan_spk(KonKO(enabled=False, so=so), T, SO, tgl) == {"order_code": None, "issued": False}
    assert await KO.perkiraan_spk(KonKO(so=None), T, SO, tgl) == {"order_code": None, "issued": False}
    assert await KO.perkiraan_spk(KonKO(so=dict(so, order_code="SPK-LAMA")), T, SO, tgl) == {"order_code": "SPK-LAMA", "issued": False}
    assert await KO.perkiraan_spk(KonKO(so=dict(so, status="draft")), T, SO, tgl) == {"order_code": None, "issued": False}
    berikut = KO.render("SPK-{YY}{MM}-{SEQ}", 5, 3, tgl)
    k = KonKO(so=so)
    assert await KO.perkiraan_spk(k, T, SO, tgl) == {"order_code": berikut, "issued": True}
    lompat = KO.render("SPK-{YY}{MM}-{SEQ}", 6, 3, tgl)
    assert await KO.perkiraan_spk(KonKO(so=so, terpakai={berikut}), T, SO, tgl) == {"order_code": lompat, "issued": True}
    assert not any(q.startswith(("INSERT", "UPDATE")) or "pg_advisory" in q for q in k.sql)


def _fungsi(mod, nama):
    pohon = ast.parse(Path(mod.__file__).read_text())
    return next(n for n in ast.walk(pohon) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == nama)


def _teks_kode(fn) -> str:
    """Konstanta string di badan fungsi, KECUALI docstring (pemindai AST, bukan teks mentah)."""
    badan = fn.body[1:] if fn.body and isinstance(fn.body[0], ast.Expr) and isinstance(getattr(fn.body[0], "value", None), ast.Constant) else fn.body
    return " ".join(n.value for b in badan for n in ast.walk(b) if isinstance(n, ast.Constant) and isinstance(n.value, str))


@pytest.mark.parametrize("mod,nama", [(CD, "preview_create_customer_deposit"), (CD, "_isi_pratinjau_buat_dp"),
                                      (CD, "perkiraan_nomor_dp"), (VD, "perkiraan_nomor_umv"),
                                      (KO, "perkiraan_spk"), (KO, "_intip_berikutnya")])
def test_jalur_pratinjau_tanpa_tulis_tanpa_kunci(mod, nama):
    t = _teks_kode(_fungsi(mod, nama)).upper()
    for kata in ("INSERT ", "UPDATE ", "DELETE ", "FOR UPDATE", "PG_ADVISORY", "GENERATE_", "NEXTVAL"):
        assert kata not in t, (nama, kata)


def test_rute_pratinjau_transaksi_read_only_dan_penentu_bersama():
    fn = _fungsi(CD, "preview_create_customer_deposit")
    kw = [k for n in ast.walk(fn) if isinstance(n, ast.Call) for k in n.keywords]
    assert any(k.arg == "readonly" and getattr(k.value, "value", None) is True for k in kw)
    assert any(k.arg == "pratinjau" and getattr(k.value, "value", None) is True for k in kw)
    panggil = {n.func.id if isinstance(n.func, ast.Name) else getattr(n.func, "attr", None) for n in ast.walk(fn) if isinstance(n, ast.Call)}
    assert "rencana_buat_dp" in panggil


def test_penulis_memakai_penentu_bersama_bukan_cek_sendiri():
    fn = _fungsi(CD, "create_customer_deposit")
    panggil = {n.func.id if isinstance(n.func, ast.Name) else getattr(n.func, "attr", None) for n in ast.walk(fn) if isinstance(n, ast.Call)}
    assert "rencana_buat_dp" in panggil
    for lama in ("validasi_proforma_dp", "assert_deposit_within_order_total", "pelanggan_kanonik_tenant", "resolve_order_id_for_deposit"):
        assert lama not in panggil, lama


def test_penulis_memanggil_penentu_sebelum_insert():
    src = ast.get_source_segment(Path(CD.__file__).read_text(), _fungsi(CD, "create_customer_deposit"))
    assert src.index("rencana_buat_dp(") < src.index("INSERT INTO customer_deposits")


@pytest.fixture
def _isi_palsu(monkeypatch):
    from decimal import Decimal
    from app.services import proforma_terbayar as PT

    async def terbayar(conn, tid, so_ids):
        assert so_ids == [SO]
        return {PF: {"paid": Decimal("100000")}}

    async def spk(conn, tid, so_id, tgl):
        assert so_id == SO and tgl == date(2026, 11, 3)
        return {"order_code": "SPK-2611-005", "issued": True}

    async def tgl(conn, tid):
        return date(2026, 11, 3)
    monkeypatch.setattr(PT, "terbayar_proforma", terbayar)
    monkeypatch.setattr(KO, "perkiraan_spk", spk)
    monkeypatch.setattr(CD, "tanggal_dokumen", tgl)


async def _isi(kon, **ubah):
    b = badan(**ubah)
    r = await CD.rencana_buat_dp(kon, CTX, b, pratinjau=True)
    return await CD._isi_pratinjau_buat_dp(kon, CTX, b, r)


@pytest.mark.asyncio
async def test_isi_pratinjau_posting_jurnal_bank_pesanan_proforma_kode(_isi_palsu):
    k = Kon(sudah=50_000)
    d = await _isi(k, proforma_id=str(PF), terbitkan_kode_order=True)
    assert d["can_proceed"] is True and d["blocks"] == []
    assert (d["deposit_number_preview"], d["deposit_number_estimated"]) == ("DEP-2611-0009", True)
    assert d["before"] == {"exists": False}
    assert d["after"] == {"exists": True, "status": "posted", "status_detail": "posted", "amount": 200000.0, "amount_applied": 0.0,
                          "amount_refunded": 0.0, "remaining": 200000.0, "position_text": CD._umd.status_text_um("posted")}
    assert d["journals"] == [{"journal_number": None, "source_type": "CUSTOMER_DEPOSIT", "journal_date": "2026-11-03",
                              "description": "Customer Deposit DEP-2611-0009 - Toko Melati",
                              "lines": [{"account_code": "1-10100", "account_name": "Kas", "debit": 200000.0, "credit": 0.0},
                                        {"account_code": "2-10400", "account_name": "Uang Muka Pelanggan", "debit": 0.0,
                                         "credit": 200000.0}]}]
    assert d["bank_transactions"] == [{"bank_account": "BCA Operasional", "amount": 200000.0, "transaction_type": "deposit"}]
    assert d["order"] == {"sales_order_id": str(SO), "order_number": "SO-2611-0001", "total_amount": 1000000.0,
                          "received_before": 50000.0, "received_after": 250000.0, "dp_required": 300000.0}
    assert d["proforma"] == {"proforma_id": str(PF), "proforma_number": "PRO-2611-0001", "amount": 500000.0, "paid_before": 100000.0,
                             "remaining_before": 400000.0, "remaining_after": 200000.0, "paid_off_before": False, "estimated": True}
    assert d["order_code_preview"] == {"order_code": "SPK-2611-005", "issued": True}
    assert d["payload"]["amount"] == 200000 and d["preview"] is True
    assert not any(q.startswith(("INSERT", "UPDATE")) or "FOR UPDATE" in q or "pg_advisory" in q for q in k.sql)


@pytest.mark.asyncio
async def test_isi_pratinjau_draf_tanpa_jurnal_sisa_proforma_tetap(_isi_palsu):
    d = await _isi(Kon(), proforma_id=str(PF), auto_post=False)
    assert d["journals"] == [] and d["bank_transactions"] == []
    assert d["after"]["status"] == "draft" and d["after"]["remaining"] == 0.0
    assert d["proforma"]["remaining_after"] == d["proforma"]["remaining_before"] == 400000.0
    assert d["order_code_preview"] is None


@pytest.mark.asyncio
async def test_isi_pratinjau_tanpa_rekening_bank_tanpa_cermin_dan_tanpa_so_kode_tak_terbit(_isi_palsu):
    k = Kon()
    k.rekening = None
    d = await _isi(k, sales_order_id=None, terbitkan_kode_order=True)
    assert d["bank_transactions"] == [] and len(d["journals"]) == 1
    assert d["order"] is None and d["order_code_preview"] == {"order_code": None, "issued": False}


@pytest.mark.asyncio
async def test_isi_pratinjau_terblok_tanpa_nomor_tanpa_jurnal(_isi_palsu):
    d = await _isi(Kon(so_status="draft"))
    assert d["can_proceed"] is False and [b["code"] for b in d["blocks"]] == ["SO_NOT_ACCEPTING_DEPOSIT"]
    assert set(d["blocks"][0]) == {"code", "message"}
    assert d["deposit_number_preview"] is None and d["deposit_number_estimated"] is False
    assert d["journals"] == [] and d["bank_transactions"] == []
