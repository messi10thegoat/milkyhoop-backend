"""BUG-021 (5 Nov 2026): impor barang Excel + 1 baris chatbox = satu jalur batch (pratinjau per baris -> tulis ATOMIK)."""
import inspect
import os
from decimal import Decimal as D

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from app.services import impor_barang as IB  # noqa: E402
from app.routers import items as R  # noqa: E402


# ---------------------------------------------------------------- angka
@pytest.mark.parametrize("teks,harap", [
    ("243rb", D("243000")), ("1,5jt", D("1500000")), ("35.000", D("35000")), ("35000", D("35000")), ("0,5", D("0.5")),
    ("0.5", D("0.5")), ("Rp 12.500", D("12500")), ("35k", D("35000")), (12, D("12")), ("", None), (None, None),
])
def test_angka(teks, harap):
    assert IB.angka(teks) == harap


def test_angka_tak_terbaca():
    with pytest.raises(ValueError):
        IB.angka("tiga puluh")


# ---------------------------------------------------------------- tafsir chatbox
def test_tafsir_harga_ikut_satuan_tepat_sebelumnya_karton():
    b = IB.tafsir_baris("bimoli 2L, karton isi 6, beli 210rb, jual 243rb")
    assert b["nama"] == "bimoli 2L" and b["satuan_besar"] == "karton" and b["isi"] == "6"
    assert b["harga_beli_besar"] == "210rb" and b["harga_jual_besar"] == "243rb"
    assert "harga_jual" not in b and b["satuan_dasar"] == "Pcs" and "_galat" not in b


def test_tafsir_harga_ikut_satuan_dasar_yang_disebut_terakhir():
    b = IB.tafsir_baris("minyak 2L karton=12 botol jual 35rb")
    assert (b["nama"], b["satuan_besar"], b["isi"], b["satuan_dasar"]) == ("minyak 2L", "karton", "12", "botol")
    assert b["harga_jual"] == "35rb" and "harga_jual_besar" not in b


def test_tafsir_garis_miring_menang_dan_stok_satuan_besar():
    b = IB.tafsir_baris("gula 1kg dus isi 20 pcs jual 15rb, jual 290rb/dus, stok 3 dus, kode GL-01, kategori Sembako")
    assert b["satuan_dasar"] == "pcs" and b["harga_jual"] == "15rb" and b["harga_jual_besar"] == "290rb"
    assert D(b["stok_awal"]) == 60 and b["kode"] == "GL-01" and b["kategori"] == "Sembako"


def test_tafsir_tanpa_satuan_sebelumnya_per_satuan_dasar():
    b = IB.tafsir_baris("teh pucuk jual 4rb")
    assert b["harga_jual"] == "4rb" and b["satuan_dasar"] == "Pcs"


def test_tafsir_sisa_tak_dikenal_tidak_dibuang_diam():
    b = IB.tafsir_baris("teh pucuk jual 4rb blabla")
    assert any("blabla" in g for g in b["_galat"])
    _, bl, _ = IB.ke_badan(b)
    assert bl and bl[0]["code"] == "TAK_TERBACA"


def test_tafsir_kata_jasa_di_nama_bukan_tipe():
    b = IB.tafsir_baris("jasa cuci karpet tipe jasa jual 50rb")
    assert b["nama"] == "jasa cuci karpet" and b["tipe"] == "jasa"


def test_tafsir_banyak_baris():
    assert [x["nama"] for x in IB.tafsir_teks("teh jual 4rb\n\n kopi jual 5rb ")] == ["teh", "kopi"]


# ---------------------------------------------------------------- baris -> badan POST /items
def test_ke_badan_harga_eksplisit_per_satuan_tanpa_turunan():
    p, bl, harga = IB.ke_badan({"nama": "Bimoli", "satuan_dasar": "Pouch", "satuan_besar": "Karton", "isi": 6,
                                "harga_jual_besar": "243rb", "harga_beli_besar": 210000})
    assert bl == [] and "sales_price" not in p
    assert p["conversions"] == [{"conversion_unit": "Karton", "conversion_factor": "6", "sales_price": 243000.0, "purchase_price": 210000.0}]
    assert [h["teks"].split(" / ")[1] for h in harga] == ["Karton", "Karton"] and "243.000" in harga[0]["teks"]


@pytest.mark.parametrize("b,kode", [
    ({"nama": "X", "satuan_dasar": "Pcs", "tipe": "jasa", "satuan_besar": "Dus", "isi": 2}, "KONVERSI_HANYA_BARANG"),
    ({"nama": "X", "satuan_dasar": "Pcs", "satuan_besar": "Dus"}, "ISI_WAJIB"),
    ({"nama": "X", "satuan_dasar": "Pcs", "harga_jual_besar": 5}, "SATUAN_BESAR_WAJIB"),
    ({"nama": "X", "satuan_dasar": "Pcs", "tipe": "barang aneh"}, "TIPE_TIDAK_DIKENAL"),
    ({"nama": "X", "satuan_dasar": "Pcs", "harga_jual": "lima"}, "ANGKA_TIDAK_SAH"),
    ({"nama": "X", "satuan_dasar": "Pcs", "tipe": "non stok", "stok_awal": 5}, "STOK_HANYA_BARANG"),
])
def test_ke_badan_blok(b, kode):
    assert kode in [x["code"] for x in IB.ke_badan(b)[1]]


# ---------------------------------------------------------------- Excel
def test_templat_bolak_balik():
    isi = IB.templat_xlsx([{"abbreviation": "Pcs"}, {"abbreviation": "Pouch"}])
    baris = IB.baca_xlsx(isi)["baris"]  # BUG-101: baca_xlsx -> {baris, pemetaan, tak_dipakai, peringatan}
    assert baris[0]["nama"] == "Minyak Goreng 2L" and baris[0]["isi"] == 12 and baris[0]["satuan_besar"] == "Karton"


def test_baca_xlsx_judul_asing_ditolak():
    from openpyxl import Workbook
    import io
    wb = Workbook(); wb.active.append(["foo", "bar"]); buf = io.BytesIO(); wb.save(buf)
    with pytest.raises(HTTPException) as e:
        IB.baca_xlsx(buf.getvalue())
    assert e.value.detail["code"] == "KOLOM_TIDAK_DIKENAL"


# ---------------------------------------------------------------- rencana batch (penentu per baris dipalsukan)
class _Conn:
    def __init__(self, tx=True):
        self.tx, self.exec, self.kode = tx, [], {}

    async def fetch(self, sql, *a):
        return [{"name": "Minyak"}]

    async def execute(self, sql, *a):
        self.exec.append((sql, a))

    async def fetchval(self, sql, *a):
        return self.kode.get(str(a[0]))

    def is_in_transaction(self):
        return self.tx


@pytest.fixture
def palsu(monkeypatch):
    s = {"siapkan": [], "tulis": [], "buat": [], "tolak": {}}

    async def daftar(conn, tid):
        return [{"abbreviation": u} for u in ("Pcs", "Karton", "Botol")]

    async def siapkan(conn, tid, ctx, body):
        s["siapkan"].append(body.name)
        if body.name in s["tolak"]:
            raise s["tolak"][body.name]
        return (body.item_code or "BRG-0010", None)

    async def buat(conn, tid, nama, singkatan=None):
        s["buat"].append(nama)

    async def tulis(conn, ctx, body, uid, un):
        s["tulis"].append(body.name)
        if body.name in s.get("gagal_tulis", ()):
            raise HTTPException(status_code=409, detail={"code": "NAMA_BARANG_DIPAKAI", "message": "balapan"})
        iid = f"id-{len(s['tulis'])}"
        conn.kode[iid] = f"K{len(s['tulis'])}"
        return iid

    monkeypatch.setattr(IB.SM, "daftar", daftar)
    monkeypatch.setattr(IB.SM, "buat", buat)
    monkeypatch.setattr(R, "_siapkan_buat_barang", siapkan)
    monkeypatch.setattr(R, "tulis_barang_baru", tulis)
    return s


CTX = {"tenant_id": "t", "user_id": "u"}
_b = lambda nama, **k: {"nama": nama, "satuan_dasar": "pcs", **k}  # noqa: E731


@pytest.mark.asyncio
async def test_rencana_kode_otomatis_berurutan_dan_bentrok_kode_eksplisit(palsu):
    r = await IB.rencana(_Conn(), CTX, [_b("A"), _b("B"), _b("C", kode="BRG-0013")])
    assert [x["rencana"]["item_code"] for x in r["baris"]] == ["BRG-0010", "BRG-0011", "BRG-0013"] and r["ok"]
    r = await IB.rencana(_Conn(), CTX, [_b("A", kode="BRG-0010"), _b("B")])
    assert [x["rencana"]["item_code"] for x in r["baris"]] == ["BRG-0010", "BRG-0011"] and r["ok"]
    r = await IB.rencana(_Conn(), CTX, [_b("A"), _b("B", kode="brg-0010")])
    assert not r["ok"] and r["baris"][1]["blocks"][0]["code"] == "GANDA_DI_BERKAS"


@pytest.mark.asyncio
async def test_rencana_nama_ganda_di_berkas_tanpa_peduli_huruf(palsu):
    r = await IB.rencana(_Conn(), CTX, [_b("Teh Pucuk"), _b("teh  pucuk")])
    assert r["baris"][0]["ok"] and not r["baris"][1]["ok"]
    assert r["baris"][1]["blocks"][0] == {"code": "GANDA_DI_BERKAS", "message": 'Nama "teh  pucuk" sama dengan baris 1.', "field": "name"}


@pytest.mark.asyncio
async def test_rencana_satuan_kategori_ejaan_tenant_dan_baru_sekali(palsu):
    r = await IB.rencana(_Conn(), CTX, [_b("A", satuan_besar="karton", isi=6, kategori="minyak"),
                                         {"nama": "B", "satuan_dasar": "Pouch", "kategori": "Sabun"}, {"nama": "C", "satuan_dasar": "pouch"}])
    assert r["ok"] and r["ringkas"] == {"barang": 3, "satuan_baru": ["Pouch"], "kategori_baru": ["Sabun"]}
    p = r["baris"][0]["payload"]
    assert (p["base_unit"], p["conversions"][0]["conversion_unit"], p["kategori"]) == ("Pcs", "Karton", "Minyak")
    assert r["baris"][2]["payload"]["base_unit"] == "Pouch"


@pytest.mark.asyncio
async def test_rencana_galat_penentu_per_baris_termasuk_saldo_awal_law28(palsu):
    palsu["tolak"]["B"] = HTTPException(status_code=400, detail="Usaha sudah bertransaksi; saldo awal stok ditolak.")
    r = await IB.rencana(_Conn(), CTX, [_b("A"), _b("B", stok_awal=10, harga_pokok_awal=5000)])
    assert r["baris"][0]["ok"] and not r["baris"][1]["ok"] and not r["ok"]
    assert "saldo awal" in r["baris"][1]["blocks"][0]["message"]
    assert r["blocks"][0]["code"] == "BARIS_BERGALAT"


@pytest.mark.asyncio
async def test_rencana_batas_baris(palsu):
    r = await IB.rencana(_Conn(), CTX, [_b(f"x{i}") for i in range(IB.MAKS_BARIS + 1)])
    assert not r["ok"] and r["blocks"][0]["code"] == "TERLALU_BANYAK" and palsu["siapkan"] == []


# ---------------------------------------------------------------- tulis ATOMIK
@pytest.mark.asyncio
async def test_tulis_satu_blok_tolak_semua_nol_tulis(palsu):
    palsu["tolak"]["B"] = HTTPException(status_code=409, detail={"code": "NAMA_BARANG_DIPAKAI", "message": "sudah ada"})
    with pytest.raises(HTTPException) as e:
        await IB.tulis(_Conn(), CTX, [_b("A", satuan_dasar="Pouch"), _b("B")], "u", "n")
    assert e.value.status_code == 422 and e.value.detail["code"] == "IMPOR_DITOLAK"
    assert [x["no"] for x in e.value.detail["baris"]] == [2]
    assert palsu["tulis"] == [] and palsu["buat"] == []


@pytest.mark.asyncio
async def test_tulis_kunci_lalu_satuan_kategori_lalu_barang_berurutan(palsu):
    k = _Conn()
    h = await IB.tulis(k, CTX, [_b("A", satuan_dasar="Pouch", kategori="Sabun"), _b("B", satuan_dasar="pouch")], "u", "n")
    assert "ITEM_IMPORT:t" in k.exec[0][1] and "item_categories" in k.exec[1][0]
    assert palsu["buat"] == ["Pouch"] and palsu["tulis"] == ["A", "B"]
    assert h["jumlah"] == 2 and [x["item_code"] for x in h["barang"]] == ["K1", "K2"]


@pytest.mark.asyncio
async def test_tulis_galat_di_tengah_naik_supaya_seluruh_batch_rollback(palsu):
    palsu["gagal_tulis"] = {"B"}
    with pytest.raises(HTTPException) as e:
        await IB.tulis(_Conn(), CTX, [_b("A"), _b("B"), _b("C")], "u", "n")
    assert e.value.status_code == 409 and palsu["tulis"] == ["A", "B"]


@pytest.mark.asyncio
async def test_tulis_wajib_di_transaksi(palsu):
    with pytest.raises(RuntimeError):
        await IB.tulis(_Conn(tx=False), CTX, [_b("A")], "u", "n")


# ---------------------------------------------------------------- rute
def _src(fn):
    return " ".join(inspect.getsource(fn).split())


def test_rute_pratinjau_read_only_dan_tulis_atomik_berkunci():
    lihat, tulis = _src(R.pratinjau_impor_barang), _src(R.impor_barang)
    assert "transaction(readonly=True)" in lihat and "await tr.rollback()" in lihat and "IB.rencana(" in lihat
    assert "async with conn.transaction():" in tulis and "idem_buat.mulai_aksi(" in tulis and "IB.tulis(" in tulis
    assert tulis.index("mulai_aksi(") < tulis.index("IB.tulis(")


def test_rute_impor_terdaftar_sebelum_rute_id_dan_berizin():
    from app.middleware import permission_middleware as PM
    jalur = [getattr(r, "path", "") for r in R.router.routes]
    assert jalur.index("/items/impor/templat") < jalur.index("/items/{item_id}")
    src = inspect.getsource(PM)
    assert 'r"^/api/items/impor(/baca|/pratinjau)?$", ["POST"], "item", "C"' in src
    assert src.index("/api/items/impor/templat") < src.index('r"^/api/items/[^/]+$", ["GET"]')
