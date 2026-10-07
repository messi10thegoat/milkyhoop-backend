"""Fase LOKASI pesanan (7 Okt 2026, MASTER GO opsi A): data OPERASIONAL -- nol jurnal/stok/HPP.

Latar: pemilik grapgrap -- produksi di Bandung, toko & pengambilan di Manado. Tahap "Dikirim ke Toko Manado" / "Tersedia di Toko Manado"
tersimpan (barang non_inventory tak punya stok untuk menurunkannya). Ironlaws: Law 1/16/29 (outstanding_amount = turunan jurnal via
ringkasan_pesanan, bukan kolom baru), Law 8 (perubahan tercatat di riwayat), Law 12/13/14/23/24, Law 31 gate 4 otomatis lulus (nol CoA)."""
import asyncio
import inspect
import json
import os
import re
import uuid
from datetime import datetime
from decimal import Decimal

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

import pytest
from fastapi import HTTPException
from starlette.routing import Match

from app.middleware import permission_middleware as PM
from app.routers import so_fase_lokasi as R
from app.services import so_agregat as AG
from app.services import so_fase_lokasi as SF
from app.services import so_posisi as P

T, U = "t-uji", "0bccdb25-fdf0-4e99-9024-b9a20846f76c"
SO, GD, GD2 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
CTX = {"tenant_id": T, "user_id": U}


def _jalan(c): return asyncio.run(c)


# ---------------- teks & posisi ----------------
def test_teks_fase():
    assert SF.teks_fase("tersedia", "Toko Manado") == "Tersedia di Toko Manado"
    assert SF.teks_fase("dikirim_ke_lokasi", "Toko Manado") == "Dikirim ke Toko Manado"
    assert SF.teks_fase(None, "x") is None and SF.teks_fase("tersedia", None) == "Tersedia di —"


def _pos(status="confirmed", sisa="100", belum_bayar=(), fase=None, ada_faktur=False):
    return P.posisi(status, Decimal(sisa), list(belum_bayar), ada_faktur, Decimal("0"), False, Decimal("0"), fase)


def test_posisi_fase_di_depan_pembayaran_tapi_batal_dan_lunas_menang():
    f = "Tersedia di Toko Manado"
    assert _pos(fase=f) == (f, False)
    assert _pos(belum_bayar=[{"purpose": "DP", "termin_ke": None}], fase=f) == (f, False)   # di depan "Menunggu DP"
    assert _pos(status="cancelled", fase=f)[0] == "Batal"
    assert _pos(sisa="0", fase=f)[0] == "Lunas"
    assert _pos()[0] == "Belum ditagih"   # tanpa fase = perilaku lama utuh


# ---------------- penentu & tulis (koneksi palsu) ----------------
class _K:
    def __init__(self, order, gudang=None, gudang_lama=None):
        self.order, self.gudang, self.gudang_lama = order, gudang or {}, gudang_lama
        self.sql, self.audit = [], []

    async def fetchrow(self, sql, *a):
        s = " ".join(sql.split())
        self.sql.append(s)
        if "FROM sales_orders" in s:
            assert "tenant_id = $2" in s  # tenant eksplisit
            return self.order
        if "FROM warehouses" in s:
            assert "tenant_id = $2" in s
            return self.gudang.get(a[0])
        raise AssertionError(s)

    async def execute(self, sql, *a):
        s = " ".join(sql.split())
        self.sql.append(s)
        if "INSERT INTO audit_logs" in s:
            self.audit.append(a)
        return "OK"


def _order(status="confirmed", fase=None, gid=None):
    return {"id": SO, "order_number": "SO-2610-0007", "status": status, "fase_lokasi": fase, "fase_gudang_id": gid}


G = {GD: {"id": GD, "name": "Toko Manado", "is_active": True}, GD2: {"id": GD2, "name": "Gudang Mati", "is_active": False}}


def test_rencana_blok_status_gudang_pasangan():
    for st in ("draft", "cancelled", "completed"):
        r = _jalan(SF.rencana(_K(_order(st), G), CTX, SO, "tersedia", GD))
        assert [b["code"] for b in r["blocks"]] == ["SO_STATUS_TIDAK_BOLEH"]
    r = _jalan(SF.rencana(_K(_order(), G), CTX, SO, "tersedia", uuid.uuid4()))
    assert r["blocks"][0]["code"] == "GUDANG_TAK_ADA" and r["blocks"][0]["status"] == 404
    r = _jalan(SF.rencana(_K(_order(), G), CTX, SO, "tersedia", GD2))
    assert r["blocks"][0]["code"] == "GUDANG_NONAKTIF"
    r = _jalan(SF.rencana(_K(_order(), G), CTX, SO, "tersedia", None))
    assert r["blocks"][0]["code"] == "FASE_GUDANG_PASANGAN"
    r = _jalan(SF.rencana(_K(_order(), G), CTX, SO, None, GD))
    assert r["blocks"][0]["code"] == "FASE_GUDANG_PASANGAN"
    ok = _jalan(SF.rencana(_K(_order(), G), CTX, SO, "tersedia", GD))
    assert ok["blocks"] == [] and ok["berubah"]
    with pytest.raises(HTTPException) as e:
        _jalan(SF.rencana(_K(None, G), CTX, SO, "tersedia", GD))
    assert e.value.status_code == 404


def test_tulis_maju_mundur_hapus_tercatat_riwayat():
    k = _K(_order(), G)
    d = _jalan(SF.terapkan(k, CTX, SO, "dikirim_ke_lokasi", GD))
    assert d["position_text"] == "Dikirim ke Toko Manado" and d["berubah"] and d["fase_gudang_nama"] == "Toko Manado"
    up = [s for s in k.sql if s.startswith("UPDATE sales_orders")]
    assert len(up) == 1 and "fase_at = NOW()" in up[0] and "tenant_id = $2" in up[0]
    a = k.audit[0]  # catat_riwayat: (userId, event, entity_type, entity_id, nomor, tenant, source, metadata)
    ev, et, num, tenant, meta = a[1], a[2], a[4], a[5], a[7]
    assert (ev, et, tenant, num) == ("SO_FASE_LOKASI_CHANGED", "sales_orders", T, "SO-2610-0007")
    m = json.loads(meta)
    assert m["ringkas"] == "Posisi pesanan: Dikirim ke Toko Manado" and m["lama"]["fase_lokasi"] is None and m["baru"]["fase_lokasi"] == "dikirim_ke_lokasi"
    # maju
    k2 = _K(_order(fase="dikirim_ke_lokasi", gid=GD), G)
    _jalan(SF.terapkan(k2, CTX, SO, "tersedia", GD))
    assert json.loads(k2.audit[0][7])["ringkas"] == "Posisi pesanan: Tersedia di Toko Manado (sebelumnya Dikirim ke Toko Manado)"
    # mundur
    k3 = _K(_order(fase="tersedia", gid=GD), G)
    _jalan(SF.terapkan(k3, CTX, SO, "dikirim_ke_lokasi", GD))
    assert "(sebelumnya Tersedia di Toko Manado)" in json.loads(k3.audit[0][7])["ringkas"]
    # hapus
    k4 = _K(_order(fase="tersedia", gid=GD), G)
    d4 = _jalan(SF.terapkan(k4, CTX, SO, None, None))
    assert d4["position_text"] is None and any("fase_lokasi = NULL" in s for s in k4.sql)
    assert json.loads(k4.audit[0][7])["ringkas"] == "Posisi lokasi dihapus (sebelumnya Tersedia di Toko Manado)"


def test_tulis_sama_persis_tanpa_update_dan_tanpa_riwayat():
    k = _K(_order(fase="tersedia", gid=GD), G)
    d = _jalan(SF.terapkan(k, CTX, SO, "tersedia", GD))
    assert d["berubah"] is False and not k.audit and not any(s.startswith("UPDATE") for s in k.sql)


def test_tulis_diblok_kode_sama_dengan_pratinjau_dan_tanpa_tulis():
    for st, kode in (("draft", "SO_STATUS_TIDAK_BOLEH"), ("cancelled", "SO_STATUS_TIDAK_BOLEH")):
        k = _K(_order(st), G)
        with pytest.raises(HTTPException) as e:
            _jalan(SF.terapkan(k, CTX, SO, "tersedia", GD))
        assert e.value.detail["code"] == kode and e.value.status_code == 409
        assert not k.audit and not any(s.startswith("UPDATE") for s in k.sql)


def test_NOL_jurnal_stok_hpp_bank_satu_satunya_tabel_tulis_adalah_sales_orders():
    k = _K(_order(), G)
    _jalan(SF.terapkan(k, CTX, SO, "tersedia", GD))
    semua = " ".join(k.sql).lower()
    for terlarang in ("journal_entries", "journal_lines", "inventory_ledger", "warehouse_stock", "bank_transactions",
                      "receive_payments", "customer_deposits", "invoice_fulfillments", "sales_invoices"):
        assert terlarang not in semua, terlarang
    tulis = [s for s in k.sql if re.match(r"(INSERT|UPDATE|DELETE)", s)]
    assert {re.match(r"(?:UPDATE|INSERT INTO|DELETE FROM)\s+(\w+)", s).group(1) for s in tulis} == {"sales_orders", "audit_logs"}


# ---------------- rute ----------------
def test_rute_dimenangkan_handler_benar_lewat_tabel_rute_dan_izin_U():
    from app.main import app

    def pemenang(path):
        scope = {"type": "http", "method": "POST", "path": path, "root_path": "", "headers": []}
        for r in app.routes:
            m, _ = r.matches(scope)
            if m == Match.FULL:
                return r.endpoint
        return None
    assert pemenang(f"/api/sales-orders/{SO}/fase-lokasi") is R.fase_lokasi_ubah
    assert pemenang(f"/api/sales-orders/{SO}/fase-lokasi/preview") is R.fase_lokasi_preview
    mw = PM.PermissionMiddleware(lambda *a: None, False)
    assert mw._find_permission(f"/api/sales-orders/{SO}/fase-lokasi", "POST") == ("sales_order", "U")
    assert mw._find_permission(f"/api/sales-orders/{SO}/fase-lokasi/preview", "POST") == ("sales_order", "U")


class _Tx:
    async def __aenter__(self): return None
    async def __aexit__(self, *e): return False


class _KR(_K):
    def transaction(self): return _Tx()


class _Pool:
    def __init__(self, k): self.k = k

    def acquire(self):
        k = self.k

        class A:
            async def __aenter__(s): return k
            async def __aexit__(s, *e): return False
        return A()


class _Req:
    def __init__(self, kunci=None):
        self.state = type("S", (), {"user": {"tenant_id": T, "user_id": U}})()
        self.headers = {"X-Idempotency-Key": kunci} if kunci else {}


def _pasang(monkeypatch, k):
    async def gp(): return _Pool(k)
    monkeypatch.setattr(R, "get_pool", gp)


def test_pratinjau_nol_tulis_dan_memuat_blok(monkeypatch):
    k = _KR(_order("draft"), G)
    _pasang(monkeypatch, k)
    out = _jalan(R.fase_lokasi_preview(_Req(), str(SO), R.FaseLokasiRequest(fase="tersedia", warehouse_id=GD)))
    assert out["data"]["can"] is False and out["data"]["blocks"][0]["code"] == "SO_STATUS_TIDAK_BOLEH"
    assert not k.audit and not any(re.match(r"(INSERT|UPDATE|DELETE)", s) for s in k.sql)
    k2 = _KR(_order(), G)
    _pasang(monkeypatch, k2)
    ok = _jalan(R.fase_lokasi_preview(_Req(), str(SO), R.FaseLokasiRequest(fase="tersedia", warehouse_id=GD)))
    assert ok["data"]["can"] and ok["data"]["position_text"] == "Tersedia di Toko Manado"
    assert not any(re.match(r"(INSERT|UPDATE|DELETE)", s) for s in k2.sql)


def test_tulis_idempotensi_replay_tanpa_tulis_ulang(monkeypatch):
    k = _KR(_order(), G)
    _pasang(monkeypatch, k)

    async def mulai(conn, ctx, kunci, prefix, doc, isi, response=None):
        assert prefix == "SO_FASE" and kunci == "K-1"
        return "kp", "sd", {"success": True, "data": {"replay": True}}
    monkeypatch.setattr(R.idem_buat, "mulai_aksi", mulai)
    out = _jalan(R.fase_lokasi_ubah(_Req("K-1"), str(SO), R.FaseLokasiRequest(fase="tersedia", warehouse_id=GD)))
    assert out == {"success": True, "data": {"replay": True}} and not k.audit


def test_tulis_tanpa_kunci_tetap_jalan_dan_id_buruk_404(monkeypatch):
    k = _KR(_order(), G)
    _pasang(monkeypatch, k)
    out = _jalan(R.fase_lokasi_ubah(_Req(), str(SO), R.FaseLokasiRequest(fase="tersedia", warehouse_id=GD), None))
    assert out["data"]["position_text"] == "Tersedia di Toko Manado" and k.audit
    with pytest.raises(HTTPException) as e:
        _jalan(R.fase_lokasi_preview(_Req(), "bukan-uuid", R.FaseLokasiRequest()))
    assert e.value.status_code == 404


# ---------------- daftar: filter, kartu, urut, kolom ----------------
def test_satu_predikat_tersedia_untuk_kartu_filter_dan_baris():
    assert "PREDIKAT_TERSEDIA" in inspect.getsource(AG.id_tersedia) and "PREDIKAT_TERSEDIA" in inspect.getsource(AG.jumlah_tersedia)
    assert "fase_lokasi = 'tersedia'" in AG.PREDIKAT_TERSEDIA and "status <> ALL" in AG.PREDIKAT_TERSEDIA
    from app.services import dashboard_v2 as D
    assert "so_agregat" in inspect.getsource(D.id_tugas) and "tersedia" in inspect.getsource(D.id_tugas)
    from app.routers import sales_orders as SOR
    src = inspect.getsource(SOR.list_sales_orders) if hasattr(SOR, "list_sales_orders") else inspect.getsource(SOR)
    assert '"tersedia"' in src and 'sort_by' in src
    assert "tersedia_count" in inspect.getsource(SOR.get_sales_order_summary)


def test_skema_daftar_dan_ringkasan_punya_kolom_baru():
    from app.schemas.sales_orders import SalesOrderDetail, SalesOrderListItem, SalesOrderSummary
    for f in ("outstanding_amount", "fase_lokasi", "fase_gudang_id", "fase_gudang_nama"):
        assert f in SalesOrderListItem.model_fields
    assert "tersedia_count" in SalesOrderSummary.model_fields and "fase_lokasi" in SalesOrderDetail.model_fields


def test_urut_outstanding_turunan_jurnal_draf_batal_di_ujung(monkeypatch):
    a, b, c, d = (uuid.uuid4() for _ in range(4))
    rows = [{"id": a, "total_amount": 1000, "status": "confirmed"}, {"id": b, "total_amount": 500, "status": "confirmed"},
            {"id": c, "total_amount": 900, "status": "cancelled"}, {"id": d, "total_amount": 800, "status": "invoiced"}]

    async def rg(conn, tenant, ids):
        assert tenant == T
        tertutup = {a: Decimal("100"), b: Decimal("0"), c: Decimal("0"), d: Decimal("800")}   # sisa: a=900 b=500 c=- d=0
        return {i: {"tertutup": tertutup[i]} for i in ids}
    monkeypatch.setattr(P, "ringkasan_pesanan", rg)
    assert _jalan(P.urut_outstanding(None, T, rows, "desc")) == [a, b, d, c]
    assert _jalan(P.urut_outstanding(None, T, rows, "asc")) == [d, b, a, c]   # batal tetap di ujung


def test_fakta_daftar_isi_outstanding_dan_fase_tanpa_rumus_uang_baru():
    src = inspect.getsource(P.fakta_daftar)
    assert "ringkasan_pesanan" in src and 'max(sisa, NOL)' in src   # sumber jurnal yang SAMA dengan 'Lunas' di posisi()
    assert "SUM(" not in src.split("outstanding_amount")[0].split("hasil = {}")[-1]


# ---------------- migrasi ----------------
def _sql(nama):
    here = os.path.dirname(__file__)
    return open(os.path.join(here, "..", "..", "..", "migrations", nama), encoding="utf-8").read()


def test_migrasi_aditif_tanpa_menyentuh_keuangan_dan_gagal_keras():
    s = _sql("V399__so_fase_lokasi.sql")
    low = s.lower()
    for terlarang in ("journal", "inventory", "bank_", "update sales_orders", "delete from"):
        assert terlarang not in re.sub(r"--[^\n]*", "", low), terlarang   # kode SQL (bukan komentar) tak menyentuh keuangan/isi lama
    assert "chk_so_fase_lokasi" in s and "chk_so_fase_lengkap" in s and "uq_warehouses_id_tenant" in s
    assert "FOREIGN KEY (fase_gudang_id, tenant_id) REFERENCES warehouses (id, tenant_id)" in s   # gudang WAJIB satu tenant (Law 24 di DB)
    assert "RAISE EXCEPTION" in s and "dikirim_ke_lokasi" in s and "tersedia" in s
    r = _sql("V399__so_fase_lokasi_ROLLBACK.sql")
    assert "DROP COLUMN" in r and "fk_so_fase_gudang" in r
