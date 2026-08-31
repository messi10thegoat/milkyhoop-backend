"""GERBANG ENTITAS FASE 1a — gate unit.

Yang diuji: untuk `create_bill` SAJA, kartu konfirmasi TIDAK dibangun bila
vendor atau barangnya belum terdaftar (id kosong). Aksi lain tidak berubah —
terutama create_customer / create_vendor / create_item, yang MEMBUAT
entitasnya sehingga id kosong adalah keadaan yang BENAR; menolaknya =
mematikan pendaftaran entitas.

Dua lapis:
  A. fungsi murni `periksa_gerbang_entitas` (matriks radius + bentuk amplop);
  B. `_execute_propose_direct` sungguhan, dengan tiga tahap pra-gerbang
     dinetralkan dan pool DB diganti PELEDAK — supaya "tidak menyentuh DB"
     terbukti, bukan diasumsikan. Peledak itu sekaligus KONTROL POSITIF:
     pada kasus yang HARUS lolos gerbang, peledak MELEDAK.
"""
import sys

import pytest

sys.path.insert(0, "/app/backend/api_gateway")

from app.services.unified_agent.gerbang_entitas import (  # noqa: E402
    AKSI_DIGERBANG,
    KODE_GERBANG,
    PETA_AKSI,
    periksa_gerbang_entitas,
)


# ══════════════════ A. fungsi murni ══════════════════

def _bill(vendor_id=None, vendor_name="Knitto Textile", items=None):
    return {
        "vendor_id": vendor_id,
        "vendor_name": vendor_name,
        # Penamaan payload Bill = product_name/price/product_id.
        "items": items
        if items is not None
        else [{"product_name": "Kain Katun", "qty": 10, "price": 50000,
               "product_id": "11111111-1111-1111-1111-111111111111"}],
    }


def _jual(customer_id="55555555-5555-5555-5555-555555555555",
          customer_name="Toko Melati", items=None):
    """Amplop Quote/SO/SI: customer_id/customer_name + items[].item_id.

    SENGAJA beda dari _bill: menyalin amplop Bill ke aksi penjualan adalah
    cara rapi melahirkan tiga bug sekaligus.
    """
    return {
        "customer_id": customer_id,
        "customer_name": customer_name,
        # Tanggal WAJIB diisi: tanpa ini validate_payload menolak lebih dulu
        # dan KONTROL POSITIF jadi palsu — lolos tanpa pernah mencapai pagar.
        "quote_date": "2026-08-31",
        "invoice_date": "2026-08-31",
        "order_date": "2026-08-31",
        "due_date": "2026-09-30",
        "items": items
        if items is not None
        else [{"description": "Kain Katun", "quantity": 2, "unit_price": 1000,
               "item_id": "44444444-4444-4444-4444-444444444444"}],
    }


AKSI_JUAL = ["create_quote", "create_sales_invoice", "create_sales_order"]


@pytest.mark.parametrize("action_key", AKSI_JUAL)
def test_jual_customer_tak_ter_resolve_diblokir(action_key):
    hasil = periksa_gerbang_entitas(
        action_key, _jual(customer_id="create_new:Toko Melati")
    )
    assert hasil is not None, "pelanggan tak ter-resolve tapi kartu dibangun"
    assert hasil["message_type"] == "CLARIFICATION"
    assert "Toko Melati" in hasil["content"]
    assert "pelanggan" in hasil["content"].lower()
    assert "vendor" not in hasil["content"].lower(), (
        "aksi penjualan memakai label vendor - peta field tertukar"
    )


@pytest.mark.parametrize("action_key", AKSI_JUAL)
def test_jual_item_yatim_diblokir(action_key):
    hasil = periksa_gerbang_entitas(
        action_key,
        _jual(items=[{"description": "Benang Jahit", "unit_price": 2000}]),
    )
    assert hasil is not None
    assert "Benang Jahit" in hasil["content"]


@pytest.mark.parametrize("action_key", AKSI_JUAL)
def test_jual_semua_ter_resolve_lolos(action_key):
    """Kontrol positif: pagar tidak menelan kasus yang sah."""
    assert periksa_gerbang_entitas(action_key, _jual()) is None


@pytest.mark.parametrize("action_key", AKSI_JUAL)
def test_jual_pesan_diferensial_barang_tidak_menyebut_pelanggan(action_key):
    """WAJIB DIFERENSIAL: kalau hanya barang yang kurang, pesan TIDAK boleh
    menyebut pelanggan — dan sebaliknya. Kalimat buram membuat user
    memperbaiki hal yang tidak rusak."""
    h_barang = periksa_gerbang_entitas(
        action_key, _jual(items=[{"description": "Benang Jahit"}])
    )
    assert "Benang Jahit" in h_barang["content"]
    assert "Toko Melati" not in h_barang["content"]
    assert "pelanggan" not in h_barang["content"].lower()

    h_pihak = periksa_gerbang_entitas(
        action_key, _jual(customer_id="create_new:Toko Melati")
    )
    assert "Toko Melati" in h_pihak["content"]
    assert "master barang" not in h_pihak["content"]


@pytest.mark.parametrize("action_key", AKSI_JUAL)
def test_jual_customer_id_terisi_tapi_item_id_kosong_bukan_soal_pihak(action_key):
    """Penjaga peta: pagar harus membaca `item_id`, BUKAN `product_id`.

    Kalau ia keliru membaca product_id, baris ini (yang punya item_id sah)
    akan disebut yatim - nol yang meyakinkan.
    """
    hasil = periksa_gerbang_entitas(
        action_key,
        _jual(
            items=[{"description": "Kain Katun", "unit_price": 1,
                    "item_id": "44444444-4444-4444-4444-444444444444",
                    "product_id": None}]
        ),
    )
    assert hasil is None, hasil


@pytest.mark.parametrize("action_key", AKSI_JUAL)
def test_jual_amplop_rangkap_terisi(action_key):
    """Amplop rangkap mencegah `text = content_text or None` -> layar kosong."""
    h = periksa_gerbang_entitas(action_key, _jual(customer_id=""))
    assert h["success"] is False
    for kunci in ("content", "text"):
        assert isinstance(h[kunci], str) and h[kunci].strip()
    assert h["error"]["code"] == KODE_GERBANG
    assert h["error"]["message"].strip()
    assert h["data"]["question"].strip()
    assert h["data"]["allow_freetext"] is True
    assert (h["content"] or None) is not None


def test_bill_pesan_tetap_menyebut_vendor_bukan_pihak():
    """create_bill WAJIB berperilaku PERSIS SAMA sesudah perluasan radius."""
    h = periksa_gerbang_entitas("create_bill", _bill(vendor_id=None))
    assert h["content"] == (
        "Knitto Textile belum terdaftar sebagai vendor. "
        "Daftarkan dulu, lalu kirim ulang faktur ini."
    ), h["content"]


def test_bill_vendor_nama_ada_id_kosong_diblokir():
    hasil = periksa_gerbang_entitas("create_bill", _bill(vendor_id=None))
    assert hasil is not None, "vendor tak ter-resolve tapi kartu tetap dibangun"
    assert hasil["message_type"] == "CLARIFICATION"
    assert "Knitto Textile" in hasil["content"]
    assert "vendor" in hasil["content"].lower()


def test_bill_item_tanpa_product_id_diblokir():
    hasil = periksa_gerbang_entitas(
        "create_bill",
        _bill(
            vendor_id="22222222-2222-2222-2222-222222222222",
            items=[
                {"product_name": "Kain Katun", "qty": 1, "price": 1,
                 "product_id": "33333333-3333-3333-3333-333333333333"},
                {"product_name": "Benang Jahit", "qty": 2, "price": 2},
            ],
        ),
    )
    assert hasil is not None, "item yatim tapi kartu tetap dibangun"
    assert "Benang Jahit" in hasil["content"]
    assert "Kain Katun" not in hasil["content"], (
        "baris yang SUDAH ter-resolve ikut disebut sebagai belum terdaftar"
    )


def test_bill_semua_ter_resolve_lolos():
    """Kontrol positif utama: pagar tidak menelan kasus yang sah."""
    assert (
        periksa_gerbang_entitas(
            "create_bill", _bill(vendor_id="22222222-2222-2222-2222-222222222222")
        )
        is None
    )


def test_bill_sentinel_create_new_dihitung_kosong():
    hasil = periksa_gerbang_entitas(
        "create_bill", _bill(vendor_id="create_new:Knitto Textile")
    )
    assert hasil is not None


def test_bill_vendor_name_kosong_tidak_memicu_pagar_vendor():
    """Tanpa nama vendor tak ada yang bisa disebut 'belum terdaftar'.

    Kasus itu urusan validate_payload (vendor_name required), bukan pagar ini.
    """
    hasil = periksa_gerbang_entitas(
        "create_bill", _bill(vendor_id=None, vendor_name="")
    )
    assert hasil is None


@pytest.mark.parametrize(
    "action_key",
    [
        "create_customer",
        "create_vendor",
        "create_item",
        "create_expense",
    ],
)
def test_aksi_di_luar_radius_tidak_pernah_diblokir(action_key):
    """WAJIB: create_customer/vendor/item MEMBUAT entitasnya — id kosong wajar.

    Kalau salah satu dari ini tertolak, pendaftaran entitas mati.
    """
    payload = {
        "vendor_id": None,
        "vendor_name": "Knitto Textile",
        "customer_id": None,
        "customer_name": "Toko Melati",
        "name": "Kain Katun",
        "items": [{"product_name": "Kain Katun", "description": "Kain Katun"}],
    }
    assert periksa_gerbang_entitas(action_key, payload) is None


def test_radius_tepat_empat_aksi():
    """Penjaga radius: menambah anggota = tiket baru, bukan efek samping."""
    assert AKSI_DIGERBANG == frozenset(
        {
            "create_bill",
            "create_quote",
            "create_sales_invoice",
            "create_sales_order",
        }
    )


def test_peta_aksi_tidak_mencampur_penamaan():
    """Penjaga KECOCOKAN FIELD — sumber bug berulang di proyek ini.

    Bill = vendor_*/product_id; Quote/SO/SI = customer_*/item_id. Kalau peta
    tertukar, pagar membaca kunci yang selalu kosong (memblokir semuanya) atau
    selalu terisi (tak memblokir apa pun) — dua-duanya gagal DIAM-DIAM.
    """
    assert PETA_AKSI["create_bill"]["id_pihak"] == "vendor_id"
    assert PETA_AKSI["create_bill"]["id_baris"] == "product_id"
    for ak in ("create_quote", "create_sales_invoice", "create_sales_order"):
        assert PETA_AKSI[ak]["id_pihak"] == "customer_id"
        assert PETA_AKSI[ak]["nama_pihak"] == "customer_name"
        assert PETA_AKSI[ak]["id_baris"] == "item_id"
        assert PETA_AKSI[ak]["label_pihak"] == "pelanggan"


# ══════════════════ penjaga BENTUK amplop ══════════════════

def test_amplop_gerbang_punya_semua_kunci_yang_dibaca_pemanggil():
    """`_execute_propose_direct` dipanggil dari ~20 situs.

    Kalau hasilnya BUKAN DIRECT_ACTION_PREVIEW, tiap situs membaca kunci yang
    berbeda: ada `text`, ada `error.message`, ada `content`. Semua WAJIB
    terisi — T181 Fase 1 mengirim `{"message_type":"TEXT","text":null}` ke
    layar dan di-rollback. Kalau bentuk kontrak berubah, tes INI yang gagal
    duluan.
    """
    h = periksa_gerbang_entitas("create_bill", _bill(vendor_id=None))
    assert h["success"] is False
    assert h["message_type"] == "CLARIFICATION"
    for kunci in ("content", "text"):
        assert isinstance(h[kunci], str) and h[kunci].strip(), (
            "kunci %r kosong/None -> layar bisa menerima text: null" % kunci
        )
    assert h["error"]["code"] == KODE_GERBANG
    assert h["error"]["message"].strip()
    assert h["data"]["question"].strip()
    assert h["data"]["options"] == []
    assert h["data"]["allow_freetext"] is True
    # unified_chat: `text = content_text or None`. Kalimat kosong = layar bisu.
    assert (h["content"] or None) is not None


def test_kalimat_menyebut_langkah_berikutnya():
    h = periksa_gerbang_entitas(
        "create_bill",
        _bill(vendor_id=None, items=[{"product_name": "Benang Jahit"}]),
    )
    assert "Daftarkan dulu" in h["content"]
    assert "Knitto Textile" in h["content"] and "Benang Jahit" in h["content"]


# ══════════════════ B. lewat _execute_propose_direct sungguhan ══════════════════

class _PoolPeledak:
    """Pool DB yang MELEDAK kalau disentuh.

    Perannya ganda: (1) membuktikan gerbang memblokir SEBELUM INSERT
    pending_actions; (2) KONTROL POSITIF — pada kasus yang harus lolos, ia
    MELEDAK, jadi 'tidak diblokir' bukan berarti 'kode tak pernah jalan'.
    """

    def __init__(self):
        self.disentuh = False

    async def execute(self, *a, **k):
        self.disentuh = True
        raise AssertionError("DB DISENTUH")

    async def fetch(self, *a, **k):
        self.disentuh = True
        raise AssertionError("DB DISENTUH")

    async def fetchrow(self, *a, **k):
        self.disentuh = True
        raise AssertionError("DB DISENTUH")

    async def fetchval(self, *a, **k):
        self.disentuh = True
        raise AssertionError("DB DISENTUH")


def _bukan_gerbang(hasil):
    """Hasil ini boleh apa saja KECUALI hasil gerbang entitas.

    `error` kadang dict, kadang string apa adanya - dua-duanya diperiksa,
    supaya tes tidak lolos hanya karena bentuknya berbeda.
    """
    err = hasil.get("error") if isinstance(hasil, dict) else None
    kode = err.get("code") if isinstance(err, dict) else str(err or "")
    assert KODE_GERBANG not in str(kode), hasil
    assert not (
        isinstance(hasil, dict)
        and hasil.get("message_type") == "CLARIFICATION"
        and "belum terdaftar" in (hasil.get("content") or "")
    ), hasil


def _executor_uji(monkeypatch):
    from app.services.unified_agent import tool_executor as te_mod

    peledak = _PoolPeledak()

    async def _pool_palsu():
        return peledak

    monkeypatch.setattr(te_mod, "get_session_db_pool", _pool_palsu, raising=False)
    from app.services.unified_agent import db_utils as _db

    monkeypatch.setattr(_db, "get_session_db_pool", _pool_palsu, raising=False)

    class _Ctx:
        tenant_id = "00000000-0000-0000-0000-0000000000aa"
        user_id = "00000000-0000-0000-0000-0000000000bb"
        auth_token = "x"
        headers = {}

        def __getattr__(self, n):
            return ""

    te = te_mod.ToolExecutor(context=_Ctx(), session_id="sesi-uji")

    # Tiga tahap pra-gerbang dinetralkan: masing-masing menyentuh DB/HTTP dan
    # bukan itu yang diuji di sini. Yang TIDAK dinetralkan: gerbangnya sendiri
    # dan segala sesuatu SESUDAHNYA.
    monkeypatch.setattr(
        type(te), "_normalize_payload", lambda self, ak, p: p, raising=True
    )

    async def _enrich(self, at, p):
        return p

    async def _resolve(self, ak, p):
        return None

    monkeypatch.setattr(type(te), "_enrich_payload", _enrich, raising=True)
    monkeypatch.setattr(type(te), "_resolve_entity_names", _resolve, raising=True)
    return te, peledak


@pytest.mark.asyncio
async def test_e2e_bill_vendor_kosong_diblokir_sebelum_db(monkeypatch):
    te, peledak = _executor_uji(monkeypatch)
    hasil = await te._execute_propose_direct(
        {"action_key": "create_bill", "payload": _bill(vendor_id=None)}
    )
    assert hasil.get("message_type") == "CLARIFICATION", hasil
    assert hasil.get("content"), "kalimat kosong sampai ke layar"
    assert peledak.disentuh is False, "INSERT pending_actions tetap dijalankan"


@pytest.mark.asyncio
async def test_e2e_bill_item_yatim_diblokir_sebelum_db(monkeypatch):
    te, peledak = _executor_uji(monkeypatch)
    hasil = await te._execute_propose_direct(
        {
            "action_key": "create_bill",
            "payload": _bill(
                vendor_id="22222222-2222-2222-2222-222222222222",
                items=[{"product_name": "Benang Jahit", "qty": 1, "price": 1}],
            ),
        }
    )
    assert hasil.get("message_type") == "CLARIFICATION", hasil
    assert "Benang Jahit" in hasil["content"]
    assert peledak.disentuh is False


@pytest.mark.asyncio
async def test_e2e_create_customer_tanpa_id_tidak_diblokir(monkeypatch):
    """KONTROL POSITIF WAJIB.

    create_customer TIDAK boleh kena gerbang. Buktinya: eksekusi berjalan
    TERUS sampai menyentuh pool peledak (atau berhenti di validate_payload) —
    yang PASTI bukan CLARIFICATION gerbang.
    """
    te, peledak = _executor_uji(monkeypatch)
    try:
        hasil = await te._execute_propose_direct(
            {
                "action_key": "create_customer",
                "payload": {"nama": "Toko Melati", "customer_id": None},
            }
        )
    except AssertionError as e:
        assert "DB DISENTUH" in str(e)
        return
    _bukan_gerbang(hasil)


@pytest.mark.asyncio
@pytest.mark.parametrize("action_key", ["create_vendor", "create_item"])
async def test_e2e_create_vendor_item_tanpa_id_tidak_diblokir(
    monkeypatch, action_key
):
    te, peledak = _executor_uji(monkeypatch)
    try:
        hasil = await te._execute_propose_direct(
            {
                "action_key": action_key,
                "payload": {"name": "Kain Katun", "vendor_name": "Knitto Textile"},
            }
        )
    except AssertionError as e:
        assert "DB DISENTUH" in str(e)
        return
    _bukan_gerbang(hasil)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action_key", ["create_quote", "create_sales_invoice", "create_sales_order"]
)
async def test_e2e_penjualan_item_yatim_diblokir_sebelum_db(
    monkeypatch, action_key
):
    """FASE 1b: quote/SI/SO dengan baris tanpa item_id tidak boleh berkartu.

    validate_payload TIDAK menangkap ini — `items` hanya dicek truthy sebagai
    list, tidak pernah per-baris. Peledak membuktikan blokirnya terjadi
    SEBELUM INSERT pending_actions.
    """
    te, peledak = _executor_uji(monkeypatch)
    hasil = await te._execute_propose_direct(
        {
            "action_key": action_key,
            "payload": _jual(
                items=[
                    {
                        "description": "Kain Katun",
                        "unit_price": 1000,
                        "item_id": "44444444-4444-4444-4444-444444444444",
                    },
                    {"description": "Benang Jahit", "unit_price": 2000},
                ]
            ),
        }
    )
    assert hasil.get("message_type") == "CLARIFICATION", hasil
    assert "Benang Jahit" in hasil["content"]
    assert "Kain Katun" not in hasil["content"]
    assert peledak.disentuh is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action_key", ["create_quote", "create_sales_invoice", "create_sales_order"]
)
async def test_e2e_penjualan_semua_ter_resolve_lewat_gerbang(
    monkeypatch, action_key
):
    """KONTROL POSITIF: payload SAH tidak tertahan pagar.

    Buktinya bukan 'tidak ada CLARIFICATION' (itu juga terjadi kalau kode tak
    pernah jalan), melainkan eksekusi BERLANJUT sampai menyentuh peledak.
    """
    te, peledak = _executor_uji(monkeypatch)
    try:
        hasil = await te._execute_propose_direct(
            {"action_key": action_key, "payload": _jual()}
        )
    except AssertionError as e:
        assert "DB DISENTUH" in str(e)
        assert peledak.disentuh is True
        return
    _bukan_gerbang(hasil)
    # Lolos TANPA menyentuh peledak = kode berhenti di tahap lain (mis.
    # validate_payload) dan kontrol positif ini tidak membuktikan apa pun.
    assert peledak.disentuh is True, (
        "eksekusi tidak pernah mencapai DB - 'tidak diblokir' tak terbukti: %r"
        % (hasil,)
    )


# ══════════════════ penjaga PEMASANGAN (wiring) ══════════════════

def test_gerbang_dipasang_sebelum_validate_dan_insert():
    """Fungsi murni yang benar tapi tak pernah dipanggil = nol perlindungan.

    Diperiksa pada SUMBER `_execute_propose_direct`: panggilan gerbang harus
    muncul, dan harus mendahului validate_payload maupun INSERT.
    """
    import inspect

    from app.services.unified_agent.tool_executor import ToolExecutor

    src = inspect.getsource(ToolExecutor._execute_propose_direct)
    i_gerbang = src.find("periksa_gerbang_entitas(")
    i_validate = src.find("validate_payload(")
    i_insert = src.find("INSERT INTO pending_actions")
    assert i_gerbang != -1, "gerbang tidak dipanggil di _execute_propose_direct"
    assert i_validate != -1 and i_insert != -1, "jangkar hilang - tes ini buta"
    assert i_gerbang < i_validate, "gerbang dipasang SESUDAH validate_payload"
    assert i_gerbang < i_insert, "gerbang dipasang SESUDAH INSERT pending_actions"


def test_orchestrator_punya_jalan_keluar_clarification():
    """Kontrak pengembalian: tanpa cabang ini, hasil pagar diumpankan BALIK ke
    model di loop tool dan tak pernah sampai ke layar."""
    import inspect

    from app.services.unified_agent import orchestrator as orc

    src = inspect.getsource(orc)
    ada = 'result.get("message_type") == "CLARIFICATION"' in src
    assert ada, "loop tool LLM tidak punya cabang keluar CLARIFICATION"


@pytest.mark.asyncio
async def test_e2e_bill_semua_ter_resolve_lewat_gerbang(monkeypatch):
    """Kontrol positif jalur e2e: bill yang SAH tidak tertahan pagar.

    Buktinya bukan 'tidak ada CLARIFICATION' (bisa berarti kode tak jalan),
    melainkan eksekusi BERLANJUT sampai menyentuh pool peledak / berhenti di
    tahap lain - yang mana pun, bukan hasil gerbang.
    """
    te, peledak = _executor_uji(monkeypatch)
    try:
        hasil = await te._execute_propose_direct(
            {
                "action_key": "create_bill",
                "payload": _bill(vendor_id="22222222-2222-2222-2222-222222222222"),
            }
        )
    except AssertionError as e:
        assert "DB DISENTUH" in str(e)
        assert peledak.disentuh is True
        return
    _bukan_gerbang(hasil)
