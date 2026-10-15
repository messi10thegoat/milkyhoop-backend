"""W-20261013-LAMPIRAN-SO -- GET /api/sales-orders/{id}/lampiran (tab Lampiran panel SO).

Yang dijaga:
  * rute literal dimenangkan handler keluarga lewat TABEL RUTE app (jebakan /bulk), dan
    url tiap grup diselesaikan app ke rute /download modulnya sendiri;
  * izin rute = sales_order R; izin per GRUP = izin rute /download modul grup itu
    (ROUTE_PERMISSIONS yang SAMA + PolicyEngine middleware);
  * peran tanpa READ customer_deposit -> grup DP diganti {jenis, tersembunyi_izin, jumlah}
    TANPA nomor dokumen / nama berkas;
  * baris 'local' lama -> tetap dikirim, tersedia=false, url null (tidak hilang diam);
  * SETIAP tabel di SQL keluarga berpredikat tenant eksplisit (+ kontrol merah);
  * SO tenant lain -> 404 tanpa kueri keluarga.
"""
import asyncio
import os
import re
import uuid
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

from app.middleware import permission_middleware as pm
from app.routers import sales_order_lampiran as SL

TENANT = "grapgrap-manado"
LAIN = "kaos-biru-konveksi"
SO = uuid.UUID("31ee6038-074c-428e-8408-18d404d9fbb1")
DP = uuid.UUID("f766f4b4-a09f-4a92-9aed-7319d6ab25f0")
SI = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001")
DOK = uuid.UUID("9d5e0c53-08ef-441a-ac81-d7767e81f82e")
DOK_LOCAL = uuid.UUID("bbbbbbbb-0000-0000-0000-000000000002")
SA = uuid.UUID("cccccccc-0000-0000-0000-000000000003")
WAKTU = datetime(2026, 10, 13, 9, 47, tzinfo=timezone.utc)


def baris(jenis, dok_id, nomor, lampiran_id, nama, *, status="posted", storage="s3", sumber="hub", tgl=date(2026, 10, 13)):
    return {
        "jenis": jenis, "dok_id": dok_id, "nomor": nomor, "tanggal": tgl, "status": status,
        "lampiran_id": lampiran_id, "nama": nama, "mime": "image/png", "ukuran": 948304,
        "storage_type": storage, "uploaded_at": WAKTU, "pengunggah": "Kasir", "sumber": sumber,
    }


BARIS_SO19 = [
    baris("customer_deposit", DP, "DEP-2610-0015", DOK, "IMG_1960.png"),
]


# ------------------------------------------------------------------ tabel rute
def _pemenang(path, metode="GET"):
    from starlette.routing import Match

    from app.main import app

    scope = {"type": "http", "method": metode, "path": path, "root_path": "", "headers": []}
    for r in app.routes:
        m, _ = r.matches(scope)
        if m == Match.FULL:
            return r
    return None


def test_rute_lampiran_dimenangkan_handler_keluarga_lewat_tabel_rute():
    r = _pemenang(f"/api/sales-orders/{SO}/lampiran")
    assert r is not None
    assert r.endpoint.__name__ == "lampiran_keluarga_so"
    assert r.endpoint.__module__.rsplit(".", 1)[-1] == "sales_order_lampiran"
    assert r.path == "/api/sales-orders/{order_id}/lampiran"
    # kontrol: pencocokan yang sama memang memilih rute {id} untuk jalur tunggal
    r2 = _pemenang(f"/api/sales-orders/{SO}")
    assert r2 is not None and r2.path == "/api/sales-orders/{order_id}"


@pytest.mark.parametrize("jenis,modul,akhir", [
    ("sales_order", "sales_order_lampiran", "unduh_lampiran_so"),
    ("customer_deposit", "customer_deposits", "download_deposit_attachment"),
    ("receive_payment", "receive_payments", "download_payment_attachment"),
    ("delivery", "delivery_lampiran", "unduh_lampiran_sj"),
    ("sales_invoice", "sales_invoices", "download_invoice_attachment"),
])
def test_url_tiap_grup_diselesaikan_ke_rute_download_modulnya(jenis, modul, akhir):
    url = SL.url_unduh_lampiran(SL.JENIS_KELUARGA[jenis][0], uuid.uuid4(), uuid.uuid4())
    r = _pemenang(url)
    assert r is not None, url
    assert r.endpoint.__module__.rsplit(".", 1)[-1] == modul
    assert r.endpoint.__name__ == akhir


# ------------------------------------------------------------------ izin
def test_izin_rute_lampiran_sales_order_R():
    assert pm.izin_untuk_jalur(f"/api/sales-orders/{SO}/lampiran") == ("sales_order", "R")


@pytest.mark.parametrize("jenis,harap", [
    ("sales_order", ("sales_order", "R")),
    ("quote", ("quote", "R")),
    ("proforma", ("proforma", "R")),
    ("customer_deposit", ("customer_deposit", "R")),
    ("receive_payment", ("receive_payment", "R")),
    ("delivery", ("sales_invoice", "R")),
    ("sales_invoice", ("sales_invoice", "R")),
    ("credit_note", ("credit_note", "R")),
])
def test_izin_grup_sama_dengan_izin_rute_download_modulnya(jenis, harap):
    assert pm.izin_untuk_jalur(SL.jalur_izin_jenis(jenis)) == harap


def test_semua_jenis_keluarga_terpetakan():
    assert set(SL.JENIS_KELUARGA) == set(SL.URUTAN_JENIS)
    for j in SL.JENIS_KELUARGA:
        assert pm.izin_untuk_jalur(SL.jalur_izin_jenis(j)) is not None, j


class PEPalsu:
    def __init__(self, tolak=(), kode="ADMIN", aktif=True, role_id="r1"):
        self.tolak = set(tolak)
        self.ctx = SimpleNamespace(membership_active=aktif, business_role_id=role_id, business_role_code=kode)
        self.panggil_ctx = 0
        self.ditanya = []

    async def get_user_context(self, user_id, tenant_id, subscription_role):
        self.panggil_ctx += 1
        return self.ctx

    async def can(self, ctx, aksi, modul):
        self.ditanya.append((modul, aksi))
        return modul not in self.tolak


def test_izin_rute_pengguna_konteks_sekali_dan_tak_terpetakan_hanya_owner(monkeypatch):
    pe = PEPalsu(tolak={"customer_deposit"})
    monkeypatch.setattr(pm, "get_policy_engine", lambda: pe)
    user = {"user_id": "u", "tenant_id": TENANT}
    j = [SL.jalur_izin_jenis("customer_deposit"), SL.jalur_izin_jenis("sales_invoice"), "/api/tak-ada/x"]
    h = asyncio.run(pm.izin_rute_pengguna(user, j))
    assert h == {j[0]: False, j[1]: True, j[2]: False}
    assert pe.panggil_ctx == 1
    pe2 = PEPalsu(kode="OWNER")
    monkeypatch.setattr(pm, "get_policy_engine", lambda: pe2)
    assert asyncio.run(pm.izin_rute_pengguna(user, ["/api/tak-ada/x"])) == {"/api/tak-ada/x": True}
    pe3 = PEPalsu(aktif=False, kode="OWNER")
    monkeypatch.setattr(pm, "get_policy_engine", lambda: pe3)
    assert asyncio.run(pm.izin_rute_pengguna(user, [j[1]])) == {j[1]: False}


# ------------------------------------------------------------------ handler (DB palsu)
class Txn:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class Conn:
    def __init__(self, tenant_so, rows):
        self.tenant_so = tenant_so
        self.rows = rows
        self.fetch_dipanggil = []

    def transaction(self):
        return Txn()

    async def execute(self, sql, *a):
        return None

    async def fetchval(self, sql, *a):
        assert sql == SL._SQL_SO
        oid, tenant = a
        return oid if (oid == SO and tenant == self.tenant_so) else None

    async def fetch(self, sql, *a):
        self.fetch_dipanggil.append((sql, a))
        # LAMPIRAN-TANPA-SO: benih $3..$8 KOSONG untuk keluarga SO (hasil == SQL lama; pasangan nyata di harness)
        assert sql == SL._SQL_KELUARGA and a == (SO, TENANT, [], [], [], [], [], [])
        return self.rows


class Acq:
    def __init__(self, c):
        self.c = c

    async def __aenter__(self):
        return self.c

    async def __aexit__(self, *a):
        return False


class Pool:
    def __init__(self, c):
        self.c = c

    def acquire(self):
        return Acq(self.c)


def _jalankan(monkeypatch, conn, pe, tenant=TENANT):
    async def gp():
        return Pool(conn)

    monkeypatch.setattr(SL, "get_pool", gp)
    monkeypatch.setattr(pm, "get_policy_engine", lambda: pe)
    req = SimpleNamespace(state=SimpleNamespace(user={"user_id": "u1", "tenant_id": tenant, "role": "ADMIN"}))
    return asyncio.run(SL.lampiran_keluarga_so(req, SO))


def test_so19_grup_dp_berisi_bukti_transfer_dengan_url_download(monkeypatch):
    out = _jalankan(monkeypatch, Conn(TENANT, BARIS_SO19), PEPalsu())
    assert out["jumlah"] == 1
    (g,) = out["data"]
    assert g["dokumen"] == {"jenis": "customer_deposit", "id": str(DP), "nomor": "DEP-2610-0015",
                            "tanggal": "2026-10-13", "status": "posted", "void": False}
    (lp,) = g["lampiran"]
    assert lp["display_name"] == "IMG_1960.png" and lp["tersedia"] is True
    assert lp["url"] == f"/api/customer-deposits/{DP}/attachments/{DOK}/download"
    assert lp["diunggah_oleh"] == "Kasir" and lp["ukuran"] == 948304 and lp["mime"] == "image/png"


def test_peran_tanpa_read_dp_grup_tersembunyi_tanpa_nama_berkas(monkeypatch):
    rows = BARIS_SO19 + [baris("sales_order", SO, "SO-2610-0019", uuid.uuid4(), "desain.pdf", status="confirmed")]
    pe = PEPalsu(tolak={"customer_deposit"})
    out = _jalankan(monkeypatch, Conn(TENANT, rows), pe)
    teks = repr(out)
    assert "IMG_1960" not in teks and "DEP-2610-0015" not in teks and str(DP) not in teks
    assert out["data"][-1] == {"dokumen": {"jenis": "customer_deposit"}, "tersembunyi_izin": True, "jumlah": 1}
    assert out["data"][0]["dokumen"]["jenis"] == "sales_order"
    assert out["jumlah"] == 1  # hanya yang terlihat
    assert ("customer_deposit", "R") in pe.ditanya and ("sales_order", "R") in pe.ditanya


def test_kontrol_peran_dengan_read_dp_melihat_berkasnya(monkeypatch):
    out = _jalankan(monkeypatch, Conn(TENANT, BARIS_SO19), PEPalsu(tolak={"sales_invoice"}))
    assert "IMG_1960.png" in repr(out)


def test_so_tenant_lain_404_tanpa_kueri_keluarga(monkeypatch):
    conn = Conn(LAIN, BARIS_SO19)  # SO milik tenant lain
    with pytest.raises(HTTPException) as e:
        _jalankan(monkeypatch, conn, PEPalsu())
    assert e.value.status_code == 404
    assert conn.fetch_dipanggil == []


def test_tanpa_lampiran_tanpa_panggilan_policy(monkeypatch):
    pe = PEPalsu()
    out = _jalankan(monkeypatch, Conn(TENANT, []), pe)
    assert out == {"success": True, "data": [], "jumlah": 0}
    assert pe.panggil_ctx == 0


# ------------------------------------------------------------------ penyusun grup
def test_local_lama_tetap_dikirim_tersedia_false_url_null_dan_faktur_lama_tersedia():
    rows = [
        baris("customer_deposit", DP, "DEP-1", DOK_LOCAL, "lama.jpg", storage="local"),
        baris("sales_invoice", SI, "INV-1", SA, "bukti.jpg", storage=None, sumber="faktur_lama"),
    ]
    out = SL.susun_grup_lampiran(rows, {"customer_deposit": True, "sales_invoice": True})
    dp = out["data"][0]["lampiran"][0]
    assert dp["tersedia"] is False and dp["url"] is None and dp["display_name"] == "lama.jpg"
    si = out["data"][1]["lampiran"][0]
    assert si["tersedia"] is True and si["url"] == f"/api/sales-invoices/{SI}/attachments/{SA}/download"


def test_void_ditandai_urutan_jenis_dan_dedup_berkas_lintas_grup():
    rows = [
        baris("sales_invoice", SI, "INV-1", DOK, "a.png", status="void"),
        baris("customer_deposit", DP, "DEP-1", DOK, "a.png"),
        baris("customer_deposit", DP, "DEP-1", DOK, "a.png"),  # baris ganda (dua jalur keluarga)
        baris("sales_order", SO, "SO-1", uuid.uuid4(), "b.png", status="cancelled"),
    ]
    out = SL.susun_grup_lampiran(rows, {"sales_invoice": True, "customer_deposit": True, "sales_order": True})
    assert [g["dokumen"]["jenis"] for g in out["data"]] == ["sales_order", "customer_deposit", "sales_invoice"]
    assert out["data"][0]["dokumen"]["void"] is True and out["data"][2]["dokumen"]["void"] is True
    assert out["data"][1]["dokumen"]["void"] is False
    assert len(out["data"][1]["lampiran"]) == 1
    assert out["jumlah"] == 2  # DOK di dua grup dihitung sekali


def test_quote_proforma_tanpa_rute_unduh_url_null():
    q = uuid.uuid4()
    out = SL.susun_grup_lampiran([baris("quote", q, "Q-1", uuid.uuid4(), "x.pdf")], {"quote": True})
    assert out["data"][0]["lampiran"][0]["url"] is None


# ------------------------------------------------------------------ pagar tenant SQL
_CTE = {"so", "pf", "dp", "si", "rp", "meta", "rp0", "dp0", "sj0", "nk0"}
_TANPA_TENANT = {'"User"', "user_profiles"}  # tabel global (nama pengunggah)


def _tabel_tanpa_tenant(sql: str):
    salah = []
    alias_ada = [al for _, t, al in re.findall(r"\b(FROM|JOIN)\s+(\"User\"|\w+)\s+(\w+)", sql)
                 if t not in _CTE and t not in _TANPA_TENANT and al.upper() not in {"WHERE", "ON"}]
    if len(alias_ada) != len(set(alias_ada)):
        salah.append(f"alias ganda {sorted(alias_ada)}")
    for kata, tabel, alias in re.findall(r"\b(FROM|JOIN)\s+(\"User\"|\w+)\s+(\w+)", sql):
        if tabel in _CTE or tabel in _TANPA_TENANT:
            continue
        if alias.upper() in {"WHERE", "ON"}:
            if not re.search(rf"FROM\s+{tabel}\s+WHERE\s+[^)]*\btenant_id\s*=\s*\$2", sql):
                salah.append(tabel)
            continue
        if not re.search(rf"\b{alias}\.tenant_id\s*=\s*\$2", sql):
            salah.append(f"{tabel} {alias}")
    return salah


def test_setiap_tabel_sql_keluarga_berpredikat_tenant():
    tabel = re.findall(r"\b(?:FROM|JOIN)\s+(\"User\"|\w+)", SL._SQL_KELUARGA)
    nyata = {t for t in tabel if t not in _CTE}
    assert {"sales_orders", "proformas", "customer_deposits", "sales_invoices", "receive_payments",
            "receive_payment_allocations", "quotes", "invoice_fulfillments", "document_attachments",
            "documents", "sales_invoice_attachments", "credit_notes"} <= nyata
    assert _tabel_tanpa_tenant(SL._SQL_KELUARGA) == []


@pytest.mark.parametrize("hapus", [
    "AND da.tenant_id = $2", "AND d.tenant_id = $2", "AND sa.tenant_id = $2",
    "WHERE r.tenant_id = $2 AND r.id IN", "AND alx.tenant_id = $2", "WHERE pfx.tenant_id = $2",
])
def test_kontrol_merah_predikat_tenant_dicabut_tertangkap(hapus):
    assert SL._SQL_KELUARGA.count(hapus) == 1
    ganti = {"WHERE r.tenant_id = $2 AND r.id IN": "WHERE r.id IN", "WHERE pfx.tenant_id = $2": ""}
    rusak = SL._SQL_KELUARGA.replace(hapus, ganti.get(hapus, ""), 1)
    assert _tabel_tanpa_tenant(rusak) != []


def test_penanda_jendela_ada():
    assert SL.PENANDA_LAMPIRAN_KELUARGA == "lampiran-so-keluarga-w20261013"
