"""Gerbang T2 paket Penawaran: skema tool cocok Pydantic BE, rute ada, rute terpetakan izin,
medan jurnal tidak bisa dikirim, gerbang T0 (AS-G1/AS-G14) menurunkan WRITE ke NAV.

Tes teks-sumber: membaca berkas BE, bukan mengimpor (impor router menarik DB/LLM)."""
import ast
import os
import re

import pytest

from app.milkybook.inti.registri import Kelas, Registri
from app.milkybook.paket import penawaran as pw
from app.milkybook.paket import penjualan_baca as pb
from app.milkybook.paket import semua as semua_paket

from .conftest import izin_semua

AKAR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "app"))


def _src(rel):
    with open(os.path.join(AKAR, rel), encoding="utf-8") as f:
        return f.read()


def medan_kelas(rel, kelas):
    """{nama_medan: wajib?} dari class Pydantic (termasuk kelas induk dalam berkas yang sama)."""
    pohon = ast.parse(_src(rel))
    kelas_peta = {n.name: n for n in pohon.body if isinstance(n, ast.ClassDef)}
    hasil = {}
    node = kelas_peta[kelas]
    rantai = [node]
    while rantai[-1].bases and isinstance(rantai[-1].bases[0], ast.Name) and rantai[-1].bases[0].id in kelas_peta:
        rantai.append(kelas_peta[rantai[-1].bases[0].id])
    for n in reversed(rantai):
        for st in n.body:
            if isinstance(st, ast.AnnAssign) and isinstance(st.target, ast.Name) and not st.target.id.startswith("_"):
                wajib = st.value is None or (
                    isinstance(st.value, ast.Call) and getattr(st.value.func, "id", "") == "Field"
                    and st.value.args and isinstance(st.value.args[0], ast.Constant) and st.value.args[0].value is Ellipsis
                )
                hasil[st.target.id] = wajib
    return hasil


PETA_SKEMA = {
    # tool -> (berkas skema, kelas, kelas baris)
    "hitung_penawaran": ("schemas/quotes.py", "CreateQuoteRequest", "QuoteItemCreate"),
    "buat_penawaran": ("schemas/quotes.py", "CreateQuoteRequest", "QuoteItemCreate"),
    "pratinjau_ubah_penawaran": ("schemas/quotes.py", "UpdateQuoteRequest", "QuoteItemUpdate"),
    "ubah_penawaran": ("schemas/quotes.py", "UpdateQuoteRequest", "QuoteItemUpdate"),
    "pratinjau_batal_penawaran": ("schemas/quotes.py", "VoidQuoteRequest", None),
    "batalkan_penawaran": ("schemas/quotes.py", "VoidQuoteRequest", None),
    "pratinjau_jadi_pesanan": ("schemas/quotes.py", "ConvertToOrderRequest", None),
    "jadikan_pesanan": ("schemas/quotes.py", "ConvertToOrderRequest", None),
    "pratinjau_pelanggan_baru": ("schemas/customers.py", "CreateCustomerRequest", None),
    "buat_pelanggan": ("schemas/customers.py", "CreateCustomerRequest", None),
    "pratinjau_barang_baru": ("schemas/items.py", "CreateItemRequest", None),
    "buat_barang": ("schemas/items.py", "CreateItemRequest", None),
}
SEMUA_TOOLS = pw.TOOLS + pb.TOOLS
TOOL = {t.nama: t for t in SEMUA_TOOLS}


@pytest.mark.parametrize("nama", sorted(PETA_SKEMA))
def test_medan_tool_subset_pydantic_dan_wajib_tercakup(nama):
    berkas, kelas, kelas_baris = PETA_SKEMA[nama]
    t = TOOL[nama]
    medan = medan_kelas(berkas, kelas)
    props = {k: v for k, v in t.skema["properties"].items() if k not in t.param_jalur()}
    asing = set(props) - set(medan)
    assert not asing, f"{nama}: medan tak ada di {kelas}: {asing}"
    wajib_be = {k for k, w in medan.items() if w}
    assert wajib_be <= set(t.skema["required"]), f"{nama}: wajib BE tak wajib di tool: {wajib_be - set(t.skema['required'])}"
    if kelas_baris:
        mb = medan_kelas(berkas, kelas_baris)
        baris = props["items"]["items"]["properties"]
        assert not set(baris) - set(mb), f"{nama}: medan baris asing {set(baris) - set(mb)}"


def test_medan_pembuat_jurnal_tidak_bisa_dikirim():
    terlarang = {"ar_opening_balance", "opening_balance_date", "opening_stock", "opening_stock_rate", "opening_stock_date"}
    for t in SEMUA_TOOLS:
        assert not terlarang & set(t.skema.get("properties", {})), t.nama
        assert t.skema.get("additionalProperties") is False


def test_bisa_dikirim_ada_di_be_dan_ditanyakan():
    assert "bisa_dikirim" in medan_kelas("schemas/items.py", "CreateItemRequest")
    assert "TANYAKAN" in TOOL["buat_barang"].skema["properties"]["bisa_dikirim"]["description"]


# ---------------------------------------------------------------- rute BE

PREFIKS = {  # prefiks main.py -> berkas router + prefiks dekorator
    "/api/quotes": ("routers/quotes.py", ""),
    "/api/customers": ("routers/customers.py", ""),
    "/api/items": ("routers/items.py", "/items"),
    "/api/sales-orders": ("routers/sales_orders.py", ""),
    "/api/sales-invoices": ("routers/sales_invoices.py", ""),
    "/api/dashboard": ("routers/dashboard_v2.py", ""),
    "/api/tax-codes": ("routers/tax_codes.py", ""),
    "/api/receive-payments": ("routers/receive_payments.py", ""),
    "/api/customer-deposits": ("routers/customer_deposits.py", ""),
    "/api/deliveries": ("routers/deliveries.py", ""),
}


def _cari_rute(metode, jalur):
    for pref, (berkas, dek) in PREFIKS.items():
        if jalur == pref or jalur.startswith(pref + "/"):
            sisa = dek + jalur[len(pref):]
            sisa_pola = re.sub(r"\{[a-z_]+\}", r"{[a-z_]+}", re.escape(sisa).replace(r"\{", "{").replace(r"\}", "}"))
            pola = re.compile(r'@router\.' + metode.lower() + r'\(\s*"' + sisa_pola + r'"')
            return bool(pola.search(_src(berkas))), berkas
    return False, None


def test_main_py_memakai_prefiks_yang_diasumsikan():
    main = _src("main.py")
    for pref, (berkas, _) in PREFIKS.items():
        mod = os.path.basename(berkas)[:-3]
        p = "/api" if mod == "items" else pref
        assert re.search(rf'include_router\(\s*{mod}\.router,\s*prefix="{re.escape(p)}"', main), (mod, p)


@pytest.mark.parametrize("t", [t for t in SEMUA_TOOLS if t.jalur and not (t.kelas == Kelas.PREVIEW and t.butuh)], ids=lambda t: t.nama)
def test_rute_tool_ada_di_router_be(t):
    """Termasuk WRITE ber-butuh dan rute aksi NAV: rutenya SUDAH ada, hanya idempotency/pratinjau yang menunggu T0."""
    ada, berkas = _cari_rute(t.metode, t.jalur)
    assert ada, f"{t.nama}: {t.metode} {t.jalur} tak ditemukan di {berkas}"


@pytest.mark.parametrize("t", [t for t in pw.TOOLS if t.kelas == Kelas.PREVIEW and t.butuh], ids=lambda t: t.nama)
def test_rute_pratinjau_t0_belum_ada_ingatkan_saat_mendarat(t):
    """Merah = T0 sudah mendarat: hapus `butuh`, perbarui stub eval dengan bentuk nyata."""
    ada, _ = _cari_rute(t.metode, t.jalur)
    assert not ada, f"{t.nama}: rute T0 sudah ada; cabut butuh={t.butuh} dan perbarui stub"


def test_query_tool_baca_adalah_parameter_router():
    peta = {"cari_pelanggan": "routers/customers.py", "cari_barang": "routers/items.py", "daftar_penawaran": "routers/quotes.py",
            "daftar_pesanan": "routers/sales_orders.py", "daftar_faktur": "routers/sales_invoices.py",
            "opsi_pajak": "routers/tax_codes.py", "daftar_penerimaan": "routers/receive_payments.py",
            "daftar_uang_muka": "routers/customer_deposits.py", "daftar_pengiriman": "routers/deliveries.py",
            "penawaran_segera_kedaluwarsa": "routers/quotes.py", "ringkasan_faktur": "routers/sales_invoices.py"}
    for nama, berkas in peta.items():
        s = _src(berkas)
        for k in TOOL[nama].skema["properties"]:
            assert re.search(rf"\b{k}\s*:[^=]{{0,600}}?=\s*Query\(", s), f"{nama}.{k} bukan Query di {berkas}"


def _rute_izin():
    pohon = ast.parse(_src("middleware/permission_middleware.py"))
    for n in pohon.body:
        if isinstance(n, ast.AnnAssign) and getattr(n.target, "id", "") == "ROUTE_PERMISSIONS":
            return [(re.compile(p), m, mod, a) for p, m, mod, a in ast.literal_eval(n.value)]
    raise AssertionError("ROUTE_PERMISSIONS tak ditemukan")


# /api/dashboard dikecualikan dari tabel izin (permission_middleware: "Dashboard has own FCL rules";
# dashboard_izin.boleh_semua). izin_rute_pengguna menjawab rute tak terpetakan = OWNER saja, jadi di
# pilot `ringkasan_usaha` hanya muncul untuk OWNER. Integrasi dashboard_izin = tiket lanjutan.
IZIN_SENDIRI = {"ringkasan_usaha"}


@pytest.mark.parametrize("t", [t for t in SEMUA_TOOLS if t.jalur], ids=lambda t: t.nama)
def test_rute_tool_terpetakan_di_tabel_izin(t):
    """Rute tak terpetakan = OWNER saja (izin_rute_pengguna) -> tool hilang untuk staf. Harus terpetakan."""
    j = t.jalur_contoh()
    cocok = [(mod, a) for pola, metode, mod, a in _rute_izin() if t.metode in metode and pola.match(j)]
    if t.butuh and t.kelas == Kelas.PREVIEW:
        return  # rute T0 belum ada; pola izinnya bagian dari tiket T0
    if t.nama in IZIN_SENDIRI:
        assert not cocok  # merah = sudah terpetakan: cabut dari IZIN_SENDIRI
        return
    assert cocok, f"{t.nama}: {t.metode} {j} tak terpetakan"


# ---------------------------------------------------------------- gerbang T0

async def test_sebelum_t0_semua_write_jadi_nav_dan_pratinjau_t0_hilang():
    r = Registri([pw.paket()])
    efektif = {t.nama: t for t in await r.tools_untuk(izin_semua, gap_mendarat=())}
    for nama in ("ubah_penawaran", "batalkan_penawaran", "buat_pelanggan", "buat_barang"):
        assert efektif[nama].kelas == Kelas.NAV, nama
    assert "pratinjau_pelanggan_baru" not in efektif and "pratinjau_barang_baru" not in efektif
    assert [t.nama for t in efektif.values() if t.kelas == Kelas.WRITE] == ["buat_penawaran", "jadikan_pesanan"]


@pytest.mark.parametrize("t", [t for t in pw.TOOLS if t.kelas == Kelas.WRITE], ids=lambda t: t.nama)
def test_write_tanpa_butuh_harus_idempoten_di_be(t):
    """WRITE aktif tanpa gerbang T0 hanya bila handler BE-nya membaca kunci idempotency."""
    if t.butuh:
        return
    s = _src("routers/quotes.py")
    if t.nama == "buat_penawaran":
        m = re.search(r'@router\.post\(\s*""', s)
        badan = s[m.end(): m.end() + 6000]
        assert re.search(r"idem_buat\.kunci_dari\(request\)", badan) and "idem_buat.mulai(" in badan
    elif t.nama == "jadikan_pesanan":
        # idempoten alami: kunci advisory per penawaran + SO lama dikembalikan bila sudah dikonversi
        i = s.index("async def _konversi_penawaran(")
        badan = s[i: i + 4000]
        assert "pg_advisory_xact_lock" in badan and 'f"QUOTE:{ctx[\'tenant_id\']}:{quote_id}"' in badan
        assert '"already_converted": True' in badan
        assert re.search(r"await _konversi_penawaran\(conn, ctx, quote_id, body\)", s)
    else:
        raise AssertionError(f"{t.nama}: WRITE tanpa butuh tanpa bukti idempotency")


async def test_sesudah_t0_write_aktif():
    r = Registri([pw.paket()])
    efektif = {t.nama: t for t in await r.tools_untuk(izin_semua, gap_mendarat=(pw.AS_G1, pw.AS_G14))}
    assert {n for n, t in efektif.items() if t.kelas == Kelas.WRITE} == {
        "buat_penawaran", "ubah_penawaran", "batalkan_penawaran", "jadikan_pesanan", "buat_pelanggan", "buat_barang"}


async def test_hanya_as_g1_pelanggan_barang_tetap_nav():
    r = Registri([pw.paket()])
    efektif = {t.nama: t for t in await r.tools_untuk(izin_semua, gap_mendarat=(pw.AS_G1,))}
    assert efektif["ubah_penawaran"].kelas == Kelas.WRITE
    assert efektif["buat_pelanggan"].kelas == Kelas.NAV and efektif["buat_barang"].kelas == Kelas.NAV


def test_kartu_pengetahuan_dimuat_dan_menyebut_kekhasan():
    p = pw.paket()
    for kata in ("bisa_dikirim", "kode_order", "STATUS_BOLEH_UBAH", "Jangan hitung sendiri"):
        assert kata in p.pengetahuan



def test_tidak_ada_tool_strict():
    """strict dicabut 24 Okt (batas API 20 tool/24 opsional/16 union). Merah = ada yang memasang strict lagi."""
    assert not [t.nama for t in SEMUA_TOOLS if t.untuk_api().get("strict")]


@pytest.mark.parametrize("t", [t for t in SEMUA_TOOLS if t.kelas == Kelas.READ and t.jalur], ids=lambda t: t.nama)
def test_nilai_enum_tool_baca_ada_di_router(t):
    """Nilai enum karangan = 422 di BE (Literal). Setiap nilai harus tertulis di berkas router rutenya."""
    berkas = _cari_rute(t.metode, t.jalur)[1]
    s = _src(berkas)
    for k, v in t.skema["properties"].items():
        for nilai in v.get("enum", []):
            assert f'"{nilai}"' in s, f"{t.nama}.{k}={nilai} tak ada di {berkas}"



def test_paket_baca_hanya_read_dan_dimuat_semua():
    assert all(t.kelas == Kelas.READ for t in pb.TOOLS)
    nama = [p.modul for p in semua_paket()]
    assert nama == ["penawaran", "penjualan_baca"]
    r = Registri(semua_paket())
    for kata in ("menunggu_pembayaran", "belum_diterapkan", "perlu_difaktur", "Jangan menghitung sisa"):
        assert kata in r.pengetahuan()


def test_model_bawaan_sonnet_medium():
    """Putusan MASTER 24 Okt (rancangan §11.8)."""
    from app.milkybook.inti import model
    assert model.MODEL_BAWAAN == "claude-sonnet-5-5" and model.EFFORT_BAWAAN == "medium"


async def test_pratinjau_pasangan_tersembunyi_dari_model_tetap_dipakai_inti():
    from app.milkybook.inti.audit import AuditMemori
    from app.milkybook.inti.putaran import Putaran, Sesi
    from .conftest import BEPalsu, ModelPalsu, teks
    m = ModelPalsu([teks("ok")])
    p = Putaran(Registri(semua_paket()), m, AuditMemori(), gap_mendarat=(pw.AS_G1, pw.AS_G14))
    await p.giliran(Sesi("s", "t", "u"), "halo", BEPalsu(), izin_semua)
    dikirim = {t["name"] for t in m.permintaan[0]["tools"]}
    for nama in ("pratinjau_ubah_penawaran", "pratinjau_batal_penawaran", "pratinjau_jadi_pesanan",
                 "pratinjau_pelanggan_baru", "pratinjau_barang_baru"):
        assert nama not in dikirim
        assert any(t.pratinjau == nama for t in pw.TOOLS)  # masih jadi pasangan WRITE
    for nama in ("hitung_penawaran", "hitung_tanggal", "buat_penawaran", "ubah_penawaran", "tutup_kartu_tinjau"):
        assert nama in dikirim  # tool harga/tanggal TIDAK ikut disembunyikan (syarat MASTER)
