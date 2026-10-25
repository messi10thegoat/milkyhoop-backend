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
    "kirim_penawaran": ("schemas/quotes.py", "SendQuoteRequest", None),
    "pratinjau_kirim_penawaran": ("schemas/quotes.py", "SendQuoteRequest", None),
    "tolak_penawaran": ("schemas/quotes.py", "DeclineQuoteRequest", None),
    "pratinjau_tolak_penawaran": ("schemas/quotes.py", "DeclineQuoteRequest", None),
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


@pytest.mark.parametrize("t", [t for t in SEMUA_TOOLS if t.jalur], ids=lambda t: t.nama)
def test_rute_tool_ada_di_router_be(t):
    """Semua rute tool (termasuk pratinjau T0 dan rute aksi NAV) ada di router BE."""
    ada, berkas = _cari_rute(t.metode, t.jalur)
    assert ada, f"{t.nama}: {t.metode} {t.jalur} tak ditemukan di {berkas}"


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
    if t.nama in IZIN_SENDIRI:
        assert not cocok  # merah = sudah terpetakan: cabut dari IZIN_SENDIRI
        return
    assert cocok, f"{t.nama}: {t.metode} {j} tak terpetakan"


# ---------------------------------------------------------------- sesudah T0 (BACKEND2 master 02307237)

async def test_semua_write_aktif_tanpa_gap_sesudah_t0():
    r = Registri([pw.paket()])
    efektif = {t.nama: t for t in await r.tools_untuk(izin_semua, gap_mendarat=())}
    assert {n for n, t in efektif.items() if t.kelas == Kelas.WRITE} == {
        "buat_penawaran", "ubah_penawaran", "batalkan_penawaran", "jadikan_pesanan", "buat_pelanggan", "buat_barang",
        "kirim_penawaran", "terima_penawaran", "tolak_penawaran", "hapus_penawaran"}  # + unit QUOTES cce7facc
    assert not [t.nama for t in pw.TOOLS if t.butuh]


def test_rute_pratinjau_t0_ada_di_master():
    for nama in ("pratinjau_pelanggan_baru", "pratinjau_barang_baru"):
        t = TOOL[nama]
        ada, berkas = _cari_rute(t.metode, t.jalur)
        assert ada, f"{nama}: {t.jalur} di {berkas}"


_HANDLER_IDEM = {  # WRITE -> (berkas router, pola dekorator handler); handler WAJIB membaca kunci idempotency klien
    "buat_penawaran": ("routers/quotes.py", r'@router\.post\(\s*""'),
    "ubah_penawaran": ("routers/quotes.py", r'@router\.patch\(\s*"/\{quote_id\}"'),
    "batalkan_penawaran": ("routers/quotes.py", r'@router\.post\(\s*"/\{quote_id\}/void"'),
    "buat_pelanggan": ("routers/customers.py", r'@router\.post\(\s*""'),
    "buat_barang": ("routers/items.py", r'@router\.post\(\s*"/items"'),
    "kirim_penawaran": ("routers/quotes.py", r'@router\.post\(\s*"/\{quote_id\}/send"'),
    "terima_penawaran": ("routers/quotes.py", r'@router\.post\(\s*"/\{quote_id\}/accept"'),
    "tolak_penawaran": ("routers/quotes.py", r'@router\.post\(\s*"/\{quote_id\}/decline"'),
    "hapus_penawaran": ("routers/quotes.py", r'@router\.delete\(\s*"/\{quote_id\}"'),
}


@pytest.mark.parametrize("t", [t for t in pw.TOOLS if t.kelas == Kelas.WRITE], ids=lambda t: t.nama)
def test_write_harus_idempoten_di_be(t):
    """Setiap WRITE aktif hanya bila handler BE-nya idempoten (kunci klien, atau alami untuk to-order)."""
    if t.nama == "jadikan_pesanan":
        s = _src("routers/quotes.py")
        i = s.index("async def _konversi_penawaran(")
        badan = s[i: i + 4000]
        assert "pg_advisory_xact_lock" in badan and 'f"QUOTE:{ctx[\'tenant_id\']}:{quote_id}"' in badan
        assert '"already_converted": True' in badan
        return
    berkas, pola = _HANDLER_IDEM[t.nama]
    s = _src(berkas)
    m = re.search(pola, s)
    assert m, f"{t.nama}: dekorator tak ditemukan"
    badan = s[m.end(): m.end() + 6000]
    assert "kunci_dari(request" in badan, f"{t.nama}: handler tak membaca X-Idempotency-Key"


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


async def test_saldo_awal_disebut_pengguna_kartu_membawa_catatan_tak_dicatat():
    from app.milkybook.inti.audit import AuditMemori
    from app.milkybook.inti.putaran import Putaran, Sesi
    from .conftest import ModelPalsu, panggil, teks
    from .eval.stub_be import StubBE
    m = ModelPalsu([panggil(("buat_pelanggan", {"name": "PT Baru Makmur"})), teks("Tinjau.")])
    p = Putaran(Registri(semua_paket()), m, AuditMemori(), gap_mendarat=(pw.AS_G1, pw.AS_G14))
    ev = await p.giliran(Sesi("s", "t", "u"), "daftarin PT Baru Makmur, saldo piutang awalnya 2jt", StubBE(), izin_semua)
    k = [e for e in ev if e["jenis"] == "kartu"][0]
    assert k["tak_dicatat"] and "Saldo awal" in k["tak_dicatat"][0]
    assert "tidak_dicatat" in m.permintaan[1]["messages"][-1]["content"][0]["content"]
    m2 = ModelPalsu([panggil(("buat_pelanggan", {"name": "PT Lain"})), teks("Tinjau.")])
    p2 = Putaran(Registri(semua_paket()), m2, AuditMemori(), gap_mendarat=(pw.AS_G1, pw.AS_G14))
    ev2 = await p2.giliran(Sesi("s", "t", "u"), "daftarin PT Lain", StubBE(), izin_semua)
    assert "tak_dicatat" not in [e for e in ev2 if e["jenis"] == "kartu"][0]


def test_kirim_tanpa_send_email_dan_pratinjau_quotes_baca_saja():
    """Surel otomatis belum tersedia (pratinjau memblok send_email=true): model tak diberi medan itu. Pratinjau unit
    QUOTES (cce7facc) dikunci baca saja: READ ONLY / kunci=False; jalur tulis membaca kunci idem."""
    assert set(TOOL["kirim_penawaran"].skema["properties"]) == {"id"}
    q = _src("routers/quotes.py")
    for rute, penanda in (("/{quote_id}/send/preview", "kunci=False"), ("/{quote_id}/accept/preview", "_pratinjau_terima_tolak"),
                          ("/{quote_id}/decline/preview", "_pratinjau_terima_tolak"), ("/{quote_id}/delete/preview", "readonly=True")):
        i = q.index(f'@router.post("{rute}")')
        assert penanda in q[i: i + 1500], rute
    i = q.index("async def _pratinjau_terima_tolak(")
    assert "kunci=False" in q[i: i + 600]
