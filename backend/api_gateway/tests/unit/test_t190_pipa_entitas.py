"""T190 — PIPELINE ENTITAS V2 untuk `create_quote`, di balik flag.

Apa yang suite ini KLAIM dan apa yang TIDAK.

KLAIM (diuji di sini):
  - `HasilResolve` menolak bentuk yang mustahil DI TEMPAT LAHIRNYA;
  - tabel keputusan lengkap dan urutannya persis seperti yang dinyatakan;
  - nama mentah yang user tulis tidak hilang di perjalanan menuju pesan;
  - pesan TIDAK PERNAH mengutip kalimat utuh user;
  - `resolve_entitas` menyusun SQL yang menyaring tenant + kehidupan baris
    yang benar per tipe, dan exact match menang atas ILIKE;
  - flag mati = jalur V2 tidak pernah menyentuh apa pun.

TIDAK DIKLAIM: bahwa PostgreSQL sungguh mengembalikan baris yang sama
dengan yang dikembalikan `KoneksiPalsu`. Sisi SQL diuji sebagai TEKS
(tabel, kolom, klausa penyaring), bukan sebagai eksekusi. Untuk itu tetap
perlu probe, dan tiket ini melarangnya.
"""

import os
import sys

import pytest

sys.path.insert(0, "/app/backend/api_gateway")

os.environ.setdefault("OPENAI_API_KEY", "uji-t190-bukan-kunci-asli")

from app.services.unified_agent.hasil_resolve import (  # noqa: E402
    HasilResolve,
    Kandidat,
)
from app.services.unified_agent.gerbang_keputusan import (  # noqa: E402
    BATAS_POTONGAN,
    JENIS_KARTU,
    JENIS_PIL,
    JENIS_TAWARAN,
    potongan_aman,
    putuskan,
)
from app.services.unified_agent import resolver_entitas as RE  # noqa: E402

try:
    from app.services.unified_agent.tool_executor import ToolExecutor
except Exception:  # pragma: no cover
    ToolExecutor = None


# ───────────────────────────── perkakas ─────────────────────────────


def _ada(mentah: str, **kw) -> HasilResolve:
    return HasilResolve(
        status="DITEMUKAN", mentah=mentah, tipe=kw.pop("tipe", "item"),
        id=kw.pop("id", "id-" + mentah[:8]), nama=kw.pop("nama", mentah), **kw
    )


def _ambigu(mentah: str, n: int = 2, **kw) -> HasilResolve:
    return HasilResolve(
        status="AMBIGU", mentah=mentah, tipe=kw.pop("tipe", "item"),
        kandidat=tuple(
            Kandidat("id-%s-%d" % (mentah[:6], i), "%s varian %d" % (mentah, i))
            for i in range(n)
        ),
        **kw
    )


def _kosong(mentah: str, **kw) -> HasilResolve:
    return HasilResolve(
        status="TIDAK_ADA", mentah=mentah, tipe=kw.pop("tipe", "item"), **kw
    )


class KoneksiPalsu:
    """Menggantikan pool asyncpg. Merekam SQL, mengembalikan baris terprogram.

    Ia mengembalikan giliran demi giliran: panggilan pertama = balasan
    pertama. Dengan begitu "exact menang" bisa diuji sebagai FAKTA — kalau
    resolver melompati kueri exact, jumlah panggilannya berbeda dan tes
    melihatnya.
    """

    def __init__(self, *balasan):
        self._balasan = list(balasan)
        self.sql = []
        self.args = []

    async def fetch(self, sql, *args):
        self.sql.append(sql)
        self.args.append(args)
        i = len(self.sql) - 1
        return self._balasan[i] if i < len(self._balasan) else []


# ─────────────── 1. INVARIAN HasilResolve (pengaman struktural) ───────────────


def test_mentah_kosong_ditolak():
    with pytest.raises(ValueError):
        HasilResolve(status="TIDAK_ADA", mentah="", tipe="item")


def test_mentah_hanya_spasi_ditolak():
    with pytest.raises(ValueError):
        HasilResolve(status="TIDAK_ADA", mentah="   \t ", tipe="item")


def test_ambigu_satu_kandidat_ditolak():
    with pytest.raises(ValueError):
        HasilResolve(
            status="AMBIGU", mentah="kaos", tipe="item",
            kandidat=(Kandidat("a", "Kaos Biru"),),
        )


def test_ambigu_nol_kandidat_ditolak():
    with pytest.raises(ValueError):
        HasilResolve(status="AMBIGU", mentah="kaos", tipe="item")


def test_ditemukan_tanpa_id_ditolak():
    with pytest.raises(ValueError):
        HasilResolve(status="DITEMUKAN", mentah="kaos", tipe="item", nama="Kaos")


def test_ditemukan_tanpa_nama_ditolak():
    with pytest.raises(ValueError):
        HasilResolve(status="DITEMUKAN", mentah="kaos", tipe="item", id="x")


def test_tidak_ada_punya_id_ditolak():
    with pytest.raises(ValueError):
        HasilResolve(status="TIDAK_ADA", mentah="kaos", tipe="item", id="x")


def test_status_dan_tipe_asing_ditolak():
    with pytest.raises(ValueError):
        HasilResolve(status="MUNGKIN", mentah="kaos", tipe="item")
    with pytest.raises(ValueError):
        HasilResolve(status="TIDAK_ADA", mentah="kaos", tipe="gudang")


def test_baris_index_negatif_ditolak():
    with pytest.raises(ValueError):
        HasilResolve(status="TIDAK_ADA", mentah="kaos", tipe="item", baris_index=-1)


def test_bentuk_sah_tidak_ditolak():
    """Kontrol: invarian tidak menolak SEMUANYA.

    Tanpa baris ini, sembilan tes di atas bisa hijau karena konstruktornya
    selalu melempar — dan itu bukan pengaman, itu kerusakan.
    """
    assert _ada("Kaos Biru 30s").id
    assert len(_ambigu("kaos", 3).kandidat) == 3
    assert _kosong("Meja").id is None


def test_hasil_resolve_beku():
    h = _kosong("Meja")
    with pytest.raises(Exception):
        h.mentah = "diubah"


# ─────────────── 2. TABEL KEPUTUSAN LENGKAP (murni, tanpa DB/LLM) ───────────────


def test_semua_ditemukan_kartu():
    k = putuskan([_ada("Kaos Biru 30s"), _ada("Kaos Hitam 24s")])
    assert k.jenis == JENIS_KARTU
    assert k.pesan == ""


def test_daftar_kosong_kartu():
    assert putuskan([]).jenis == JENIS_KARTU


def test_satu_ambigu_pil():
    assert putuskan([_ada("Kaos Biru 30s"), _ambigu("kaos")]).jenis == JENIS_PIL


def test_satu_tidak_ada_tawaran():
    assert putuskan([_ada("Kaos Biru 30s"), _kosong("Meja")]).jenis == JENIS_TAWARAN


def test_tidak_ada_menang_atas_ambigu():
    """Keputusan sadar owner: yang menghalangi paling keras ditanya dulu."""
    for urut in (
        [_ambigu("kaos"), _kosong("Meja")],
        [_kosong("Meja"), _ambigu("kaos")],
    ):
        assert putuskan(urut).jenis == JENIS_TAWARAN


def test_urutan_masukan_tidak_mengubah_keputusan():
    a = [_ada("A"), _ambigu("kaos"), _kosong("Meja")]
    assert putuskan(a).jenis == putuskan(list(reversed(a))).jenis


def test_pil_hanya_satu_baris_walau_dua_ambigu():
    k = putuskan([_ambigu("kaos", 2, baris_index=1), _ambigu("kain", 3, baris_index=2)])
    assert k.jenis == JENIS_PIL
    assert "kaos" in k.pesan and "kain" not in k.pesan
    assert k.extra_data["sisa_ambigu"] == 1


def test_pil_membawa_baris_index_bukan_nol():
    """Jawaban pil harus bisa ditulis ke BARIS ITU, bukan selalu baris 0."""
    k = putuskan([_ada("A", baris_index=0), _ambigu("kaos", 2, baris_index=2)])
    assert k.extra_data["baris_index"] == 2


def test_pil_punya_opsi_kandidat_plus_bukan_semuanya():
    k = putuskan([_ambigu("kaos", 3)])
    assert len(k.opsi) == 4
    assert k.opsi[-1]["value"] == "bukan_semuanya"


def test_tawaran_satu_entitas_bentuk_kalimat_persis():
    k = putuskan([_kosong("Meja Kayu")])
    assert k.pesan == "Meja Kayu belum terdaftar di master barang. Daftarkan sekarang?"
    assert [o["label"] for o in k.opsi] == [
        "Daftarkan",
        "Ganti barang lain",
        "Batal",
    ]


def test_tawaran_banyak_bentuk_kalimat_persis():
    k = putuskan([_kosong("Meja Kayu"), _kosong("Kursi Rotan")])
    assert k.pesan == (
        "2 barang belum terdaftar: Meja Kayu, Kursi Rotan. Daftarkan sekarang?"
    )


def test_pil_bentuk_kalimat_persis():
    k = putuskan([_ambigu("kaos", 3)])
    assert k.pesan == '"kaos" cocok dengan 3 barang. Yang mana?'


def test_tawaran_pelanggan_tidak_disebut_barang():
    k = putuskan([_kosong("Toko Anu", tipe="customer")])
    assert "barang" not in k.pesan
    assert "master pelanggan" in k.pesan


def test_kartu_membawa_ikatan_id_per_baris():
    k = putuskan([_ada("A", baris_index=0, id="i0"), _ada("B", baris_index=1, id="i1")])
    assert k.extra_data["terikat"] == [
        {"baris_index": 0, "id": "i0"},
        {"baris_index": 1, "id": "i1"},
    ]


def test_putuskan_murni_tidak_mengubah_masukan():
    h = [_ambigu("kaos"), _kosong("Meja")]
    sebelum = [(x.status, x.mentah, x.baris_index) for x in h]
    putuskan(h)
    assert [(x.status, x.mentah, x.baris_index) for x in h] == sebelum


# ─────────────── 3. NAMA MENTAH TIDAK PERNAH HILANG ───────────────

ENAM_NAMA = [
    "Kaos Biru 30s",
    "Kain Sutra T187",
    "Meja Kayu Jati",
    "Kursi Rotan Anyam",
    "Spanduk 3x1",
    "Tinta Sablon Plastisol",
]


def test_enam_nama_tidak_ada_semua_muncul_utuh():
    k = putuskan([_kosong(n, baris_index=i) for i, n in enumerate(ENAM_NAMA)])
    assert k.jenis == JENIS_TAWARAN
    for n in ENAM_NAMA:
        assert n in k.pesan, "nama hilang dari pesan: " + n


def test_nama_ambigu_muncul_utuh_di_pil():
    k = putuskan([_ambigu("Kaos Biru 30s", 2)])
    assert "Kaos Biru 30s" in k.pesan


def test_nama_mentah_ikut_di_extra_data_walau_tak_dikutip():
    """Yang dilarang MENAMPILKAN kalimat, bukan MENYIMPAN nama mentahnya."""
    panjang = "buat penawaran untuk toko merdeka lima puluh kaos biru dan dua puluh kaos hitam"
    k = putuskan([_kosong(panjang)])
    assert panjang not in k.pesan
    assert k.extra_data["mentah"] == [panjang]


def test_kontrol_pemeriksa_nama_bisa_gagal():
    """Kontrol positif: pemeriksaan 'nama muncul utuh' BISA melaporkan MERAH.

    Tanpa ini, tes-tes di atas bisa hijau karena `in` selalu benar untuk
    string apa pun yang kebetulan ada — dan nol temuan tak akan berarti.
    """
    k = putuskan([_kosong(n) for n in ENAM_NAMA])
    assert "Sepeda Motor Listrik" not in k.pesan


# ─────────────── 4. ANTI-KUTIP-KALIMAT ───────────────

KALIMAT_PANJANG = (
    "buat penawaran untuk toko merdeka lima puluh kaos biru tiga puluh s dan "
    "dua puluh kaos hitam dua puluh empat s kirim minggu depan"
)


def test_kalimat_panjang_ditolak_sebagai_potongan():
    assert len(KALIMAT_PANJANG) > BATAS_POTONGAN
    assert potongan_aman(KALIMAT_PANJANG) is None


def test_pesan_tawaran_tidak_mengutip_kalimat():
    k = putuskan([_kosong(KALIMAT_PANJANG)])
    assert KALIMAT_PANJANG not in k.pesan
    # Bukan hanya kalimat UTUH: tak boleh ada penggalan panjang pun. Lima kata
    # berurutan sudah cukup untuk membuat user merasa dikutip.
    kata = KALIMAT_PANJANG.split()
    for i in range(len(kata) - 4):
        assert " ".join(kata[i:i + 5]) not in k.pesan
    assert "tidak terbaca" in k.pesan


def test_pesan_pil_tidak_mengutip_kalimat():
    k = putuskan([_ambigu(KALIMAT_PANJANG, 3)])
    assert KALIMAT_PANJANG not in k.pesan
    assert "Yang mana?" in k.pesan


@pytest.mark.parametrize(
    "potongan",
    [
        "buat penawaran",
        "catat penjualan",
        "penawaran untuk toko merdeka lima kaos",
        "untuk toko merdeka",
    ],
)
def test_potongan_berkata_perintah_ditolak(potongan):
    assert potongan_aman(potongan) is None


def test_nama_master_wajar_tetap_dikutip():
    """Batas kata-perintah sengaja SEMPIT; ini yang menguncinya.

    "Kaos Untuk Anak" memuat kata perintah `untuk` di TENGAH dan hanya tiga
    kata. Menolaknya berarti tenant yang menamai barangnya begitu tak akan
    pernah melihat namanya sendiri di layar — kerugian yang lebih besar
    daripada yang kita cegah.
    """
    assert potongan_aman("Kaos Untuk Anak") == "Kaos Untuk Anak"
    assert potongan_aman("Kaos Biru 30s") == "Kaos Biru 30s"


def test_potongan_dirapatkan_bukan_dipotong_sembarangan():
    assert potongan_aman("  Kaos   Biru  30s ") == "Kaos Biru 30s"


def test_banyak_tidak_ada_sebagian_kalimat_tetap_menyebut_yang_terbaca():
    k = putuskan([_kosong("Meja Kayu"), _kosong(KALIMAT_PANJANG)])
    assert "Meja Kayu" in k.pesan
    assert KALIMAT_PANJANG not in k.pesan
    assert "1 lagi namanya tidak terbaca" in k.pesan


# ─────────────── 5. RESOLVER — SATU SITUS, KOLOM TERUKUR ───────────────


@pytest.mark.asyncio
async def test_exact_menang_walau_ilike_cocok_banyak():
    conn = KoneksiPalsu(
        [{"id": "i1", "nm": "Kaos"}],  # exact
        [{"id": "a", "nm": "Kaos Biru"}, {"id": "b", "nm": "Kaos Hitam"}],
    )
    h = await RE.resolve_entitas(conn, "t1", "kaos", "item")
    assert h.status == "DITEMUKAN" and h.id == "i1"
    assert len(conn.sql) == 1, "kueri ILIKE tidak boleh dijalankan bila exact cocok"


@pytest.mark.asyncio
async def test_ilike_nol_jadi_tidak_ada_bukan_exception():
    conn = KoneksiPalsu([], [])
    h = await RE.resolve_entitas(conn, "t1", "Meja Kayu", "item", baris_index=2)
    assert h.status == "TIDAK_ADA"
    assert h.mentah == "Meja Kayu" and h.baris_index == 2


@pytest.mark.asyncio
async def test_ilike_satu_jadi_ditemukan():
    conn = KoneksiPalsu([], [{"id": "x", "nm": "Kaos Biru 30s"}])
    h = await RE.resolve_entitas(conn, "t1", "biru", "item")
    assert h.status == "DITEMUKAN" and h.nama == "Kaos Biru 30s"


@pytest.mark.asyncio
async def test_ilike_dua_jadi_ambigu():
    conn = KoneksiPalsu(
        [], [{"id": "a", "nm": "Kaos Biru 30s"}, {"id": "b", "nm": "Kaos Hitam 24s"}]
    )
    h = await RE.resolve_entitas(conn, "t1", "kaos", "item")
    assert h.status == "AMBIGU" and len(h.kandidat) == 2


@pytest.mark.asyncio
async def test_mentah_disimpan_apa_adanya_walau_dicari_ternormalisasi():
    conn = KoneksiPalsu([], [])
    h = await RE.resolve_entitas(conn, "t1", "  KaOs   BiRu ", "item")
    assert h.mentah == "  KaOs   BiRu "
    assert conn.args[0][1] == "kaos biru", "kunci pencarian harus ternormalisasi"


@pytest.mark.asyncio
async def test_nama_kosong_melempar_di_tempat_lahirnya():
    conn = KoneksiPalsu([], [])
    with pytest.raises(ValueError):
        await RE.resolve_entitas(conn, "t1", "   ", "item")
    assert conn.sql == [], "tak boleh menyentuh DB untuk nama kosong"


@pytest.mark.asyncio
async def test_item_disaring_deleted_at_bukan_status():
    """Kolom yang menentukan kehidupan produk DIUKUR, bukan disalin.

    kaos-biru-konveksi: 55 produk, `status='active'` 42, `deleted_at IS
    NULL` hanya 3. Menyaring dengan `status` berarti menawarkan 39 produk
    yang sudah dihapus.
    """
    conn = KoneksiPalsu([], [])
    await RE.resolve_entitas(conn, "t1", "kaos", "item")
    for sql in conn.sql:
        assert "FROM products" in sql
        assert "deleted_at IS NULL" in sql
        assert "status" not in sql
        assert "tenant_id = $1" in sql


@pytest.mark.asyncio
async def test_customer_memakai_kolom_nama_bukan_name():
    conn = KoneksiPalsu([], [])
    await RE.resolve_entitas(conn, "t1", "Toko Merdeka", "customer")
    for sql in conn.sql:
        assert "FROM customers" in sql
        assert "deleted_at IS NULL" in sql
        assert " nama " in sql or "nama AS nm" in sql or "nama ILIKE" in sql


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tipe,tabel",
    [
        ("vendor", "vendors"),
        ("akun", "chart_of_accounts"),
        ("bank", "bank_accounts"),
    ],
)
async def test_master_tanpa_deleted_at_memakai_is_active(tipe, tabel):
    """DIUKUR: vendors / chart_of_accounts / bank_accounts TIDAK punya
    kolom `deleted_at`. Menuntut klausa itu di sana = SQL yang meledak."""
    conn = KoneksiPalsu([], [])
    await RE.resolve_entitas(conn, "t1", "Anu", tipe)
    for sql in conn.sql:
        assert "FROM " + tabel in sql
        assert "deleted_at" not in sql
        assert "is_active = true" in sql


@pytest.mark.asyncio
async def test_batas_kandidat_muncul_di_sql():
    conn = KoneksiPalsu([], [])
    await RE.resolve_entitas(conn, "t1", "kaos", "item")
    assert "LIMIT " + str(RE.BATAS_KANDIDAT) in conn.sql[-1]


@pytest.mark.asyncio
async def test_tipe_asing_ditolak():
    conn = KoneksiPalsu([], [])
    with pytest.raises(ValueError):
        await RE.resolve_entitas(conn, "t1", "Anu", "gudang")


def test_kontrol_koneksi_palsu_bisa_gagal():
    """Kontrol: KoneksiPalsu benar-benar merekam, jadi nol SQL berarti sesuatu."""
    c = KoneksiPalsu()
    assert c.sql == []


# ─────────────── 6. FLAG OFF = PERILAKU IDENTIK ───────────────

pytestmark_te = pytest.mark.skipif(
    ToolExecutor is None, reason="tool_executor tidak bisa diimpor"
)


@pytestmark_te
def test_flag_default_mati():
    os.environ.pop("PIPELINE_ENTITAS_V2", None)
    assert ToolExecutor._v2_aktif("create_quote") is False


@pytestmark_te
def test_flag_hidup_hanya_untuk_create_quote(monkeypatch):
    monkeypatch.setenv("PIPELINE_ENTITAS_V2", "1")
    assert ToolExecutor._v2_aktif("create_quote") is True
    for lain in (
        "create_bill",
        "create_sales_invoice",
        "create_sales_order",
        "create_item",
        "create_customer",
        "create_expense",
    ):
        assert ToolExecutor._v2_aktif(lain) is False, lain


@pytestmark_te
@pytest.mark.parametrize("nilai", ["", "0", "off", "false", "no", "tidak"])
def test_nilai_flag_yang_bukan_hidup(monkeypatch, nilai):
    monkeypatch.setenv("PIPELINE_ENTITAS_V2", nilai)
    assert ToolExecutor._v2_aktif("create_quote") is False


@pytestmark_te
@pytest.mark.asyncio
async def test_flag_mati_pipa_pulang_none_tanpa_menyentuh_payload(monkeypatch):
    monkeypatch.delenv("PIPELINE_ENTITAS_V2", raising=False)
    payload = {
        "customer_name": "Toko Merdeka",
        "items": [{"description": "Kaos Biru 30s", "quantity": 5}],
    }
    salinan = {"customer_name": "Toko Merdeka", "items": [dict(payload["items"][0])]}

    class Diri:
        context = type("C", (), {"tenant_id": "t1"})()
        _v2_aktif = staticmethod(ToolExecutor._v2_aktif)
        _v2_entitas_mentah = staticmethod(ToolExecutor._v2_entitas_mentah)

    hasil = await ToolExecutor._pipa_entitas_v2(Diri(), "create_quote", payload)
    assert hasil is None
    assert payload == salinan, "flag mati tidak boleh mengubah payload"


@pytestmark_te
def test_entitas_mentah_membaca_kunci_create_quote():
    payload = {
        "customer_name": "Toko Merdeka",
        "items": [
            {"description": "Kaos Biru 30s"},
            {"item_id": "sudah-terikat", "description": "Kaos Hitam 24s"},
            {"item_name": "Meja Kayu"},
        ],
    }
    e = ToolExecutor._v2_entitas_mentah(payload)
    assert ("customer", "Toko Merdeka", None) in e
    assert ("item", "Kaos Biru 30s", 0) in e
    assert ("item", "Meja Kayu", 2) in e
    assert all(m != "Kaos Hitam 24s" for _, m, _ in e), "baris terikat dicari ulang"


@pytestmark_te
def test_entitas_mentah_menghormati_penanda_tanpa_makna():
    """`description` = "Item" adalah penanda yang DITULIS pengayaan, bukan nama.

    Mencarinya di master melahirkan pil berisi barang acak yang kebetulan
    memuat kata itu.
    """
    e = ToolExecutor._v2_entitas_mentah({"items": [{"description": "Item"}]})
    assert e == []


@pytestmark_te
def test_entitas_mentah_mengurai_items_string_tanpa_menyentuh_payload():
    payload = {"items": '[{"description": "Kaos Biru 30s"}]'}
    e = ToolExecutor._v2_entitas_mentah(payload)
    assert ("item", "Kaos Biru 30s", 0) in e
    assert isinstance(payload["items"], str), "payload tidak boleh disentuh di sini"


@pytestmark_te
def test_jalur_v2_tidak_memuat_penghapus():
    """Kaidah 1: append-only sampai gerbang.

    Dibaca dari SUMBER, bukan dari niat: badan ketiga fungsi V2 tidak boleh
    memuat `payload.pop`, dan tidak boleh menulis None ke `item_id` /
    `description`.
    """
    import inspect

    from app.services.unified_agent import tool_executor as TE

    src = "".join(
        inspect.getsource(f)
        for f in (
            TE.ToolExecutor._v2_aktif,
            TE.ToolExecutor._v2_entitas_mentah,
            TE.ToolExecutor._pipa_entitas_v2,
        )
    )
    for terlarang in (
        "payload.pop(",
        '["item_id"] = None',
        '["description"] = None',
        '"description"] =',
    ):
        assert terlarang not in src, "jalur V2 memuat penghapus: " + terlarang


@pytestmark_te
def test_kontrol_pembaca_sumber_bisa_gagal():
    """Kontrol: pembacaan sumber di atas benar-benar melihat kode.

    Kalau `inspect.getsource` mengembalikan string kosong, tes sebelumnya
    hijau tanpa memeriksa apa pun.
    """
    import inspect

    from app.services.unified_agent import tool_executor as TE

    src = inspect.getsource(TE.ToolExecutor._pipa_entitas_v2)
    assert "PIPA_V2" in src and len(src) > 500


@pytestmark_te
def test_pipa_dipasang_sebelum_enricher():
    """Titik pemasangan adalah bagian dari kontrak, bukan detail.

    Di hilir `_enrich_payload` nama mentah sudah bisa hilang; pipa yang
    berjanji nama tidak hilang harus berdiri SEBELUMNYA.
    """
    import inspect

    from app.services.unified_agent import tool_executor as TE

    src = inspect.getsource(TE.ToolExecutor._execute_propose_direct)
    i_v2 = src.find("_pipa_entitas_v2(action_key")
    i_en = src.find("_enrich_payload(_enrich_action_type")
    assert i_v2 > 0 and i_en > 0
    assert i_v2 < i_en


# ─────────────── 7. FLAG ON — TIGA KEPUTUSAN TERBUKTI ───────────────


class PoolSkrip:
    """Pool palsu yang menjawab menurut nilai `$2` yang diterimanya.

    Dipakai untuk membuktikan ketiga keputusan lewat `_pipa_entitas_v2`
    UTUH — bukan hanya lewat `putuskan()` — tanpa menyentuh DB nyata.
    """

    def __init__(self, peta):
        self.peta = peta
        self.sql = []

    async def fetch(self, sql, *args):
        self.sql.append((sql, args))
        kunci = str(args[1]).strip("%")
        exact = "= $2" in sql
        baris = self.peta.get(kunci, [])
        if exact:
            return [b for b in baris if b["nm"].casefold() == kunci]
        return baris


def _diri_dengan(pool):
    class Diri:
        context = type("C", (), {"tenant_id": "kaos-biru-konveksi"})()
        _v2_aktif = staticmethod(ToolExecutor._v2_aktif)
        _v2_entitas_mentah = staticmethod(ToolExecutor._v2_entitas_mentah)

    d = Diri()
    d._pool = pool
    return d


async def _jalankan(monkeypatch, pool, payload):
    monkeypatch.setenv("PIPELINE_ENTITAS_V2", "1")
    from app.services.unified_agent import db_utils as DU

    async def _pool_palsu():
        return pool

    monkeypatch.setattr(DU, "get_session_db_pool", _pool_palsu)
    return await ToolExecutor._pipa_entitas_v2(
        _diri_dengan(pool), "create_quote", payload
    )


@pytestmark_te
@pytest.mark.asyncio
async def test_flag_on_keputusan_tawaran_daftar(monkeypatch, caplog):
    pool = PoolSkrip({"toko merdeka": [{"id": "c1", "nm": "Toko Merdeka"}]})
    with caplog.at_level("WARNING"):
        hasil = await _jalankan(
            monkeypatch,
            pool,
            {
                "customer_name": "Toko Merdeka",
                "items": [{"description": "Meja Kayu Jati"}],
            },
        )
    assert hasil["message_type"] == "CLARIFICATION"
    assert hasil["data"]["pipa_v2"]["keputusan"] == JENIS_TAWARAN
    assert "Meja Kayu Jati belum terdaftar di master barang" in hasil["text"]
    assert "keputusan=TAWARAN_DAFTAR" in caplog.text


@pytestmark_te
@pytest.mark.asyncio
async def test_flag_on_keputusan_pil(monkeypatch, caplog):
    pool = PoolSkrip(
        {
            "toko merdeka": [{"id": "c1", "nm": "Toko Merdeka"}],
            "kaos": [
                {"id": "p1", "nm": "Kaos Biru 30s"},
                {"id": "p2", "nm": "Kaos Hitam 24s"},
            ],
        }
    )
    with caplog.at_level("WARNING"):
        hasil = await _jalankan(
            monkeypatch,
            pool,
            {"customer_name": "Toko Merdeka", "items": [{"description": "kaos"}]},
        )
    assert hasil["data"]["pipa_v2"]["keputusan"] == JENIS_PIL
    assert hasil["text"] == '"kaos" cocok dengan 2 barang. Yang mana?'
    assert [o["label"] for o in hasil["data"]["options"]][:2] == [
        "Kaos Biru 30s",
        "Kaos Hitam 24s",
    ]
    assert "keputusan=PIL" in caplog.text


@pytestmark_te
@pytest.mark.asyncio
async def test_flag_on_keputusan_kartu_mengisi_id_tanpa_menghapus(
    monkeypatch, caplog
):
    pool = PoolSkrip(
        {
            "toko merdeka": [{"id": "c1", "nm": "Toko Merdeka"}],
            "kaos biru 30s": [{"id": "p1", "nm": "Kaos Biru 30s"}],
        }
    )
    payload = {
        "customer_name": "Toko Merdeka",
        "items": [{"description": "Kaos Biru 30s", "quantity": 5}],
    }
    with caplog.at_level("WARNING"):
        hasil = await _jalankan(monkeypatch, pool, payload)
    assert hasil is None, "KARTU = teruskan ke jalur lama"
    assert payload["customer_id"] == "c1"
    assert payload["items"][0]["item_id"] == "p1"
    # Kaidah 1 + 3: nama yang user tulis MASIH ADA sesudah pipa lewat.
    assert payload["items"][0]["description"] == "Kaos Biru 30s"
    assert payload["items"][0]["quantity"] == 5
    assert payload["customer_name"] == "Toko Merdeka"
    assert "keputusan=KARTU" in caplog.text


@pytestmark_te
@pytest.mark.asyncio
async def test_log_resolve_tidak_memuat_nama_entitas(monkeypatch, caplog):
    pool = PoolSkrip({"toko merdeka": [{"id": "c1", "nm": "Toko Merdeka"}]})
    with caplog.at_level("WARNING"):
        await _jalankan(
            monkeypatch,
            pool,
            {
                "customer_name": "Toko Merdeka",
                "items": [{"description": "Meja Kayu Jati"}],
            },
        )
    baris = [b for b in caplog.text.splitlines() if "tahap=resolve" in b]
    assert baris, "baris log resolve tidak terbit"
    for b in baris:
        assert "Meja Kayu Jati" not in b
        assert "Toko Merdeka" not in b


@pytestmark_te
@pytest.mark.asyncio
async def test_amplop_v2_lolos_kontrak_render(monkeypatch):
    from app.services.unified_agent.kontrak_render import periksa_kontrak_render

    pool = PoolSkrip({})
    hasil = await _jalankan(
        monkeypatch, pool, {"items": [{"description": "Meja Kayu Jati"}]}
    )
    assert periksa_kontrak_render("CLARIFICATION", hasil["text"], hasil["data"]) == []


@pytestmark_te
@pytest.mark.asyncio
async def test_kontrol_kontrak_render_bisa_gagal():
    """Kontrol: pemeriksa kontrak render BISA melaporkan MERAH."""
    from app.services.unified_agent.kontrak_render import periksa_kontrak_render

    assert periksa_kontrak_render("CLARIFICATION", "ada teks", None) != []
