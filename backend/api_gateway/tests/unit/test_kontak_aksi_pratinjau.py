"""KONTAK standar SO (9 Nov 2026, unit pemilik langsung; kontrak lewat MASTER): Pelanggan + Vendor.

1   aksi_tersedia bentuk bersama (schemas/aksi_tersedia) dari aksi_detail YANG SAMA.
2a  POST /vendors/preview -- penentu SAMA dengan POST /vendors (_siapkan_buat_vendor).
2b  pratinjau status / hapus kedua modul + alias PATCH /customers/{id}/status (penulis lama, nol logika tulis baru).
K-B POST /{id}/edit/preview kedua modul -- penentu SAMA dengan PATCH; catatan rename-impact satu penentu dengan /rename-impact.
S   GET /vendors/{id}/rename-impact (kembar pelanggan; KDT.dampak_ganti_nama).
Setiap pratinjau: transaksi READ ONLY + rollback, nol tulis; 404 diteruskan; galat penentu -> blok (kosakata blok_dari_galat).
"""
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routers import customers as cu
from app.routers import vendors as ve
from app.services import kontak_detail as KDT
from app.services.galat_berkode import GalatBerkode

TENANT = "kaos-biru-konveksi"
ID = uuid.UUID("11111111-1111-1111-1111-111111111111")


class _Tr:
    def __init__(self, conn, readonly):
        self.conn, self.readonly = conn, readonly

    async def start(self):
        self.conn.log.append(("start", self.readonly))

    async def rollback(self):
        self.conn.log.append(("rollback", self.readonly))

    async def __aenter__(self):
        self.conn.log.append(("tx", self.readonly))

    async def __aexit__(self, *a):
        return False


class Conn:
    """Fake asyncpg: baris pihak per tabel, jumlah cek_jejak per tabel, hitung rename per tabel."""

    def __init__(self, pihak=None, jejak=None, angka=None):
        self.pihak = pihak
        self.jejak = jejak or {}
        self.angka = angka or {}
        self.log = []

    def transaction(self, readonly=False):
        return _Tr(self, readonly)

    async def execute(self, sql, *a):
        self.log.append(("execute", sql, a))

    async def fetchval(self, sql, *a):
        self.log.append(("fetchval", sql, a))
        if sql.startswith("SELECT COUNT(*) FROM "):
            return self.jejak.get(sql.split()[3], 0)
        return 1 if self.pihak else None

    async def fetchrow(self, sql, *a):
        self.log.append(("fetchrow", sql, a))
        if "count(*) AS total" in sql:
            t, b = self.angka.get(sql.rsplit(" FROM ", 1)[1].split()[0], (0, 0))
            return {"total": t, "nama_berbeda": b}
        return self.pihak

    def tulis(self):
        return [x for x in self.log if x[0] == "execute" and not x[1].lstrip().upper().startswith("SELECT")]


class _Acq:
    def __init__(self, c):
        self.c = c

    async def __aenter__(self):
        return self.c

    async def __aexit__(self, *a):
        return False


def pasang(monkeypatch, mod, conn):
    async def _pool():
        return SimpleNamespace(acquire=lambda: _Acq(conn))
    monkeypatch.setattr(mod, "get_pool", _pool)

    async def _if_match(request, table, row_id):
        return None
    monkeypatch.setattr("app.services.optimistic_concurrency.assert_if_match_row", _if_match)


def req():
    return SimpleNamespace(headers={}, state=SimpleNamespace(user={"tenant_id": TENANT, "user_id": "22222222-2222-2222-2222-222222222222"}))


def baca_saja(conn):
    """Pratinjau: dibuka READ ONLY lalu rollback, tanpa tulis."""
    assert ("start", True) in conn.log and ("rollback", True) in conn.log
    assert not conn.tulis()


# ---------------------------------------------------------------- 1 aksi_tersedia

def test_aksi_tersedia_dari_aksi_detail_yang_sama():
    aksi = KDT.aksi_detail(True, "Tidak bisa dihapus karena masih memiliki transaksi. Nonaktifkan saja.", "SA-1")
    peta = KDT.aksi_tersedia(aksi)
    assert list(peta) == [a["kunci"] for a in aksi] == ["ubah", "saldo_awal", "gabung", "nonaktifkan", "hapus"]
    for a in aksi:
        assert peta[a["kunci"]] == {"tersedia": a["boleh"], "alasan": None if a["boleh"] else a["alasan"]}
    assert peta["hapus"]["tersedia"] is False and "Nonaktifkan saja" in peta["hapus"]["alasan"]
    assert peta["saldo_awal"]["tersedia"] is False and "SA-1" in peta["saldo_awal"]["alasan"]
    assert list(KDT.aksi_tersedia(KDT.aksi_detail(False, None, None)))[3] == "aktifkan"


@pytest.mark.parametrize("skema", ["customers", "vendors"])
def test_aksi_tersedia_dideklarasikan_di_skema_detail(skema):
    """response_model membuang medan tak dideklarasikan (pola BUG-097): medan wajib ada dan bertipe bersama."""
    import importlib
    m = importlib.import_module(f"app.schemas.{skema}")
    kelas = m.CustomerDetail if skema == "customers" else m.VendorDetail
    from app.schemas.aksi_tersedia import AksiTersediaPeta
    assert kelas.model_fields["aksi_tersedia"].annotation == (AksiTersediaPeta | None)


@pytest.mark.parametrize("mod", [cu, ve])
def test_detail_mengirim_aksi_tersedia_dari_aksi_yang_sama(mod):
    import inspect
    src = inspect.getsource(mod.get_customer if mod is cu else mod.get_vendor)
    assert '"aksi_tersedia": KDT.aksi_tersedia(_aksi)' in src and '"aksi": _aksi' in src


# ---------------------------------------------------------------- S rename-impact vendor

@pytest.mark.asyncio
async def test_rename_impact_vendor(monkeypatch):
    conn = Conn(pihak={"id": ID, "nama": "CV Sumber"}, angka={"bills": (3, 1), "purchase_orders": (2, 0)})
    pasang(monkeypatch, ve, conn)
    r = (await ve.get_vendor_rename_impact(req(), ID))["data"]
    assert r["vendor_id"] == str(ID) and r["nama"] == "CV Sumber"
    assert r["total_dokumen"] == 5 and r["nama_berbeda"] == 1
    assert set(r["per_jenis"]) == {k for _, k in KDT.GANTI_NAMA["vendor"]["dok"]}
    assert "5 dokumen" in r["pesan"]
    hitung = [x for x in conn.log if x[0] == "fetchrow" and "count(*)" in x[1]]
    assert len(hitung) == len(KDT.GANTI_NAMA["vendor"]["dok"])
    for _, sql, a in hitung:
        assert "tenant_id = $1 AND vendor_id = $2" in sql and "vendor_name IS DISTINCT FROM $3" in sql
        assert a == (TENANT, ID, "CV Sumber")
    assert [x for x in conn.log if "FROM vendors" in x[1]][0][2] == (ID, TENANT)


@pytest.mark.asyncio
async def test_rename_impact_vendor_tak_ada_404(monkeypatch):
    pasang(monkeypatch, ve, Conn())
    with pytest.raises(HTTPException) as e:
        await ve.get_vendor_rename_impact(req(), ID)
    assert e.value.status_code == 404


def test_rename_pelanggan_satu_daftar_dengan_penentu():
    assert cu.RENAME_TABEL is KDT.GANTI_NAMA["customer"]["dok"]


# ---------------------------------------------------------------- 2b hapus

@pytest.mark.asyncio
@pytest.mark.parametrize("mod,fn,jejak,tabel", [(cu, "preview_delete_customer", "JEJAK_PELANGGAN", "customers"),
                                                (ve, "preview_delete_vendor", "JEJAK_VENDOR", "vendors")])
async def test_pratinjau_hapus_kalimat_sama_dengan_delete(monkeypatch, mod, fn, jejak, tabel):
    from app.services.smart_delete import smart_delete
    tabel_jejak = getattr(mod, jejak)[0][0]
    conn = Conn(pihak={"id": ID, "nama": "Nutrindo", "name": "Nutrindo"}, jejak={tabel_jejak: 2})
    pasang(monkeypatch, mod, conn)
    d = (await getattr(mod, fn)(req(), ID))["data"]
    with pytest.raises(HTTPException) as e:
        await smart_delete(conn, tenant_id=TENANT, entity_id=str(ID), table=tabel,
                           name_column="nama" if mod is cu else "name", ledger_checks=getattr(mod, jejak))
    assert d["ok"] is False and d["blocks"] == [{"code": "HTTP_400", "message": e.value.detail}]
    assert d["rencana"] == {"id": str(ID), "nama": "Nutrindo"}


@pytest.mark.asyncio
@pytest.mark.parametrize("mod,fn", [(cu, "preview_delete_customer"), (ve, "preview_delete_vendor")])
async def test_pratinjau_hapus_bersih_dan_404(monkeypatch, mod, fn):
    conn = Conn(pihak={"id": ID, "nama": "Nutrindo"})
    pasang(monkeypatch, mod, conn)
    d = (await getattr(mod, fn)(req(), ID))["data"]
    assert d["ok"] is True and d["blocks"] == [] and not conn.tulis()
    pasang(monkeypatch, mod, Conn())
    with pytest.raises(HTTPException) as e:
        await getattr(mod, fn)(req(), ID)
    assert e.value.status_code == 404


# ---------------------------------------------------------------- 2b status

@pytest.mark.asyncio
async def test_status_pelanggan_alias_memanggil_penulis_lama(monkeypatch):
    panggil = []

    async def _re(request, cid):
        panggil.append(("reactivate", cid))
        return {"success": True}

    async def _up(request, cid, body):
        panggil.append(("update", cid, body.model_fields_set, body.is_active))
        return {"success": True}
    monkeypatch.setattr(cu, "reactivate_customer", _re)
    monkeypatch.setattr(cu, "update_customer", _up)
    await cu.set_customer_status(req(), ID, status="active")
    await cu.set_customer_status(req(), ID, status="inactive")
    assert panggil == [("reactivate", str(ID)), ("update", str(ID), {"is_active"}, False)]


@pytest.mark.asyncio
async def test_pratinjau_aktifkan_pelanggan_penentu_sama(monkeypatch):
    conn = Conn(pihak={"id": ID, "nama": "N", "nomor_member": "C-1", "is_active": True, "deleted_at": None})
    pasang(monkeypatch, cu, conn)
    d = (await cu.preview_customer_status(req(), ID, status="active"))["data"]
    assert d["ok"] is False and d["blocks"][0]["message"] == "Pelanggan sudah aktif."
    baca_saja(conn)
    with pytest.raises(HTTPException) as e:
        await cu._cek_aktifkan_pelanggan(conn, TENANT, ID)
    assert e.value.detail == d["blocks"][0]["message"]

    seen = []

    async def _nomor(conn_, tid, nomor, kecuali_id=None):
        seen.append(nomor)
        raise GalatBerkode(409, "KODE_DIPAKAI", "Kode C-1 sudah dipakai.", "code")
    monkeypatch.setattr(cu, "tolak_nomor_dipakai", _nomor)
    conn2 = Conn(pihak={"id": ID, "nama": "N", "nomor_member": "C-1", "is_active": False, "deleted_at": None})
    pasang(monkeypatch, cu, conn2)
    d = (await cu.preview_customer_status(req(), ID, status="active"))["data"]
    assert seen == ["C-1"] and d["blocks"] == [{"code": "KODE_DIPAKAI", "message": "Kode C-1 sudah dipakai.", "field": "code"}]
    d = (await cu.preview_customer_status(req(), ID, status="inactive"))["data"]
    assert d == {"ok": True, "blocks": [], "rencana": {"is_active": False}}


@pytest.mark.asyncio
async def test_reactivate_memakai_penentu_aktifkan(monkeypatch):
    async def _cek(conn, tid, cid):
        raise HTTPException(status_code=400, detail="SENTINEL")
    monkeypatch.setattr(cu, "_cek_aktifkan_pelanggan", _cek)
    pasang(monkeypatch, cu, Conn())
    with pytest.raises(HTTPException) as e:
        await cu.reactivate_customer(req(), str(ID))
    assert e.value.detail == "SENTINEL"


@pytest.mark.asyncio
@pytest.mark.parametrize("mod,fn", [(cu, "preview_customer_status"), (ve, "preview_vendor_status")])
async def test_pratinjau_status_404(monkeypatch, mod, fn):
    pasang(monkeypatch, mod, Conn())
    with pytest.raises(HTTPException) as e:
        await getattr(mod, fn)(req(), ID, status="inactive")
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_pratinjau_status_vendor(monkeypatch):
    pasang(monkeypatch, ve, Conn(pihak={"id": ID}))
    d = (await ve.preview_vendor_status(req(), ID, status="inactive"))["data"]
    assert d == {"ok": True, "blocks": [], "rencana": {"is_active": False}}


# ---------------------------------------------------------------- 2a buat vendor

@pytest.mark.asyncio
async def test_pratinjau_buat_vendor(monkeypatch):
    async def _nama(conn, tid, nama, kecuali_id=None):
        if nama == "Ganda":
            raise GalatBerkode(409, "NAMA_DIPAKAI", "Nama Ganda sudah dipakai.", "name")

    async def _kode(conn, tid, kode, kecuali_id=None):
        return None
    monkeypatch.setattr(ve.VU, "tolak_nama_dipakai", _nama)
    monkeypatch.setattr(ve.VU, "tolak_kode_dipakai", _kode)
    conn = Conn()
    pasang(monkeypatch, ve, conn)
    d = (await ve.preview_create_vendor(req(), {"name": "CV Baru", "payment_terms_days": 14}))["data"]
    assert d["ok"] is True and d["blocks"] == [] and d["rencana"]["name"] == "CV Baru"
    assert d["rencana"]["payment_terms_days"] == 14 and d["payload"] == {"name": "CV Baru", "payment_terms_days": 14}
    baca_saja(conn)
    d = (await ve.preview_create_vendor(req(), {"name": "Ganda"}))["data"]
    assert d["ok"] is False and d["blocks"] == [{"code": "NAMA_DIPAKAI", "message": "Nama Ganda sudah dipakai.", "field": "name"}]
    d = (await ve.preview_create_vendor(req(), {"name": "CV Baru", "opening_balance": 5000}))["data"]
    assert d["ok"] is False and d["blocks"][0]["message"] == ve.PESAN_SALDO_AWAL_SAAT_BUAT
    d = (await ve.preview_create_vendor(req(), {"payment_terms_days": 14}))["data"]
    assert d["ok"] is False and d["blocks"][0]["code"] == "DATA_TIDAK_SAH" and d["rencana"] is None


@pytest.mark.asyncio
async def test_buat_vendor_memakai_penentu_yang_sama(monkeypatch):
    async def _siap(conn, tid, body):
        raise HTTPException(status_code=409, detail="SENTINEL")
    monkeypatch.setattr(ve, "_siapkan_buat_vendor", _siap)

    async def _mulai(*a, **k):
        return None, None, None
    monkeypatch.setattr(ve.idem_buat, "mulai", _mulai)
    pasang(monkeypatch, ve, Conn())
    with pytest.raises(HTTPException) as e:
        await ve.create_vendor(req(), ve.CreateVendorRequest(name="CV Baru"))
    assert e.value.detail == "SENTINEL"


# ---------------------------------------------------------------- K-B ubah

BARIS_PELANGGAN = {"id": ID, "nama": "Nutrindo", "telepon": "0812", "credit_limit": Decimal("1500000.50"), "jenis_id": None,
                   "nomor_dokumen": None, "notes": "lama"}


def _tolak_lolos(monkeypatch, mod):
    async def _ok(*a, **k):
        return None
    for nama in ("tolak_nama_dipakai", "tolak_nomor_dipakai", "pastikan_daftar_harga"):
        if hasattr(mod, nama):
            monkeypatch.setattr(mod, nama, _ok)


@pytest.mark.asyncio
async def test_pratinjau_ubah_pelanggan(monkeypatch):
    _tolak_lolos(monkeypatch, cu)

    async def _dampak(conn, tid, jenis, pid):
        assert (tid, jenis, pid) == (TENANT, "customer", ID)
        return {"pesan": "Nama baru hanya dipakai dokumen baru. 4 dokumen yang sudah ada tetap memakai nama saat dokumen itu dibuat."}
    monkeypatch.setattr(KDT, "dampak_ganti_nama", _dampak)
    conn = Conn(pihak=dict(BARIS_PELANGGAN))
    pasang(monkeypatch, cu, conn)
    body = {"name": "Nutrindo Jaya", "phone": "0812", "credit_limit": 2000000, "notes": None}
    d = (await cu.preview_update_customer(req(), ID, body))["data"]
    assert d["ok"] is True and d["blocks"] == [] and d["payload"] == body
    assert d["perubahan"] == [
        {"field": "name", "label": "Nama", "lama": "Nutrindo", "baru": "Nutrindo Jaya"},
        {"field": "credit_limit", "label": "Limit kredit", "lama": 1500000.5, "baru": 2000000},
        {"field": "notes", "label": "Catatan", "lama": "lama", "baru": None},
    ]
    assert d["catatan"] == ["Nama baru hanya dipakai dokumen baru. 4 dokumen yang sudah ada tetap memakai nama saat dokumen itu dibuat."]
    baca_saja(conn)
    q = [x for x in conn.log if x[0] == "fetchrow" and "FROM customers" in x[1]][0]
    assert "tenant_id = $2" in q[1] and q[2] == (ID, TENANT)


@pytest.mark.asyncio
async def test_pratinjau_ubah_pelanggan_blok_404_ifmatch(monkeypatch):
    _tolak_lolos(monkeypatch, cu)

    async def _nama(conn, tid, nama, kecuali_id=None):
        raise GalatBerkode(409, "NAMA_DIPAKAI", "Nama X sudah dipakai.", "name")
    monkeypatch.setattr(cu, "tolak_nama_dipakai", _nama)
    pasang(monkeypatch, cu, Conn(pihak=dict(BARIS_PELANGGAN)))
    d = (await cu.preview_update_customer(req(), ID, {"name": "X"}))["data"]
    assert d["ok"] is False and d["blocks"] == [{"code": "NAMA_DIPAKAI", "message": "Nama X sudah dipakai.", "field": "name"}]

    _tolak_lolos(monkeypatch, cu)

    async def _basi(request, table, row_id):
        raise HTTPException(status_code=412, detail="Data sudah diubah orang lain.")
    pasang(monkeypatch, cu, Conn(pihak=dict(BARIS_PELANGGAN)))
    monkeypatch.setattr("app.services.optimistic_concurrency.assert_if_match_row", _basi)
    d = (await cu.preview_update_customer(req(), ID, {"notes": "baru"}))["data"]
    assert d["blocks"] == [{"code": "HTTP_412", "message": "Data sudah diubah orang lain."}]

    pasang(monkeypatch, cu, Conn())
    with pytest.raises(HTTPException) as e:
        await cu.preview_update_customer(req(), ID, {"notes": "baru"})
    assert e.value.status_code == 404
    d = (await cu.preview_update_customer(req(), ID, {"payment_terms_days": "abc"}))["data"]
    assert d["blocks"][0]["code"] == "DATA_TIDAK_SAH"


@pytest.mark.asyncio
async def test_ubah_pelanggan_memakai_penentu_yang_sama(monkeypatch):
    async def _siap(conn, tid, cid, body):
        raise HTTPException(status_code=409, detail="SENTINEL")
    monkeypatch.setattr(cu, "_siapkan_ubah_pelanggan", _siap)
    pasang(monkeypatch, cu, Conn())
    with pytest.raises(HTTPException) as e:
        await cu.update_customer(req(), str(ID), cu.UpdateCustomerRequest(notes="x"))
    assert e.value.detail == "SENTINEL"


def test_perubahan_pelanggan_sama_dengan_riwayat_lama():
    """Banding & label = blok riwayat lama (nama kolom DB via UBAH_KOLOM; nilai sama tak dicatat)."""
    cur = {"nama": "A", "telepon": "1", "alamat": "Jl. Lama", "is_active": True}
    out = cu.perubahan_pelanggan(cur, {"name": "A", "phone": "2", "address": "Jl. Baru", "is_active": False, "code": "C-9"})
    assert [(x["field"], x["label"], x["lama"], x["baru"]) for x in out] == [
        ("phone", "Telepon", "1", "2"), ("address", "Alamat", "Jl. Lama", "Jl. Baru"),
        ("is_active", "Status aktif", True, False), ("code", "code", None, "C-9")]


BARIS_VENDOR = {"id": ID, "name": "CV Sumber", "code": "V-1", "vendor_type": "BADAN", "phone": "021", "credit_limit": None}


@pytest.mark.asyncio
async def test_pratinjau_ubah_vendor_tanpa_kunci(monkeypatch):
    async def _ok(*a, **k):
        return None
    monkeypatch.setattr(ve.VU, "tolak_nama_dipakai", _ok)
    monkeypatch.setattr(ve.VU, "tolak_kode_dipakai", _ok)

    async def _dampak(conn, tid, jenis, pid):
        assert jenis == "vendor"
        return {"pesan": None}
    monkeypatch.setattr(KDT, "dampak_ganti_nama", _dampak)
    conn = Conn(pihak=dict(BARIS_VENDOR))
    pasang(monkeypatch, ve, conn)
    body = {"name": "CV Sumber Baru", "code": "V-2", "phone": "021", "contacts": []}
    d = (await ve.preview_update_vendor(req(), ID, body))["data"]
    assert d["ok"] is True
    assert sorted(d["perubahan"], key=lambda x: x["field"]) == [
        {"field": "code", "label": "Kode", "lama": "V-1", "baru": "V-2"},
        {"field": "name", "label": "Nama", "lama": "CV Sumber", "baru": "CV Sumber Baru"}]
    assert d["catatan"] == [ve.ANAK_TEKS["contacts"]]
    baca_saja(conn)
    assert not [x for x in conn.log if x[0] == "execute" and "advisory" in x[1]]

    d = (await ve.preview_update_vendor(req(), ID, {"nik": "1234567890123456"}))["data"]
    assert d["blocks"] == [{"code": "HTTP_422", "message": "NIK hanya untuk pemasok Orang Pribadi"}]


@pytest.mark.asyncio
async def test_ubah_vendor_tulis_tetap_berkunci(monkeypatch):
    async def _ok(*a, **k):
        return None
    monkeypatch.setattr(ve.VU, "tolak_nama_dipakai", _ok)
    monkeypatch.setattr(ve.VU, "tolak_kode_dipakai", _ok)
    conn = Conn(pihak=dict(BARIS_VENDOR))
    await ve._siapkan_ubah_vendor(conn, TENANT, ID, ve.UpdateVendorRequest(name="Baru", code="V-9"))
    kunci = [x[2][0] for x in conn.log if x[0] == "execute" and "advisory" in x[1]]
    assert kunci == [f"VENDOR_NAMA:{TENANT}:baru", f"VENDOR_KODE:{TENANT}:v-9"]


@pytest.mark.asyncio
async def test_ubah_vendor_memakai_penentu_yang_sama(monkeypatch):
    async def _siap(conn, tid, vid, body, kunci=True):
        assert kunci is True
        raise HTTPException(status_code=409, detail="SENTINEL")
    monkeypatch.setattr(ve, "_siapkan_ubah_vendor", _siap)
    pasang(monkeypatch, ve, Conn())
    with pytest.raises(HTTPException) as e:
        await ve.update_vendor(req(), ID, ve.UpdateVendorRequest(notes="x"))
    assert e.value.detail == "SENTINEL"


# ---------------------------------------------------------------- izin rute

@pytest.mark.parametrize("jalur,metode,harap", [
    (f"/api/customers/{ID}/edit/preview", "POST", ("customer", "U")),
    (f"/api/customers/{ID}/delete/preview", "POST", ("customer", "D")),
    (f"/api/customers/{ID}/status", "PATCH", ("customer", "P")),
    (f"/api/customers/{ID}/status/preview", "POST", ("customer", "P")),
    (f"/api/customers/{ID}/reactivate", "PATCH", ("customer", "P")),
    ("/api/vendors/preview", "POST", ("supplier", "C")),
    (f"/api/vendors/{ID}/edit/preview", "POST", ("supplier", "U")),
    (f"/api/vendors/{ID}/delete/preview", "POST", ("supplier", "D")),
    (f"/api/vendors/{ID}/status/preview", "POST", ("supplier", "U")),
    (f"/api/vendors/{ID}/status", "PATCH", ("supplier", "U")),
    (f"/api/vendors/{ID}/rename-impact", "GET", ("supplier", "R")),
    ("/api/customers/merge/preview", "POST", ("customer", "C")),
    ("/api/customers/merge", "POST", ("customer", "C")),
    ("/api/vendors/merge/preview", "POST", ("supplier", "C")),
])
def test_izin_rute_ikut_huruf_tulisnya(jalur, metode, harap):
    from app.middleware.permission_middleware import PermissionMiddleware
    assert PermissionMiddleware(app=None)._find_permission(jalur, metode) == harap


# ---------------------------------------------------------------- penjaga hapus lengkap
# Diukur pg_constraint 9 Nov 2026 (confdeltype = NO ACTION ke customers / vendors) + dokumen ber-vendor_id TANPA FK (purchase_orders,
# expenses). Tabel yang terlewat dulu: DELETE 500 (FK sales_orders, terbukti di salinan) atau dokumen yatim. Tabel CASCADE (aktivitas,
# alamat, kontak) memang ikut terhapus.
FK_PELANGGAN = {"accounts_receivable", "credit_notes", "customer_deposits", "proformas", "quotes", "receive_payments", "sales_invoices",
                "sales_orders", "sales_receipts"}
FK_VENDOR = {"asset_maintenance", "bill_payments_v2", "bom_operations", "cheques", "fixed_assets", "goods_receipts",
             "production_subcontracts", "recurring_bills", "vendor_deposits", "purchase_orders", "expenses", "bills", "vendor_credits"}


def test_jejak_hapus_lengkap():
    assert FK_PELANGGAN <= {x[0] for x in cu.JEJAK_PELANGGAN}
    assert FK_VENDOR <= {x[0] for x in ve.JEJAK_VENDOR}
    for x in [*cu.JEJAK_PELANGGAN, *ve.JEJAK_VENDOR]:
        assert x[2] and x[2].startswith("Tidak bisa menghapus")


# ---------------------------------------------------------------- perubahan[].lama untuk SEMUA medan PATCH (temuan WORKSPACE)

@pytest.mark.asyncio
@pytest.mark.parametrize("mod", [cu, ve])
async def test_baris_sekarang_memuat_semua_kolom_patch(mod, monkeypatch):
    conn = Conn(pihak={"id": ID, "nama": "A", "name": "A", "code": "V-1", "vendor_type": "BADAN", "jenis_id": None, "nomor_dokumen": None})
    if mod is cu:
        _tolak_lolos(monkeypatch, cu)
        await cu._siapkan_ubah_pelanggan(conn, TENANT, ID, cu.UpdateCustomerRequest())
        medan, kolom = cu.UpdateCustomerRequest.model_fields, lambda f: cu.UBAH_KOLOM.get(f, f)
    else:
        await ve._siapkan_ubah_vendor(conn, TENANT, ID, ve.UpdateVendorRequest(), kunci=False)
        medan, kolom = {f: 0 for f in ve.UpdateVendorRequest.model_fields if f not in ("shipping_address", "contacts", "contacts_replace")}, \
            lambda f: f
    sql = [x[1] for x in conn.log if x[0] == "fetchrow"][0]
    pilih = {c.strip() for c in sql.split("SELECT", 1)[1].split("FROM", 1)[0].split(",")}
    assert {kolom(f) for f in medan} <= pilih, {kolom(f) for f in medan} - pilih


def test_uuid_kolom_sama_dengan_teks_badan():
    u = uuid.UUID("33333333-3333-3333-3333-333333333333")
    assert cu.perubahan_pelanggan({"price_list_id": u}, {"price_list_id": str(u)}) == []
    assert cu.perubahan_pelanggan({"nomor_member": "C1"}, {"code": "C9"})[0]["lama"] == "C1"
    assert ve.perubahan_vendor({"bank_name": "BCA"}, {"bank_name": "BRI"})[0]["lama"] == "BCA"


# ---------------------------------------------------------------- K-D gabung pelanggan simetri vendor

def test_badan_gabung_baru_dan_lama():
    a, b = str(ID), "22222222-2222-2222-2222-222222222222"
    assert cu._badan_gabung({"source_customer_id": b, "target_customer_id": a}) == {"target_id": a, "source_ids": [b]}
    lama = {"target_id": a, "source_ids": [b]}
    assert cu._badan_gabung(lama) is lama


def test_ringkas_pindah_semua_tabel_gabung_terpetakan():
    tabel = {t for t, _ in cu.MERGE_TABEL} | {t for t, *_ in ve.GABUNG_TABEL} | {"customer_addresses"}
    assert tabel <= set(KDT.PINDAH_TEKS)
    assert KDT.ringkas_pindah({"sales_invoices": 3, "receive_payments": 1, "quotes": 0, "chat_session_state": 2}) == [
        "3 faktur penjualan", "1 pembayaran masuk"]


class _ReqJson(SimpleNamespace):
    async def json(self):
        return self.badan


def req_json(badan):
    return _ReqJson(badan=badan, headers={}, state=SimpleNamespace(user={"tenant_id": TENANT, "user_id": "22222222-2222-2222-2222-222222222222"}))


KONFLIK = [{"type": "sales_receipt_locked", "count": 1, "blocking": True, "message": "1 kwitansi penjualan sudah selesai/dibatalkan."}]


def _gabung_palsu(monkeypatch, konflik):
    S = uuid.UUID("22222222-2222-2222-2222-222222222222")
    peta = {ID: {"nama": "Tujuan", "nomor_member": "C-1"}, S: {"nama": "Sumber", "nomor_member": "C-2"}}

    async def _pihak(conn, tid, body):
        assert cu._badan_gabung(body)["source_ids"] == [str(S)]
        return ID, [S], peta

    async def _rinci(conn, tid, sasaran, sumber):
        return {"sales_invoices": 2, "customer_addresses": 1}, konflik, len(konflik)
    monkeypatch.setattr(cu, "_gabung_pihak", _pihak)
    monkeypatch.setattr(cu, "_gabung_rincian", _rinci)
    return S


@pytest.mark.asyncio
async def test_pratinjau_gabung_pelanggan_bentuk_vendor_aditif(monkeypatch):
    S = _gabung_palsu(monkeypatch, KONFLIK)
    pasang(monkeypatch, cu, Conn())
    r = await cu.preview_customer_merge(req_json({"source_customer_id": str(S), "target_customer_id": str(ID)}))
    d = r["data"]
    assert d["ok"] is False and d["blocks"] == [{"code": "SALES_RECEIPT_LOCKED", "message": KONFLIK[0]["message"]}]
    assert d["source"] == {"id": str(S), "name": "Sumber", "code": "C-2"} and d["target"]["name"] == "Tujuan"
    assert d["records_to_move"] == {"sales_invoices": 2, "customer_addresses": 1} and d["irreversible"] is True
    assert d["rencana"]["ringkas"] == ["2 faktur penjualan", "1 alamat"]
    assert r["preview"]["conflicts"] == KONFLIK and r["preview"]["records_to_move"]["invoices"] == 2  # bentuk lama tetap


@pytest.mark.asyncio
async def test_gabung_pelanggan_blok_409_dan_idem(monkeypatch):
    S = _gabung_palsu(monkeypatch, KONFLIK)
    pasang(monkeypatch, cu, Conn())
    with pytest.raises(HTTPException) as e:
        await cu.merge_customers(req_json({"target_id": str(ID), "source_ids": [str(S)]}))
    assert e.value.status_code == 409 and e.value.detail == {"code": "SALES_RECEIPT_LOCKED", "message": KONFLIK[0]["message"]}

    from app.services import idem_buat
    panggil = []

    async def _mulai(conn, ctx, kunci, prefix, doc, isi, response=None):
        panggil.append((prefix, doc))
        return "k", "s", {"replay": True}
    monkeypatch.setattr(idem_buat, "mulai_aksi", _mulai)
    r = await cu.merge_customers(req_json({"source_customer_id": str(S), "target_customer_id": str(ID)}))
    assert r == {"replay": True} and panggil == [("CUSTOMER_MERGE", str(ID))]


@pytest.mark.asyncio
async def test_pratinjau_gabung_vendor_ringkas(monkeypatch):
    S = uuid.UUID("22222222-2222-2222-2222-222222222222")

    async def _rencana(conn, tid, sumber, tujuan):
        return {"peta": {S: {"id": S, "name": "A", "code": "V-1"}, ID: {"id": ID, "name": "B", "code": "V-2"}},
                "cacah": {"bills": 4, "bill_payments_v2": 0, "purchase_orders": 1}, "blocks": []}
    monkeypatch.setattr(ve, "_gabung_rencana", _rencana)
    pasang(monkeypatch, ve, Conn())
    d = (await ve.preview_merge_vendors(req(), ve.MergeVendorRequest(source_vendor_id=str(S), target_vendor_id=str(ID))))["data"]
    assert d["rencana"]["ringkas"] == ["4 faktur pembelian", "1 pesanan pembelian"] and d["ok"] is True
