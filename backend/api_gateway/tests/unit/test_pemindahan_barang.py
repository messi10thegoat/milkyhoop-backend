"""12 Okt 2026 (kontrak R2, putusan MASTER P3/P5/P6/P7): Pemindahan Barang antargudang, SATU LANGKAH, TANPA JURNAL.

Penjaga: (1) flag: tanpa `pemindahan_barang` semua rute TULIS 409 STOCK_TRANSFER_NOT_AVAILABLE; (2) validasi: gudang sama/nonaktif,
barang non-stok / batch / serial, baris ganda; (3) selesai = tepat 2 baris kartu per barang (keluar asal, masuk tujuan), nilai = WAC
(unit_cost = average_cost = WAC, WAC TIDAK dihitung ulang), Decimal 2dp, journal_id TIDAK diisi; (4) stok asal kurang / WAC nol ->
409 dan NOL baris tertulis; (5) idempoten; (6) void = pembalik kedua kaki, average_cost = WAC saat void, stok tujuan terpakai -> 409;
(7) NOL BUKU: modul tak menyentuh jurnal / UPDATE warehouse_stock / float / int(); (8) KOLOM HANTU: tiap INSERT ke inventory_ledger di
seluruh app memakai hanya kolom yang ADA di tabel; (9) dua langkah (ship/receive) 409; (10) rute punya pola izin; (11) fase SO
informatif: blok SO tak menggagalkan pemindahan."""
import ast
import asyncio
import re
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.routers import stock_transfers as R
from app.services import pemindahan_barang as S

T = "t-uji"
U = uuid.uuid4()
APP = Path(S.__file__).resolve().parent.parent
HARI = date(2026, 10, 12)
# Kolom inventory_ledger yang ADA (information_schema prod, 12 Okt 2026). Tambah kolom di DB -> tambahkan di sini.
KOLOM_LEDGER = set(
    "id,tenant_id,product_id,product_code,product_name,movement_type,movement_date,source_type,source_id,source_number,"
    "quantity_in,quantity_out,quantity_balance,unit_cost,total_cost,average_cost,storage_location_id,journal_id,created_at,"
    "created_by,notes,warehouse_id,batch_id,conversion_factor,transaction_quantity,transaction_unit".split(","))

GA, GB = uuid.uuid4(), uuid.uuid4()          # gudang asal / tujuan
P1, P2 = uuid.uuid4(), uuid.uuid4()


def jalan(c):
    return asyncio.run(c)


def barang(pid, nama="Kaos A", track=True, batch=False, serial=False):
    return {"id": pid, "item_code": f"K-{str(pid)[:4]}", "nama_produk": nama, "base_unit": "pcs",
            "track_inventory": track, "track_batches": batch, "track_serial": serial}


class Konek:
    """Stub: dijawab menurut isi SQL; tiap SQL dicatat; INSERT ledger di-parse ke self.ledger."""

    def __init__(self, flag=True, wac=Decimal("43332.95"), stok=None, saldo=Decimal("130"), produk=None,
                 header=None, items=None, asli=None, gudang_aktif=True):
        self.flag, self.wac, self.saldo = flag, wac, saldo
        self.stok = stok if stok is not None else {(GA, P1): Decimal("100"), (GB, P1): Decimal("30")}
        self.produk = produk or {P1: barang(P1)}
        self.header = header
        self.items = items or []
        self.asli = asli or []
        self.gudang_aktif = gudang_aktif
        self.log, self.ledger, self.updates = [], [], []

    def _s(self, sql):
        s = " ".join(sql.split())
        self.log.append(s)
        return s

    async def fetchval(self, sql, *a):
        s = self._s(sql)
        if "FROM tenant_features" in s:
            return 1 if self.flag else None
        if "generate_stock_transfer_number" in s:
            return "ST-2610-0001"
        if "get_weighted_average_cost" in s:
            return self.wac
        if "available_quantity FROM warehouse_stock" in s:
            return self.stok.get((a[1], a[2]), Decimal("0"))
        if "SUM(quantity_in - quantity_out)" in s:
            return self.saldo
        if s.startswith("INSERT INTO stock_transfers"):
            return U
        if "FROM sales_orders WHERE id" in s:
            return 1
        if "FROM inventory_ledger WHERE tenant_id = $1 AND source_type = $2 AND source_id = $3 LIMIT 1" in s:
            return None
        return None

    async def fetchrow(self, sql, *a):
        s = self._s(sql)
        if "FROM warehouses WHERE id" in s:
            return {"id": a[0], "name": "Gudang A" if a[0] == GA else "Gudang B", "is_active": self.gudang_aktif}
        if "FROM stock_transfers WHERE id" in s:
            return self.header
        if "FROM sales_orders WHERE id" in s:
            return {"id": a[0], "order_number": "SO-2610-0001", "fase_lokasi": "tersedia", "fase_gudang_id": GB}
        return None

    async def fetch(self, sql, *a):
        s = self._s(sql)
        if "FROM products" in s:
            return [self.produk[i] for i in a[1] if i in self.produk]
        if "FROM stock_transfer_items" in s:
            return self.items
        if "FROM inventory_ledger WHERE tenant_id = $1 AND source_type" in s:
            return self.asli
        if "FROM warehouses WHERE tenant_id" in s:
            return [{"id": GA, "name": "Gudang A"}, {"id": GB, "name": "Gudang B"}]
        return []

    async def execute(self, sql, *a):
        s = self._s(sql)
        if s.startswith("INSERT INTO inventory_ledger"):
            kol = re.search(r"\(([^)]*)\)\s*VALUES", s).group(1)
            nama = [k.strip() for k in kol.split(",")]
            self.ledger.append(dict(zip(nama, a)))
        elif s.startswith("UPDATE") or s.startswith("DELETE"):
            self.updates.append((s, a))
        return "OK"


@pytest.fixture(autouse=True)
def _pasang(monkeypatch):
    async def hari(conn, tid):
        return HARI
    monkeypatch.setattr(S, "tanggal_dokumen", hari)
    ev = []

    async def riwayat(conn, tenant_id, entity_type, entity_id, entity_number, event, user_id, ringkas, meta=None, source="api"):
        ev.append((event, entity_number))
    monkeypatch.setattr(S, "catat_riwayat", riwayat)
    fase = {"rencana": None, "terapkan": []}

    async def rencana(conn, ctx, so_id, f, g, kunci=False):
        return fase["rencana"] or {"blocks": [], "berubah": True, "order": None}

    async def terapkan(conn, ctx, so_id, f, g):
        fase["terapkan"].append((so_id, f, g))
        return {}
    monkeypatch.setattr(S.so_fase_lokasi, "rencana", rencana)
    monkeypatch.setattr(S.so_fase_lokasi, "terapkan", terapkan)
    return ev, fase


def ctx():
    return {"tenant_id": T, "user_id": U}


def draf(**kw):
    h = {"id": U, "tenant_id": T, "transfer_number": "ST-2610-0001", "status": "draft", "from_warehouse_id": GA,
         "to_warehouse_id": GB, "sales_order_id": None}
    h.update(kw)
    return h


def baris(pid=P1, qty="10", n=1):
    return {"id": uuid.uuid4(), "item_id": pid, "quantity_requested": Decimal(qty), "line_number": n, "notes": None}


def body(items=None, **kw):
    d = {"from_warehouse_id": GA, "to_warehouse_id": GB,
         "items": items or [{"item_id": P1, "quantity_requested": Decimal("10")}]}
    d.update(kw)
    return S.PemindahanIn(**d)


def kode(exc):
    return exc.value.detail["code"]


# ---- (1) flag
def test_tanpa_flag_409():
    with pytest.raises(HTTPException) as e:
        jalan(S.pastikan_aktif(Konek(flag=False), T))
    assert e.value.status_code == 409 and kode(e) == "STOCK_TRANSFER_NOT_AVAILABLE"
    jalan(S.pastikan_aktif(Konek(flag=True), T))


def test_dependency_rute_tulis_tanpa_flag(monkeypatch):
    class Req:
        class state:
            user = {"tenant_id": T, "user_id": str(U)}

    class Pool:
        def acquire(self):
            k = Konek(flag=False)

            class Cm:
                async def __aenter__(s):
                    return k

                async def __aexit__(s, *x):
                    return False
            return Cm()

    async def pool():
        return Pool()
    monkeypatch.setattr(R, "get_pool", pool)
    with pytest.raises(HTTPException) as e:
        jalan(R._butuh_flag(Req()))
    assert e.value.status_code == 409 and e.value.detail["code"] == "STOCK_TRANSFER_NOT_AVAILABLE"


def test_dua_langkah_selalu_409():
    with pytest.raises(HTTPException) as e:
        jalan(R._dua_langkah_belum())
    assert e.value.detail["code"] == "STOCK_TRANSFER_DUA_LANGKAH_BELUM_TERSEDIA"
    rute = {(r.path, tuple(sorted(r.methods))): r for r in R.router.routes}
    for p in ("/{transfer_id}/ship", "/{transfer_id}/receive"):
        deps = {d.dependency for d in rute[(p, ("POST",))].dependencies}
        assert R._dua_langkah_belum in deps and R._butuh_flag in deps


# ---- (2) validasi
def test_gudang_sama_ditolak_pydantic():
    with pytest.raises(ValueError):
        S.PemindahanIn(from_warehouse_id=GA, to_warehouse_id=GA, items=[{"item_id": P1, "quantity_requested": 1}])


def test_barang_non_stok_batch_serial_ditolak():
    for p, kd in ((barang(P1, track=False), "PB_BARANG_NON_STOK"), (barang(P1, batch=True), "PB_BARANG_BATCH_SERIAL"),
                  (barang(P1, serial=True), "PB_BARANG_BATCH_SERIAL")):
        c = Konek(produk={P1: p})
        with pytest.raises(HTTPException) as e:
            jalan(S.buat_draf(c, ctx(), body()))
        assert kode(e) == kd
        assert not any(s.startswith("INSERT INTO stock_transfers") for s in c.log)


def test_barang_ganda_dan_gudang_nonaktif():
    c = Konek()
    with pytest.raises(HTTPException) as e:
        jalan(S.buat_draf(c, ctx(), body(items=[{"item_id": P1, "quantity_requested": 1}, {"item_id": P1, "quantity_requested": 2}])))
    assert kode(e) == "PB_BARANG_GANDA" and e.value.status_code == 422
    with pytest.raises(HTTPException) as e:
        jalan(S.buat_draf(Konek(gudang_aktif=False), ctx(), body()))
    assert kode(e) == "PB_GUDANG_NONAKTIF"


def test_draf_nilai_klien_diabaikan():
    c = Konek()
    b = S.PemindahanIn(from_warehouse_id=GA, to_warehouse_id=GB,
                       items=[{"item_id": P1, "quantity_requested": 5, "unit_cost": 1, "item_name": "PALSU"}])
    jalan(S.buat_draf(c, ctx(), b))
    ins = [s for s in c.log if s.startswith("INSERT INTO stock_transfer_items")]
    assert len(ins) == 1 and "0, 0," in ins[0]          # unit_cost & total_value nol pada draf: server menetapkan saat selesai
    assert not c.ledger                                   # draf tak menyentuh kartu stok


# ---- (3)(4)(5) selesai
def selesai(c):
    return jalan(S.selesaikan(c, ctx(), U))


def test_selesai_dua_baris_kartu_nilai_wac():
    c = Konek(header=draf(), items=[baris(P1, "10")], saldo=Decimal("130"))
    h = selesai(c)
    assert h == {"ulang": False, "catatan_fase": None}
    assert len(c.ledger) == 2
    out, masuk = c.ledger
    assert out["movement_type"] == "RELOKASI_KELUAR" and out["warehouse_id"] == GA
    assert out["quantity_out"] == Decimal("10") and out["quantity_in"] == 0
    assert masuk["movement_type"] == "RELOKASI_MASUK" and masuk["warehouse_id"] == GB
    assert masuk["quantity_in"] == Decimal("10") and masuk["quantity_out"] == 0
    for r in (out, masuk):
        assert r["source_type"] == "RELOKASI_GUDANG" and r["source_id"] == U
        assert r["unit_cost"] == r["average_cost"] == Decimal("43332.95")      # WAC, TIDAK dihitung ulang
        assert r["total_cost"] == Decimal("433329.50")
        assert "journal_id" not in r                                           # tanpa jurnal, SENGAJA
    assert out["quantity_balance"] == Decimal("120") and masuk["quantity_balance"] == Decimal("130")
    assert any("status = 'completed'" in s for s, _ in c.updates)


def test_nilai_decimal_dua_dp_setengah_naik():
    c = Konek(header=draf(), items=[baris(P1, "3")], wac=Decimal("333.335"))
    selesai(c)
    assert c.ledger[0]["total_cost"] == Decimal("1000.01")        # 3 x 333.335 = 1000.005 -> 1000.01 (HALF_UP)
    assert c.ledger[0]["total_cost"] == c.ledger[1]["total_cost"]  # kedua kaki setara: neto nol


def test_stok_asal_kurang_409_tanpa_tulis():
    c = Konek(header=draf(), items=[baris(P1, "101")])
    with pytest.raises(HTTPException) as e:
        selesai(c)
    assert e.value.status_code == 409 and kode(e) == "PB_STOK_TIDAK_CUKUP"
    assert not c.ledger and not c.updates


def test_wac_nol_409_tanpa_tulis():
    c = Konek(header=draf(), items=[baris(P1, "1")], wac=Decimal("0"))
    with pytest.raises(HTTPException) as e:
        selesai(c)
    assert kode(e) == "PB_WAC_NOL" and not c.ledger


def test_barang_kedua_gagal_tidak_menulis_barang_pertama_di_stub_satu_transaksi():
    # Satu transaksi milik pemanggil: galat di barang ke-2 menaikkan HTTPException -> router ROLLBACK semuanya.
    c = Konek(header=draf(), items=[baris(P1, "10", 1), baris(P2, "10", 2)], produk={P1: barang(P1), P2: barang(P2, "Kaos B")})
    c.stok[(GA, P2)] = Decimal("1")
    with pytest.raises(HTTPException) as e:
        selesai(c)
    assert kode(e) == "PB_STOK_TIDAK_CUKUP"      # baris 1 sempat tertulis di stub; atomisitas dijamin transaksi pemanggil
    assert not any("status = 'completed'" in s for s, _ in c.updates)


def test_idempoten_sudah_selesai_tanpa_tulis():
    c = Konek(header=draf(status="completed"), items=[baris()])
    assert selesai(c) == {"ulang": True, "catatan_fase": None}
    assert not c.ledger and not c.updates


def test_status_lain_ditolak():
    for st in ("cancelled", "voided", "in_transit"):
        with pytest.raises(HTTPException) as e:
            selesai(Konek(header=draf(status=st), items=[baris()]))
        assert kode(e) == "PB_STATUS_TIDAK_BOLEH"


def test_kunci_stok_berurut_dan_dokumen_dulu():
    c = Konek(header=draf(), items=[baris(P1, "1", 1), baris(P2, "1", 2)], produk={P1: barang(P1), P2: barang(P2, "B")})
    c.stok[(GA, P2)] = Decimal("5")
    selesai(c)
    kunci = [s for s in c.log if "pg_advisory_xact_lock" in s]
    assert len(kunci) == 1 + 4   # dokumen + (2 barang x 2 gudang)


# ---- (11) fase SO informatif
def test_fase_so_terisi_dan_blok_tak_menggagalkan(_pasang):
    ev, fase = _pasang
    so = uuid.uuid4()
    c = Konek(header=draf(sales_order_id=so), items=[baris()])
    h = selesai(c)
    assert h["catatan_fase"] is None and fase["terapkan"] == [(so, "tersedia", GB)]
    fase["terapkan"].clear()
    fase["rencana"] = {"blocks": [{"message": "Pesanan sudah selesai."}], "berubah": False}
    c2 = Konek(header=draf(sales_order_id=so), items=[baris()])
    h2 = selesai(c2)
    assert "tidak diubah" in h2["catatan_fase"] and not fase["terapkan"] and len(c2.ledger) == 2   # stok tetap pindah


def test_audit_selesai_tercatat(_pasang):
    ev, _ = _pasang
    selesai(Konek(header=draf(), items=[baris()]))
    assert ("PEMINDAHAN_BARANG_SELESAI", "ST-2610-0001") in ev


# ---- (6) void
def asli_pasangan(qty="10", wac_lama="43332.95"):
    return [
        {"product_id": P1, "product_code": "K", "product_name": "Kaos A", "warehouse_id": GA, "quantity_in": Decimal("0"),
         "quantity_out": Decimal(qty), "unit_cost": Decimal(wac_lama), "total_cost": Decimal("433329.50"), "created_at": 1, "id": 1},
        {"product_id": P1, "product_code": "K", "product_name": "Kaos A", "warehouse_id": GB, "quantity_in": Decimal(qty),
         "quantity_out": Decimal("0"), "unit_cost": Decimal(wac_lama), "total_cost": Decimal("433329.50"), "created_at": 1, "id": 2},
    ]


def test_void_pembalik_dua_kaki_wac_saat_void():
    c = Konek(header=draf(status="completed"), asli=asli_pasangan(), wac=Decimal("50000"), saldo=Decimal("130"))
    h = jalan(S.void(c, ctx(), U, "salah gudang"))
    assert h["ulang"] is False and len(c.ledger) == 2
    a, b = c.ledger
    assert a["warehouse_id"] == GB and a["quantity_out"] == Decimal("10")          # tujuan dibalik KELUAR lebih dulu
    assert b["warehouse_id"] == GA and b["quantity_in"] == Decimal("10")
    for r in c.ledger:
        assert r["source_type"] == r["movement_type"] == "RELOKASI_GUDANG_VOID" and r["source_id"] == U
        assert r["unit_cost"] == Decimal("43332.95") and r["total_cost"] == Decimal("433329.50")   # nilai asli
        assert r["average_cost"] == Decimal("50000")                                                # WAC SAAT void, bukan basi
    assert any("status = 'voided'" in s for s, _ in c.updates)


def test_void_stok_tujuan_terpakai_409_tanpa_tulis():
    c = Konek(header=draf(status="completed"), asli=asli_pasangan("10"), stok={(GB, P1): Decimal("3")})
    with pytest.raises(HTTPException) as e:
        jalan(S.void(c, ctx(), U, "x"))
    assert kode(e) == "PB_STOK_TUJUAN_TERPAKAI" and not c.ledger and not c.updates


def test_void_idempoten_dan_status_salah():
    c = Konek(header=draf(status="voided"))
    assert jalan(S.void(c, ctx(), U, "x"))["ulang"] is True and not c.ledger
    with pytest.raises(HTTPException) as e:
        jalan(S.void(Konek(header=draf(status="draft")), ctx(), U, "x"))
    assert kode(e) == "PB_STATUS_TIDAK_BOLEH"


# ---- (7) NOL BUKU
def _fungsi_sumber():
    return (APP / "services" / "pemindahan_barang.py").read_text()


def test_nol_buku_dan_nol_float():
    src = _fungsi_sumber()
    t = ast.parse(src)
    doc = set()
    for n in ast.walk(t):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.body \
                and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant):
            doc.add(id(n.body[0].value))
    literal = " ".join(n.value for n in ast.walk(t) if isinstance(n, ast.Constant) and isinstance(n.value, str)
                       and id(n) not in doc)
    for terlarang in ("journal_entries", "journal_lines", "UPDATE warehouse_stock", "INSERT INTO warehouse_stock",
                      "record_inventory_inbound", "record_inventory_outbound", "resolve_account"):
        assert terlarang not in literal, terlarang
    for n in ast.walk(t):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in ("float", "int"):
            pytest.fail(f"float()/int() pada jalur uang di baris {n.lineno}")


def test_penjaga_nol_buku_bisa_merah():
    t = ast.parse('async def f(c):\n    await c.execute("UPDATE warehouse_stock SET quantity = 1")\n    x = int(1.5)\n')
    lit = " ".join(n.value for n in ast.walk(t) if isinstance(n, ast.Constant) and isinstance(n.value, str))
    assert "UPDATE warehouse_stock" in lit
    assert any(isinstance(n, ast.Call) and getattr(n.func, "id", "") == "int" for n in ast.walk(t))


# ---- (8) KOLOM HANTU: seluruh app
def _insert_ledger(sumber):
    hasil = []
    for m in re.finditer(r"INSERT\s+INTO\s+inventory_ledger\s*\(([^)]*)\)", sumber, re.I):
        kol = [k.strip() for k in m.group(1).split(",") if k.strip()]
        hasil.append(kol)
    return hasil


def test_tiap_insert_ledger_memakai_kolom_yang_ada():
    salah = {}
    banyak = 0
    for p in APP.rglob("*.py"):
        s = p.read_text()
        for kol in _insert_ledger(s):
            banyak += 1
            hantu = [k for k in kol if k not in KOLOM_LEDGER]
            if hantu:
                salah[str(p.relative_to(APP))] = hantu
    assert banyak >= 10, banyak               # pemindai bisa melihat: bukan hijau hampa
    assert salah == {}, salah


def test_penjaga_kolom_hantu_bisa_merah():
    s = "INSERT INTO inventory_ledger (tenant_id, item_id, quantity_change, total_value, transaction_date) VALUES ($1,$2,$3,$4,$5)"
    ada = _insert_ledger(s)
    assert ada and [k for k in ada[0] if k not in KOLOM_LEDGER] == ["item_id", "quantity_change", "total_value", "transaction_date"]


# ---- (10) izin + inventaris
def test_rute_punya_pola_izin():
    from app.middleware import permission_middleware as PM
    pola = [(re.compile(p), m, mod, aksi) for p, m, mod, aksi in PM.ROUTE_PERMISSIONS]

    def cari(path, meth):
        for rx, ms, mod, aksi in pola:
            if rx.match(path) and meth in ms:
                return mod, aksi
        return None
    assert cari("/api/stock-transfers/abc/complete", "POST") == ("item", "P")
    assert cari("/api/stock-transfers/abc/void", "POST") == ("item", "V")
    assert cari("/api/stock-transfers/abc/ship", "POST") == ("item", "P")


def test_kosakata_ledger_mengenal_void():
    from app.services import kosakata_ledger as K
    assert "RELOKASI_GUDANG" in K.DIKENAL and "RELOKASI_GUDANG_VOID" in K.DIKENAL


# ============================================================================
# v2 (12 Okt 2026, kebutuhan FE PB): pratinjau = angka server, atomik, riwayat, ringkasan, idempotensi
# ============================================================================
def _ln(r, i=0):
    return r["lines"][i]


def test_pratinjau_baru_angka_server_nol_tulis():
    c = Konek()
    r = jalan(S.pratinjau_baru(c, ctx(), S.PratinjauIn(
        from_warehouse_id=GA, to_warehouse_id=GB, items=[{"item_id": P1, "quantity_requested": Decimal("10")}])))
    assert r["ok"] is True and r["blocks"] == []
    ln = _ln(r)
    assert ln["quantity"] == Decimal("10") and ln["unit_cost"] == Decimal("43332.95") and ln["value"] == Decimal("433329.50")
    assert ln["available_from"] == Decimal("100") and ln["from_after"] == Decimal("90") and ln["to_after"] == Decimal("40")
    assert r["total_quantity"] == Decimal("10") and r["total_value"] == Decimal("433329.50")
    assert r["from_"] if False else r["dari"]["name"] == "Gudang A" and r["ke"]["name"] == "Gudang B"
    assert r["payload"]["items"][0]["quantity_requested"] == "10"
    # NOL TULIS: tak ada INSERT/UPDATE/DELETE, tak ada kunci advisory, tak ada kartu
    assert not c.ledger and not c.updates
    assert not any(s.startswith(("INSERT", "UPDATE", "DELETE")) or "pg_advisory" in s for s in c.log)


def test_pratinjau_semua_blok_sekaligus_dan_200_badan_cacat():
    c = Konek(produk={P1: barang(P1, track=False), P2: barang(P2, "Kaos B")}, gudang_aktif=False)
    isi = S.PratinjauIn(from_warehouse_id=GA, to_warehouse_id=None, items=[
        {"item_id": P1, "quantity_requested": Decimal("1")},          # non-stok
        {"item_id": P2, "quantity_requested": Decimal("5")},          # stok asal 0 -> kurang
        {"item_id": P2, "quantity_requested": Decimal("1")},          # ganda
        {"item_id": None, "quantity_requested": None},                # cacat: dilewati, tak meledak
    ])
    r = jalan(S.pratinjau_baru(c, ctx(), isi))
    kodes = [b["code"] for b in r["blocks"]]
    assert r["ok"] is False
    for k in ("PB_GUDANG_NONAKTIF", "PB_GUDANG_TUJUAN_WAJIB", "PB_BARANG_NON_STOK", "PB_STOK_TIDAK_CUKUP", "PB_BARANG_GANDA"):
        assert k in kodes, (k, kodes)
    assert len(kodes) >= 5                      # SEMUA blok sekaligus, bukan satu-satu
    assert any(b["item_id"] == str(P1) for b in r["blocks"])
    assert not c.ledger and not c.updates


def test_gudang_sama_dan_qty_nol_jadi_blok_bukan_422_pratinjau():
    r = jalan(S.pratinjau_baru(Konek(), ctx(), S.PratinjauIn(
        from_warehouse_id=GA, to_warehouse_id=GA, items=[{"item_id": P1, "quantity_requested": Decimal("0")}])))
    kodes = [b["code"] for b in r["blocks"]]
    assert "PB_GUDANG_SAMA" in kodes and "PB_QTY_TIDAK_SAH" in kodes and r["ok"] is False


def test_pratinjau_selesai_sama_dengan_angka_tulis():
    pre = jalan(S.pratinjau_selesai(Konek(header=draf(), items=[baris(P1, "10")]), ctx(), U))
    c = Konek(header=draf(), items=[baris(P1, "10")])
    selesai(c)
    out = c.ledger[0]
    ln = _ln(pre)
    assert ln["unit_cost"] == out["unit_cost"] and ln["value"] == out["total_cost"] and ln["quantity"] == out["quantity_out"]
    assert pre["ok"] is True and pre["transfer_number"] == "ST-2610-0001"


def test_pratinjau_selesai_status_dan_ulang():
    r = jalan(S.pratinjau_selesai(Konek(header=draf(status="cancelled"), items=[baris()]), ctx(), U))
    assert r["ok"] is False and r["blocks"][0]["code"] == "PB_STATUS_TIDAK_BOLEH"
    r2 = jalan(S.pratinjau_selesai(Konek(header=draf(status="completed"), items=[baris()]), ctx(), U))
    assert r2["ok"] is True and r2["ulang"] is True


def test_pratinjau_void_stok_sebelum_sesudah_dan_blok():
    c = Konek(header=draf(status="completed"), asli=asli_pasangan("10"), stok={(GB, P1): Decimal("30"), (GA, P1): Decimal("90")})
    r = jalan(S.pratinjau_void(c, ctx(), U, "salah gudang"))
    assert r["ok"] is True
    ln = _ln(r)
    assert ln["from_before"] == Decimal("30") and ln["from_after"] == Decimal("20")      # gudang tujuan berkurang
    assert ln["to_before"] == Decimal("90") and ln["to_after"] == Decimal("100")         # gudang asal kembali
    assert not c.ledger and not c.updates
    r2 = jalan(S.pratinjau_void(Konek(header=draf(status="completed"), asli=asli_pasangan("10"), stok={(GB, P1): Decimal("3")}),
                                ctx(), U, ""))
    kodes = [b["code"] for b in r2["blocks"]]
    assert "PB_STOK_TUJUAN_TERPAKAI" in kodes and "PB_ALASAN_WAJIB" in kodes


def test_pratinjau_batal():
    r = jalan(S.pratinjau_batal(Konek(header=draf()), ctx(), U, "salah buat"))
    assert r["ok"] is True
    r2 = jalan(S.pratinjau_batal(Konek(header=draf(status="completed")), ctx(), U, "x"))
    assert r2["ok"] is False and r2["blocks"][0]["code"] == "PB_STATUS_TIDAK_BOLEH"
    r3 = jalan(S.pratinjau_batal(Konek(header=draf()), ctx(), U, " "))
    assert [b["code"] for b in r3["blocks"]] == ["PB_ALASAN_WAJIB"]


def test_buat_dan_selesaikan_gagal_melempar_dan_router_satu_transaksi():
    c = Konek(items=[baris(P1, "101")], stok={(GA, P1): Decimal("100")})
    # selesaikan membaca header+item dari DB; stub header diisi setelah INSERT -> pakai header draf yang sama
    c.header = draf()
    with pytest.raises(HTTPException) as e:
        jalan(S.buat_dan_selesaikan(c, ctx(), body(items=[{"item_id": P1, "quantity_requested": Decimal("101")}])))
    assert kode(e) == "PB_STOK_TIDAK_CUKUP"
    src = (APP / "routers" / "stock_transfers.py").read_text()
    a = src.index("async def create_and_complete_stock_transfer")
    isi = src[a:src.index("@router.post", a + 10)]
    assert isi.count("conn.transaction()") == 1 and "buat_dan_selesaikan" in isi
    assert "buat_draf" not in isi                      # tak ada dua transaksi/draf yatim


def test_idempotensi_di_kedua_jalur_buat():
    src = (APP / "routers" / "stock_transfers.py").read_text()
    for nama in ("async def create_stock_transfer(", "async def create_and_complete_stock_transfer("):
        a = src.index(nama)
        isi = src[a:src.index("\n\n\n", a)]
        assert "idem_buat.kunci_dari(request)" in isi and "idem_buat.mulai(" in isi and "idem_buat.simpan(" in isi, nama
        assert isi.index("idem_buat.mulai(") < isi.index("pb.buat"), nama     # replay SEBELUM nomor terbit


def test_rute_baru_izin_dan_urutan():
    from app.middleware import permission_middleware as PM
    pola = [(re.compile(p), m, mod, aksi) for p, m, mod, aksi in PM.ROUTE_PERMISSIONS]

    def cari(path, meth):
        for rx, ms, mod, aksi in pola:
            if rx.match(path) and meth in ms:
                return mod, aksi
        return None
    x = "00000000-0000-0000-0000-000000000000"
    assert cari("/api/stock-transfers/preview", "POST") == ("item", "C")
    assert cari("/api/stock-transfers/create-and-complete", "POST") == ("item", "P")
    assert cari(f"/api/stock-transfers/{x}/complete/preview", "POST") == ("item", "P")
    assert cari(f"/api/stock-transfers/{x}/void/preview", "POST") == ("item", "V")
    assert cari(f"/api/stock-transfers/{x}/cancel/preview", "POST") == ("item", "V")
    for g in ("/api/stock-transfers", "/api/stock-transfers/summary", f"/api/stock-transfers/{x}", f"/api/stock-transfers/{x}/history"):
        assert cari(g, "GET") == ("item", "R"), g
    # literal sebelum /{transfer_id}: 'summary' tidak boleh tertelan sebagai id
    rute = [(r.path, tuple(sorted(r.methods))) for r in R.router.routes]
    assert rute.index(("/summary", ("GET",))) < rute.index(("/{transfer_id}", ("GET",)))
    assert rute.index(("/in-transit", ("GET",))) < rute.index(("/{transfer_id}", ("GET",)))


def test_riwayat_pemindahan_terdaftar():
    from app.services import so_riwayat as H
    assert H.ENTITAS_AUDIT["stock_transfers"] == "stock_transfer"
    assert H.MODUL_PEMINDAHAN["stock_transfer"] == "item"
    assert callable(H.riwayat_pemindahan)
    for ev in ("PEMINDAHAN_BARANG_SELESAI", "PEMINDAHAN_BARANG_DI_VOID"):
        assert ev in H.RINGKAS_AUDIT
