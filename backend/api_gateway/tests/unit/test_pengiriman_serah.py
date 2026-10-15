"""11 Okt 2026 (kontrak pengiriman serah, disetujui MASTER): cara serah / alamat kirim / resi BANYAK / lampiran Surat Jalan.

Penjaga: (1) aturan jenis x resi x kirim_ke + bawaan; (2) lokasi bawaan -- fase menang atas gudang SO HANYA bila flag ON,
sumber diberi teks, tanpa flag kamus lama PERSIS; (3) NOL BUKU: modul serah tak menyentuh jurnal/stok/HPP/UPDATE ke
invoice_fulfillments, WAC per barang (bukan per gudang) sehingga pilihan gudang tak menggeser HPP; (4) tanpa flag: field
serah -> 409, respons tanpa kunci serah, hash idempotensi klien lama tak berubah; (5) PUT serah: voided 409, audit,
periode tertutup boleh; (6) rute punya pola izin; (7) cetakan SJ."""
import asyncio
import inspect
import re
import uuid
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routers import deliveries as D
from app.routers import delivery_lampiran as DL
from app.routers import metode_serah as MS
from app.services import pengiriman_serah as S
from app.services import so_pengiriman as P

T = "t-uji"
U = str(uuid.uuid4())
APP = Path(S.__file__).resolve().parent.parent
M_KURIR = {"id": uuid.uuid4(), "nama": "JNE", "jenis": "kurir", "aktif": True, "urutan": 0}
M_AMBIL = {"id": uuid.uuid4(), "nama": "Ambil di Toko Manado", "jenis": "ambil_sendiri", "aktif": True, "urutan": 1}
M_ANTAR = {"id": uuid.uuid4(), "nama": "Antar sendiri", "jenis": "antar_sendiri", "aktif": True, "urutan": 2}
M_MATI = {"id": uuid.uuid4(), "nama": "Lama", "jenis": "kurir", "aktif": False, "urutan": 3}
SEMUA = {str(m["id"]): m for m in (M_KURIR, M_AMBIL, M_ANTAR, M_MATI)}


def jalan(c):
    return asyncio.run(c)


class Konek:
    """Stub: dijawab menurut isi SQL; semua SQL dicatat."""

    def __init__(self, flag=True, so=None, pref_metode=None, pref_lokasi=None, alamat_cust=None, sj=None,
                 gudang=None):
        self.flag, self.so, self.pref_metode, self.pref_lokasi = flag, so, pref_metode, pref_lokasi
        self.alamat_cust, self.sj, self.gudang = alamat_cust, sj, gudang or {}
        self.log = []

    def _s(self, sql):
        s = " ".join(sql.split())
        self.log.append(s)
        return s

    async def fetchval(self, sql, *a):
        s = self._s(sql)
        if "FROM tenant_features" in s:
            return 1 if self.flag else None
        if "SELECT alamat FROM customers" in s:
            return self.alamat_cust
        return None

    async def fetchrow(self, sql, *a):
        s = self._s(sql)
        if "FROM metode_serah" in s:
            m = SEMUA.get(str(a[0]))
            return dict(m) if m else None
        if "FROM pengiriman_preferensi" in s:
            return {"default_lokasi_kirim_id": self.pref_lokasi, "default_metode_serah_id": self.pref_metode}
        if "SELECT shipping_address, customer_id FROM sales_orders" in s:
            return self.so
        if "SELECT id, order_number, status, warehouse_id" in s:
            return self.so
        if "FROM warehouses" in s:
            return self.gudang.get(str(a[1]))
        if "FROM invoice_fulfillments f" in s:
            return self.sj
        return None

    async def fetch(self, sql, *a):
        s = self._s(sql)
        if "FROM warehouses" in s:  # daftar aktif
            return [g for g in self.gudang.values() if g["is_active"]]
        return []

    async def execute(self, sql, *a):
        self._s(sql)

    def tulis(self):
        return [s for s in self.log if s.startswith(("INSERT", "UPDATE", "DELETE"))]


def _sel(k, body=None, so_id=None, **kw):
    return jalan(S.selesaikan(k, T, so_id, body, **kw))


def _galat(k, body, **kw):
    with pytest.raises(HTTPException) as e:
        _sel(k, body, **kw)
    assert e.value.status_code == 422 and e.value.detail["code"] == "SERAH_TIDAK_SAH"
    return e.value.detail["message"]


# ---------------- (1) aturan ----------------
def test_kurir_dengan_banyak_resi_dan_alamat_manual():
    k = Konek()
    s = _sel(k, S.SerahIn(metode_serah_id=str(M_KURIR["id"]), kirim_ke="  Jl. Sam Ratulangi 1  ",
                          resi=[S.ResiIn(nomor=" JNE001 "), S.ResiIn(nomor="JNE002", kurir="J&T")]))
    assert s["metode"] == {"id": str(M_KURIR["id"]), "nama": "JNE", "jenis": "kurir"}
    assert s["kirim_ke"] == "Jl. Sam Ratulangi 1" and s["kirim_ke_sumber"] == "manual"
    assert s["resi"] == [{"nomor": "JNE001", "kurir": None}, {"nomor": "JNE002", "kurir": "J&T"}]


def test_ambil_sendiri_menolak_alamat_dan_resi_dan_mengosongkan_kirim_ke():
    k = Konek(so={"shipping_address": "Jl. Lain 5", "customer_id": None})
    assert "alamat kirim" in _galat(k, S.SerahIn(metode_serah_id=str(M_AMBIL["id"]), kirim_ke="Jl. X"))
    assert "kurir" in _galat(k, S.SerahIn(metode_serah_id=str(M_AMBIL["id"]), resi=[S.ResiIn(nomor="A1")]))
    s = _sel(k, S.SerahIn(metode_serah_id=str(M_AMBIL["id"])), so_id=uuid.uuid4())
    assert s["kirim_ke"] is None and s["kirim_ke_sumber"] is None  # bawaan pesanan TIDAK dipakai bila ambil_sendiri


def test_antar_sendiri_tanpa_resi_tetap_boleh_beralamat():
    k = Konek()
    _galat(k, S.SerahIn(metode_serah_id=str(M_ANTAR["id"]), resi=[S.ResiIn(nomor="A1")]))
    s = _sel(k, S.SerahIn(metode_serah_id=str(M_ANTAR["id"]), kirim_ke="Jl. Y 2"))
    assert s["kirim_ke"] == "Jl. Y 2" and s["resi"] == []


def test_resi_tanpa_cara_serah_ditolak_dan_dobel_kosong_ditolak():
    k = Konek()
    assert "cara serah kurir" in _galat(k, S.SerahIn(resi=[S.ResiIn(nomor="A1")]))
    kur = str(M_KURIR["id"])
    assert "dobel" in _galat(k, S.SerahIn(metode_serah_id=kur, resi=[S.ResiIn(nomor="ab1"), S.ResiIn(nomor="AB1")]))
    assert "kosong" in _galat(k, S.SerahIn(metode_serah_id=kur, resi=[S.ResiIn(nomor="  ")]))


def test_resi_lebih_dari_20_ditolak_skema():
    with pytest.raises(Exception):
        S.SerahIn(resi=[S.ResiIn(nomor=f"R{i}") for i in range(21)])


def test_cara_serah_tak_dikenal_atau_nonaktif_ditolak():
    k = Konek()
    assert "tidak dikenal" in _galat(k, S.SerahIn(metode_serah_id=str(uuid.uuid4())))
    assert "tidak aktif" in _galat(k, S.SerahIn(metode_serah_id=str(M_MATI["id"])))
    assert "tidak dikenal" in _galat(k, S.SerahIn(metode_serah_id="bukan-uuid"))  # bukan 500


def test_alamat_bawaan_pesanan_lalu_pelanggan():
    so_id = uuid.uuid4()
    k = Konek(so={"shipping_address": "Jl. Pesanan 9", "customer_id": uuid.uuid4()}, alamat_cust="Jl. Pelanggan 1")
    s = _sel(k, S.SerahIn(metode_serah_id=str(M_KURIR["id"])), so_id=so_id)
    assert (s["kirim_ke"], s["kirim_ke_sumber"]) == ("Jl. Pesanan 9", "pesanan")
    k2 = Konek(so={"shipping_address": "  ", "customer_id": uuid.uuid4()}, alamat_cust="Jl. Pelanggan 1")
    s2 = _sel(k2, S.SerahIn(metode_serah_id=str(M_KURIR["id"])), so_id=so_id)
    assert (s2["kirim_ke"], s2["kirim_ke_sumber"]) == ("Jl. Pelanggan 1", "pelanggan")
    k3 = Konek(so={"shipping_address": None, "customer_id": None})
    assert _sel(k3, S.SerahIn(metode_serah_id=str(M_KURIR["id"])), so_id=so_id)["kirim_ke"] is None


def test_cara_serah_bawaan_tenant_dan_PUT_tanpa_bawaan():
    k = Konek(pref_metode=M_AMBIL["id"])
    assert _sel(k, None)["metode"]["nama"] == "Ambil di Toko Manado"
    assert _sel(k, None, pakai_bawaan=False)["metode"] is None  # PUT ganti-seluruhnya: kosong = tanpa cara serah
    k2 = Konek(pref_metode=M_MATI["id"])  # bawaan nonaktif -> dilewati, BUKAN galat
    assert _sel(k2, None)["metode"] is None


# ---------------- (2) lokasi bawaan ----------------
W_FASE, W_SO, W_PREF, W_DEF = (uuid.uuid4() for _ in range(4))


def _wh(i, nama, aktif=True, default=False):
    return str(i), {"id": i, "name": nama, "is_active": aktif, "is_default": default}


def _gud(**mati):
    return dict([_wh(W_FASE, "Toko Manado", aktif=mati.get("fase", True)), _wh(W_SO, "Gudang SO"),
                 _wh(W_PREF, "Gudang Pref"), _wh(W_DEF, "Gudang Utama", default=True)])


SO_FASE = {"id": uuid.uuid4(), "warehouse_id": W_SO, "fase_lokasi": "tersedia", "fase_gudang_id": W_FASE}


def _g(k, so, flag, pref=None, diminta=None):
    return jalan(P._gudang(k, T, diminta, dict(so), flag, pref))


def test_fase_menang_atas_gudang_so_hanya_bila_flag_on_dengan_teks_sumber():
    k = Konek(gudang=_gud())
    g, blok = _g(k, SO_FASE, True)
    assert blok is None and g["id"] == str(W_FASE) and g["source"] == "fase"
    assert g["source_label"] == "dari status pesanan: Tersedia di Toko Manado"
    # KONTROL: flag OFF -> fase DIABAIKAN, gudang SO, kamus LAMA persis (tanpa kunci baru)
    g0, _ = _g(Konek(gudang=_gud()), SO_FASE, False)
    assert g0 == {"id": str(W_SO), "name": "Gudang SO", "source": "so"}


def test_badan_menang_atas_fase_dan_fase_nonaktif_diblok_bukan_jatuh_diam():
    g, _ = _g(Konek(gudang=_gud()), SO_FASE, True, diminta=str(W_DEF))
    assert g["source"] == "body" and g["id"] == str(W_DEF)
    g2, blok = _g(Konek(gudang=_gud(fase=False)), SO_FASE, True)
    assert g2 is None and blok["code"] == "SO_FULFILL_WAREHOUSE_INACTIVE" and "status pesanan" in blok["message"]


def test_urutan_sisanya_so_lalu_preferensi_lalu_bawaan_tenant():
    so = {"id": uuid.uuid4(), "warehouse_id": None, "fase_lokasi": None, "fase_gudang_id": None}
    g, _ = _g(Konek(gudang=_gud()), so, True, pref=W_PREF)
    assert g["source"] == "tenant_setting" and g["id"] == str(W_PREF) and "pengaturan pengiriman" in g["source_label"]
    g2, _ = _g(Konek(gudang=_gud()), so, False, pref=W_PREF)  # flag OFF: preferensi tak dipakai
    assert g2["source"] == "tenant_default" and g2["id"] == str(W_DEF) and "source_label" not in g2
    g3, _ = _g(Konek(gudang=_gud()), {**so, "warehouse_id": W_SO}, True, pref=W_PREF)
    assert g3["source"] == "so"  # gudang SO menang atas preferensi


# ---------------- (3) NOL BUKU ----------------
def test_modul_serah_tak_menyentuh_buku_atau_surat_jalan():
    import ast
    pohon = ast.parse(inspect.getsource(S))
    for n in ast.walk(pohon):  # buang docstring (boleh menyebut buku untuk menjelaskan batasnya); sisanya = KODE
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.body \
                and isinstance(n.body[0], ast.Expr) and isinstance(getattr(n.body[0], "value", None), ast.Constant):
            n.body = n.body[1:] or [ast.Pass()]
    kode = ast.unparse(pohon).lower()
    for terlarang in ("journal_entries", "journal_lines", "inventory_ledger", "warehouse_stock", "get_weighted_average_cost",
                      "update invoice_fulfillments", "insert into invoice_fulfillments"):
        assert terlarang not in kode, terlarang
    k = Konek()
    s = _sel(k, S.SerahIn(metode_serah_id=str(M_KURIR["id"]), resi=[S.ResiIn(nomor="A1")]))
    k.log.clear()
    jalan(S.tulis(k, T, U, uuid.uuid4(), s))
    tabel = {re.search(r"(?:INSERT INTO|UPDATE|DELETE FROM)\s+(\w+)", q).group(1) for q in k.tulis()}
    assert tabel == {"pengiriman_serah", "pengiriman_resi"}


def test_wac_per_barang_bukan_per_gudang_jadi_pilihan_gudang_tak_menggeser_hpp():
    """HPP = get_weighted_average_cost(tenant, produk): argumen TANPA gudang, di rencana MAUPUN inti penulis."""
    plan = inspect.getsource(P)
    assert re.search(r"get_weighted_average_cost\(\$1, \$2\)\", tid, pid\)", plan)
    inti = (APP / "routers" / "sales_invoices.py").read_text()
    pakai = re.findall(r"get_weighted_average_cost\(([^)]*)\)", inti)
    assert pakai and all("warehouse" not in x.lower() for x in pakai), pakai


def test_tulis_kirim_so_menulis_serah_per_sj_dan_hanya_bila_ada(monkeypatch):
    import app.routers.sales_invoices as SI
    dipanggil = []

    async def inti(conn, tid, uid, f, items, wh, tgl, **k):
        return {"fulfillment_id": f"F-{f['id']}", "fulfillment_number": "SJ-1", "total_cogs": "0", "total_revenue": "0"}

    async def tulis(conn, tid, uid, fid, s):
        dipanggil.append(fid)

    monkeypatch.setattr(SI, "_execute_fulfillment", inti)
    monkeypatch.setattr(S, "tulis", tulis)
    faktur = [{"id": "a"}, {"id": "b"}]
    r = {"faktur": faktur, "kelompok": {"a": [{"b": {"id": 1}, "qty": 1}], "b": [{"b": {"id": 2}, "qty": 1}]},
         "warehouse": {"id": str(uuid.uuid4())}, "fulfillment_date": date(2026, 10, 11), "payload": None}
    ctx = {"tenant_id": T, "user_id": U}
    jalan(P.tulis_kirim_so(None, ctx, {**r, "serah": {"metode": None}}))
    assert dipanggil == ["F-a", "F-b"]  # satu per Surat Jalan, di transaksi pemanggil
    dipanggil.clear()
    jalan(P.tulis_kirim_so(None, ctx, {**r, "serah": None}))
    assert dipanggil == []  # KONTROL: tanpa serah (flag off) nol tulisan serah


# ---------------- (4) tanpa flag ----------------
def test_serah_tanpa_flag_409_bukan_diabaikan_diam():
    k = Konek(flag=False, so={"id": uuid.uuid4(), "order_number": "SO-1", "status": "confirmed", "warehouse_id": None,
                              "fase_lokasi": None, "fase_gudang_id": None})
    with pytest.raises(HTTPException) as e:
        jalan(P.rencana_kirim_so(k, {"tenant_id": T, "user_id": U}, uuid.uuid4(),
                                 P.SOFulfillRequest(serah=S.SerahIn(kirim_ke="Jl. A"))))
    assert e.value.status_code == 409 and e.value.detail["code"] == "SERAH_NONAKTIF"


def test_hash_idempotensi_klien_lama_tak_berubah():
    """Klien lama (tanpa serah) harus menghasilkan kamus yang SAMA dengan sebelum unit ini -> retry lintas rilis tak 409."""
    lama = {"items": None, "warehouse_id": None, "fulfillment_date": None, "notes": None}
    assert P.SOFulfillRequest().model_dump(mode="json", exclude={"serah"}) == lama
    sumber = (APP / "routers" / "sales_orders.py").read_text()
    assert 'exclude=None if body.serah is not None else {"serah"}' in sumber


def test_respons_tanpa_flag_tak_membawa_kunci_serah():
    r = {"so": {"order_number": "SO-1"}, "blocks": [], "notes": [], "warehouse": None, "fulfillment_date": date(2026, 10, 11),
         "lines": [], "faktur": [], "kelompok": {}, "payload": None}
    assert "serah" not in P.bentuk({**r, "serah": None, "serah_aktif": False}, None, None)
    dengan = P.bentuk({**r, "serah": {"metode": None, "kirim_ke": None, "kirim_ke_sumber": None, "resi": [],
                                      "diterima_pada": None}, "serah_aktif": True}, None, None)
    assert dengan["serah"]["resi"] == []  # KONTROL: dengan flag, kunci ada


# ---------------- (5) PUT serah ----------------
class _Tx:
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False


class _KonekPut(Konek):
    def transaction(self): return _Tx()

    async def fetch(self, sql, *a):
        s = self._s(sql)
        return []


class _Pool:
    def __init__(self, c): self.c = c
    def acquire(self): return self
    async def __aenter__(self): return self.c
    async def __aexit__(self, *a): return False


def _put(monkeypatch, k, body, fid=None):
    async def gp(): return _Pool(k)
    monkeypatch.setattr(D, "get_pool", gp)
    req = SimpleNamespace(state=SimpleNamespace(user={"tenant_id": T, "user_id": U}))
    return jalan(D.ubah_serah_surat_jalan(str(fid or uuid.uuid4()), req, body))


SJ = {"id": uuid.uuid4(), "fulfillment_number": "SJ-2610-0001", "status": "posted", "sales_order_id": uuid.uuid4()}


def test_put_serah_menulis_serah_resi_audit_tanpa_menyentuh_surat_jalan(monkeypatch):
    k = _KonekPut(sj=SJ, so={"shipping_address": None, "customer_id": None})
    out = _put(monkeypatch, k, S.SerahIn(metode_serah_id=str(M_KURIR["id"]), kirim_ke="Jl. Z 3",
                                         resi=[S.ResiIn(nomor="R1"), S.ResiIn(nomor="R2")],
                                         diterima_pada=date(2026, 10, 12)))
    assert out["data"]["resi"] == [{"nomor": "R1", "kurir": None}, {"nomor": "R2", "kurir": None}]
    assert out["data"]["diterima_pada"] == "2026-10-12"
    tabel = {re.search(r"(?:INSERT INTO|UPDATE|DELETE FROM)\s+(\"?\w+\"?)", q).group(1) for q in k.tulis()}
    assert tabel == {"pengiriman_serah", "pengiriman_resi", "audit_logs"}, tabel  # NOL UPDATE invoice_fulfillments
    assert any("pg_advisory_xact_lock" in q for q in k.log)  # Law 13


def test_put_serah_sj_voided_409_dan_tanpa_flag_409_dan_tak_ada_404(monkeypatch):
    with pytest.raises(HTTPException) as e:
        _put(monkeypatch, _KonekPut(sj={**SJ, "status": "voided"}), S.SerahIn())
    assert e.value.status_code == 409 and e.value.detail["code"] == "SJ_VOID"
    with pytest.raises(HTTPException) as e:
        _put(monkeypatch, _KonekPut(flag=False, sj=SJ), S.SerahIn())
    assert e.value.status_code == 409 and e.value.detail["code"] == "SERAH_NONAKTIF"
    with pytest.raises(HTTPException) as e:
        _put(monkeypatch, _KonekPut(sj=None), S.SerahIn())
    assert e.value.status_code == 404
    with pytest.raises(HTTPException) as e:  # id bukan UUID -> 404, bukan 500
        jalan(D.ubah_serah_surat_jalan("bukan-uuid", SimpleNamespace(state=SimpleNamespace(
            user={"tenant_id": T, "user_id": U})), S.SerahIn()))
    assert e.value.status_code == 404


def test_put_serah_tak_mengunci_periode():
    src = inspect.getsource(D.ubah_serah_surat_jalan)
    assert "check_period_is_open" not in src and "fiscal_periods" not in src  # K4: periode tertutup BOLEH


# ---------------- master cara serah ----------------
def test_metode_jenis_tak_sah_422_dan_nama_ganda_409(monkeypatch):
    req = SimpleNamespace(state=SimpleNamespace(user={"tenant_id": T, "user_id": U}))
    with pytest.raises(HTTPException) as e:
        jalan(MS.tambah_metode(req, MS.MetodeBaru(nama="X", jenis="drone")))
    assert e.value.status_code == 422 and e.value.detail["code"] == "METODE_SERAH_JENIS"
    import asyncpg

    class K(Konek):
        async def fetchrow(self, sql, *a):
            self._s(sql)
            raise asyncpg.UniqueViolationError("dup")

    async def gp(): return _Pool(K())
    monkeypatch.setattr(MS, "get_pool", gp)
    with pytest.raises(HTTPException) as e:
        jalan(MS.tambah_metode(req, MS.MetodeBaru(nama="JNE", jenis="kurir")))
    assert e.value.status_code == 409 and e.value.detail["code"] == "METODE_SERAH_NAMA_DIPAKAI"


def test_metode_jenis_terkunci_bila_sudah_dipakai_surat_jalan(monkeypatch):
    req = SimpleNamespace(state=SimpleNamespace(user={"tenant_id": T, "user_id": U}))

    class K(_KonekPut):
        async def fetchrow(self, sql, *a):
            self._s(sql)
            return dict(M_KURIR)

        async def fetchval(self, sql, *a):
            s = self._s(sql)
            return 1  # flag ON dan "dipakai" = 1

    async def gp(): return _Pool(K())
    monkeypatch.setattr(MS, "get_pool", gp)
    with pytest.raises(HTTPException) as e:
        jalan(MS.ubah_metode(req, M_KURIR["id"], MS.MetodeUbah(jenis="ambil_sendiri")))
    assert e.value.status_code == 409 and e.value.detail["code"] == "METODE_SERAH_SUDAH_DIPAKAI"


# ---------------- (6) izin rute + lampiran ----------------
def _pola():
    teks = (APP / "middleware" / "permission_middleware.py").read_text()
    blok = re.search(r"ROUTE_PERMISSIONS[^=]*=\s*\[(.*?)\n\]", teks, re.S).group(1)
    return [(re.compile(p), [x.strip().strip('"') for x in m.split(",")], mod, act)
            for p, m, mod, act in re.findall(r'\(r"([^"]+)",\s*\[([^\]]*)\],\s*"([^"]+)",\s*"([^"]+)"\)', blok)]


@pytest.mark.parametrize("metode,path,modul,aksi", [
    ("PUT", "/api/deliveries/abc/serah", "sales_invoice", "U"),
    ("POST", "/api/deliveries/abc/attachments", "sales_invoice", "U"),
    ("GET", "/api/deliveries/abc/attachments", "sales_invoice", "R"),
    ("GET", "/api/deliveries/abc/attachments/d1/download", "sales_invoice", "R"),
    ("DELETE", "/api/deliveries/abc/attachments/d1", "sales_invoice", "D"),
    ("GET", "/api/metode-serah", "warehouse", "R"),
    ("POST", "/api/metode-serah", "warehouse", "C"),
    ("PATCH", "/api/metode-serah/x", "warehouse", "U"),
    ("GET", "/api/pengiriman/preferensi", "warehouse", "R"),
    ("PUT", "/api/pengiriman/preferensi", "warehouse", "U"),
    ("GET", "/api/default-dokumen/pengiriman", "sales_order", "R"),
])
def test_rute_baru_punya_pola_izin(metode, path, modul, aksi):
    cocok = [(mod, act) for rx, ms, mod, act in _pola() if rx.match(path) and metode in ms]
    assert cocok and cocok[0] == (modul, aksi), cocok


def test_lampiran_sj_entitas_delivery_flag_di_tiap_handler_dan_tanpa_jejak_so():
    # LAMPIRAN-SO-SJ-PABRIK (15 Okt 2026): handler = pabrik lampiran_induk; spesifikasi SJ memegang flag + audit
    src = inspect.getsource(DL)
    assert "'sales_order'" not in src and "sales_orders" not in src
    assert '_ENTITAS = "delivery"' in src and "invoice_fulfillments" in src
    sp = DL.SPEC_LAMPIRAN_SJ
    assert sp.entitas == "delivery" and sp.tabel == "invoice_fulfillments" and sp.pra_cek is DL._pra_cek
    assert "pastikan_aktif" in inspect.getsource(DL._pra_cek)
    from app.routers import lampiran_induk as LI
    pabrik = inspect.getsource(LI.buat_router)
    assert pabrik.count("await _cek(conn, tenant_id)") >= 2 and "await _induk(conn, doc_id, tenant_id)" in pabrik  # flag tiap rute
    assert sp.event_lepas == "SJ_LAMPIRAN_DILEPAS" and "catat_riwayat" in pabrik  # lepas tautan = audit


def test_lampiran_sj_tanpa_flag_409(monkeypatch):
    class K(_KonekPut):
        pass

    async def gp(): return _Pool(K(flag=False))
    monkeypatch.setattr(DL, "get_pool", gp)
    req = SimpleNamespace(state=SimpleNamespace(user={"tenant_id": T, "user_id": U}))
    with pytest.raises(HTTPException) as e:
        jalan(DL.daftar_lampiran_sj(req, uuid.uuid4()))
    assert e.value.status_code == 409 and e.value.detail["code"] == "SERAH_NONAKTIF"


# ---------------- (7) cetakan ----------------
def _render(serah):
    import jinja2
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(APP / "templates" / "pdf")), autoescape=True)
    env.filters["date_id"] = lambda v: str(v)
    from app.services.pdf_service import format_qty
    env.filters["qty"] = format_qty  # 14 Okt 2026: sel qty SJ memakai filter `qty` (format_qty)
    for nama in ("rupiah", "idr", "angka", "fmt_qty", "terbilang", "tgl"):
        env.filters.setdefault(nama, lambda v, *a, **k: str(v))
    d = {"delivery_number": "SJ-1", "delivery_date": "2026-10-11", "invoice_number": "INV-1", "customer_name": "Budi",
         "customer_address": "Jl. Pelanggan 1", "customer_phone": None, "tenant": {"name": "Kaos"}, "notes": None}
    if serah is not None:
        d["serah"] = serah
    return env.get_template("delivery_note.html").render(delivery=d, delivery_items=[], tenant=d["tenant"], generated_at=datetime(2026, 10, 11, 9, 0))


def test_cetak_sj_blok_serah_hanya_bila_ada_dan_tanpa_serah_tak_berubah():
    polos = _render(None)
    assert "No. Resi" not in polos and "Cara serah" not in polos
    kurir = _render({"metode": {"nama": "JNE", "jenis": "kurir"}, "kirim_ke": "Jl. Pesanan 9", "kirim_ke_sumber": "pesanan",
                     "resi": [{"nomor": "R1", "kurir": None}, {"nomor": "R2", "kurir": None}], "warehouse_name": "Toko Manado"})
    assert "Cara serah" in kurir and "JNE" in kurir and "Jl. Pesanan 9" in kurir and "R1, R2" in kurir
    ambil = _render({"metode": {"nama": "Ambil di Toko", "jenis": "ambil_sendiri"}, "kirim_ke": None, "kirim_ke_sumber": None,
                     "resi": [], "warehouse_name": "Toko Manado"})
    assert "Diambil sendiri di Toko Manado" in ambil and "Kirim ke" not in ambil
    dup = _render({"metode": {"nama": "JNE", "jenis": "kurir"}, "kirim_ke": "Jl. Pelanggan 1", "kirim_ke_sumber": "pelanggan",
                   "resi": [], "warehouse_name": None})
    assert "Kirim ke" not in dup  # alamat pelanggan sudah tercetak di baris Alamat -> tak digandakan
