"""T196 — resolusi nama barang yang TEPAT.

U1-U3 + U10 murni/tiruan; U4-U9 + U11 memakai DB NYATA (READ-ONLY, nol tulisan).
Tes DB dilewati bila postgres tak terjangkau — jalankan dengan
`--network=milkyhoop_dev_network` supaya benar-benar dieksekusi.
"""

import asyncio

import pytest

from app.services.unified_agent.entity_resolver import EntityResolver, _norm_cocok

TENANT_G = "grapgrap-manado"
TENANT_K = "kaos-biru-konveksi"
ID_3XL = "75e4684e-91bd-4a02-b91f-6f0619b340c1"

M1 = (
    "Buat penawaran untuk Bunaken Oasis tanggal 31 Agustus 2026,\n"
    "1. Kaos 20s + Sablon Plastisol size XS-XL sebanyak 19 pcs"
)
M5 = (
    "buat penawaran untuk Toko Melati: 10 Kaos 20s + Sablon Plastisol (Size 2XL), "
    "10 Kaos 20s + Sablon Plastisol size 3XL, 10 Kaos 20s + Sablon Plastisol (Size 4XL), "
    "10 Kaos 20s + Sablon Plastisol (Size 5XL), 10 Kaos 20s + Sablon Plastisol (Size XS-XL), "
    "10 Topi Rimba Kuning Zamrud"
)

async def _pool():
    """Pool BARU per tes: asyncio_mode=auto memberi event loop baru tiap tes,
    dan pool yang di-cache akan terikat ke loop yang sudah ditutup."""
    import asyncpg

    try:
        return await asyncio.wait_for(
            asyncpg.create_pool(
                host="milkyhoop-dev-postgres-1",
                user="postgres",
                password="Proyek771977",
                database="milkydb",
                min_size=1,
                max_size=2,
            ),
            timeout=5,
        )
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"DB nyata tak terjangkau: {e}")


# ── U1-U3: _norm_cocok ────────────────────────────────────────────────────
def test_u1_norm_kurung_setara_tanpa_kurung():
    assert _norm_cocok("Kaos 20s + Sablon Plastisol (Size 3XL)") == _norm_cocok(
        "Kaos 20s + Sablon Plastisol size 3XL"
    )


def test_u2_norm_bentuk_pasti():
    assert _norm_cocok("Kaos (Size XS-XL)") == "kaos size xs xl"


def test_u3_norm_none_dan_kosong():
    assert _norm_cocok(None) == ""
    assert _norm_cocok("") == ""


# ── U4-U9, U11: DB NYATA, read-only ───────────────────────────────────────
async def test_u4_m2_step0_exact_ternormalisasi():
    r = EntityResolver(await _pool(), TENANT_G)
    res = await r._resolve_item("Kaos 20s + Sablon Plastisol (Size 3XL)", "")
    assert len(res.candidates) == 1, [c["name"] for c in res.candidates]
    assert res.confidence == 1.0
    assert res.entity_id == ID_3XL
    assert res.entity_name == "Kaos 20s + Sablon Plastisol size 3XL"


async def test_u5_m1_saringan_teks_menyempitkan():
    r = EntityResolver(await _pool(), TENANT_G)
    res = await r._resolve_item("Kaos 20s + Sablon Plastisol", M1)
    assert len(res.candidates) == 1, [c["name"] for c in res.candidates]
    assert res.entity_name == "Kaos 20s + Sablon Plastisol (Size XS-XL)"
    assert res.low_trust is False


async def test_u6_pagar_lima_nama_tidak_menyempit():
    r = EntityResolver(await _pool(), TENANT_G)
    res = await r._resolve_item("Kaos 20s + Sablon Plastisol", M5)
    assert len(res.candidates) == 5, [c["name"] for c in res.candidates]


async def test_u7_jalur_baca_tanpa_user_text():
    r = EntityResolver(await _pool(), TENANT_G)
    tanpa = await r._resolve_item("Kaos 20s + Sablon Plastisol")
    default = await r._resolve_item("Kaos 20s + Sablon Plastisol", "")
    assert [c["name"] for c in tanpa.candidates] == [
        c["name"] for c in default.candidates
    ]
    assert len(tanpa.candidates) == 5
    assert tanpa.confidence == 0.9


async def test_u8_m1_tanpa_user_text_tetap_lima():
    r = EntityResolver(await _pool(), TENANT_G)
    res = await r._resolve_item("Kaos 20s + Sablon Plastisol", "")
    assert len(res.candidates) == 5


async def test_u9_produk_terhapus_bukan_kandidat():
    r = EntityResolver(await _pool(), TENANT_K)
    res = await r._resolve_item("Kaos Uji", "")
    nama = [c["name"] for c in res.candidates]
    assert "Kaos Uji" not in nama, nama
    assert res.entity_id != "c7d7f4db-c7d6-4f70-bba2-7726c247caaa"


async def test_u11_step0_tidak_merusak_kasus_yang_sudah_benar():
    r = EntityResolver(await _pool(), TENANT_K)
    res = await r._resolve_item("Kaos Biru 30s")
    assert res.entity_name == "Kaos Biru 30s"
    assert res.entity_id == "56e66ffd-eb68-4f12-a49a-f847d6cc0e71"
    assert res.confidence == 1.0


# ── U10: pagar Step 0 (tiruan DB — tabrakan normalisasi nol di data nyata) ──
class _DbTabrakan:
    """Step 0 mengembalikan DUA baris; query berikutnya mengembalikan penanda."""

    def __init__(self):
        self.sql_dilihat = []

    async def fetch(self, sql, *args):
        self.sql_dilihat.append(sql)
        if "regexp_replace" in sql:
            return [
                {
                    "id": "aaa",
                    "nama_produk": "Kaos (A)",
                    "sales_price_amount": 1,
                    "purchase_price_amount": 1,
                    "item_type": "product",
                },
                {
                    "id": "bbb",
                    "nama_produk": "Kaos A",
                    "sales_price_amount": 1,
                    "purchase_price_amount": 1,
                    "item_type": "product",
                },
            ]
        return [
            {
                "id": "step1",
                "nama_produk": "PENANDA STEP 1",
                "sales_price_amount": 1,
                "purchase_price_amount": 1,
                "item_type": "product",
            }
        ]


async def test_u10_step0_tidak_memutuskan_saat_dua_baris():
    db = _DbTabrakan()
    res = await EntityResolver(db, "t")._resolve_item("Kaos A", "")
    assert res.entity_id == "step1", "Step 0 memutuskan padahal >= 2 baris"
    assert res.entity_name == "PENANDA STEP 1"
    assert len(db.sql_dilihat) >= 2, "alur berhenti di Step 0"


# ── U12: normalisasi (A) di loop exact-match atas `candidates` ─────────────
# Stimulus SENGAJA memaksa Step 0 mengangkat tangan (>= 2 baris ternormalisasi)
# supaya yang diuji benar-benar loop exact-match, bukan Step 0. U4 TIDAK bisa
# dipakai untuk ini sejak Step 0 ada: ia berhenti sebelum loop itu tercapai.
class _DbLoopExact:
    def __init__(self):
        self.n = 0

    async def fetch(self, sql, *args):
        self.n += 1
        baris = lambda i, nm: {  # noqa: E731
            "id": i,
            "nama_produk": nm,
            "sales_price_amount": 1,
            "purchase_price_amount": 1,
            "item_type": "product",
        }
        if "regexp_replace" in sql:
            return [baris("x", "Kaos A"), baris("y", "Kaos (A)")]
        return [baris("lain", "Kaos Zebra"), baris("benar", "Kaos A")]


async def test_u12_exact_ternormalisasi_atas_candidates():
    res = await EntityResolver(_DbLoopExact(), "t")._resolve_item("Kaos (A)", "")
    assert res.entity_id == "benar", res.entity_name
    assert res.confidence == 1.0


# ── U6b: pagar `== 1` yang BENAR-BENAR mengenai sasaran ───────────────────
# U6 (teks 5 nama) TIDAK sanggup memerahkan sabotase `== 1` -> `>= 1`: di sana
# _tersebut == rows (kelima-limanya disebut), jadi menyempitkan pun hasilnya
# tetap 5. Stimulus yang mengenai sasaran = teks yang menyebut DUA dari lima:
# _tersebut = 2 (subset sejati) -> `== 1` mempertahankan 5; `>= 1` akan
# menyempitkan ke 2 lalu mengikat yang pertama diam-diam.
M2NAMA = (
    "buat penawaran: 10 Kaos 20s + Sablon Plastisol (Size 2XL), "
    "10 Kaos 20s + Sablon Plastisol (Size 4XL)"
)


async def test_u6b_dua_nama_disebut_tetap_tidak_menyempit():
    r = EntityResolver(await _pool(), TENANT_G)
    res = await r._resolve_item("Kaos 20s + Sablon Plastisol", M2NAMA)
    assert len(res.candidates) == 5, [c["name"] for c in res.candidates]
