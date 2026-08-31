"""
T187 — blok `T171_SISA_BULK` melucuti `items` dari BADAN POST semua aksi.

Kelas bug: di `_confirm_direct_action`, `request_body = clean_payload` adalah
ALIAS. Blok bertanda T171_SISA_BULK menjalankan `clean_payload.pop("items")`
TANPA dijaga `action_key`, padahal komentarnya menyatakan radiusnya hanya
POST /api/items. Akibatnya setiap aksi yang lewat JALUR GENERIK (clean_payload
= payload minus display_only) mengirim badan tanpa `items`.

Terukur di produksi 2026-08-31 (SEBELUM perbaikan):
    [EXTRACT_S2] n_items=5 tipe=list  -> [ENRICH] backfilled items[0..4]
    POST /api/quotes -> 422 {'loc':['body','items'],'msg':'Field required'}

Yang diuji di sini adalah BADAN YANG DIKIRIM: mock httpx MEREKAM json= yang
diteruskan ke endpoint. Itu assertion utamanya, bukan kode status.

Batas: ini menguji handler confirm dengan pool & HTTP palsu. Ia TIDAK menguji
apakah endpoint sungguhan menerima badan itu.
"""
import os
import sys
import json
import uuid
import pytest

sys.path.insert(0, "/app/backend/api_gateway")

# Impor LUNAK: di pohon yang tak memuat perbaikan ini, berkas tes tetap TERBAWA.
# ModuleNotFoundError bukan bukti merah — maka kegagalan impor harus menjadi
# SKIP yang terlihat, bukan error yang menyamar jadi bukti.
os.environ.setdefault("OPENAI_API_KEY", "uji-t187-bukan-kunci-asli")

try:
    import httpx
    from app.routers import unified_chat as UC
except Exception:  # pragma: no cover
    UC = None
    httpx = None

pytestmark = pytest.mark.skipif(UC is None, reason="unified_chat tidak bisa diimpor")


# ─────────────────────────── perkakas palsu ───────────────────────────

class PoolPalsu:
    """Pool asyncpg palsu: cukup untuk melewati gerbang pending_action."""

    def __init__(self, action_key, action_plan):
        self._action_key = action_key
        self._action_plan = action_plan
        self.eksekusi = []

    async def fetchrow(self, sql, *args):
        if "FROM pending_actions" in sql:
            return {
                "action_id": self._action_key,
                "action_plan": json.dumps(self._action_plan),
                "status": "PENDING",
                "expires_at": None,
            }
        return None

    async def fetchval(self, sql, *args):
        # klaim atomik EXECUTING -> berhasil
        return uuid.uuid4()

    async def execute(self, sql, *args):
        self.eksekusi.append(sql)
        return "UPDATE 1"

    async def acquire(self):
        raise RuntimeError("acquire tidak dipakai di tes ini")

    async def release(self, conn):
        return None


class ReqPalsu:
    headers = {"authorization": "Bearer uji-t187"}


class PerekamHTTP:
    """Mengganti httpx.AsyncClient. MEREKAM badan yang dikirim."""

    rekaman = []

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def request(self, method=None, url=None, json=None, headers=None, **k):
        PerekamHTTP.rekaman.append({"method": method, "url": url, "body": json})
        return BalasanPalsu()

    async def post(self, url, **k):
        PerekamHTTP.rekaman.append({"method": "POST", "url": url, "body": k.get("json")})
        return BalasanPalsu()


class BalasanPalsu:
    status_code = 201
    text = "{}"

    def json(self):
        return {"id": "00000000-0000-0000-0000-0000000000aa"}


async def badan_yang_dikirim(monkeypatch, action_key, payload):
    """Jalankan confirm dan kembalikan badan POST PERTAMA yang direkam."""
    PerekamHTTP.rekaman = []
    monkeypatch.setattr(httpx, "AsyncClient", PerekamHTTP)
    pool = PoolPalsu(action_key, payload)
    await UC._confirm_direct_action(
        pending_action_id=str(uuid.uuid4()),
        tenant_id="t-uji",
        user_id="u-uji",
        pool=pool,
        http_request=ReqPalsu(),
        session_id=None,
        doc_status="POSTED",
    )
    assert PerekamHTTP.rekaman, (
        "NOL permintaan HTTP terekam — handler tak pernah sampai ke POST, "
        "jadi tes ini tidak menguji apa pun"
    )
    return PerekamHTTP.rekaman[0]["body"]


def lima_baris():
    return [
        {"product_name": f"Kaos T187 {i}", "qty": 1, "price": 10000, "item_id": f"id-{i}"}
        for i in range(5)
    ]


# ─────────────── KONTROL: harness ini BISA gagal ───────────────

@pytest.mark.asyncio
async def test_kontrol_harness_bisa_gagal(monkeypatch):
    """Kontrol positif. Membuktikan perekam sungguh membaca badan yang dikirim:
    field yang MEMANG tidak dikirim harus TIDAK ADA."""
    badan = await badan_yang_dikirim(monkeypatch, "create_quote", {
        "customer_id": "c-1", "customer_name": "PT Uji",
        "quote_date": "2026-08-31", "items": lima_baris(),
    })
    assert isinstance(badan, dict), f"badan bukan dict: {type(badan)}"
    assert "kunci_yang_tak_pernah_ada_t187" not in badan, (
        "perekam mengembalikan sesuatu yang menerima kunci apa pun — "
        "asersi lain di berkas ini tidak bermakna"
    )


# ─────────────── GATE A: aksi berjalur generik harus MEMBAWA items ───────────────

@pytest.mark.asyncio
async def test_create_quote_badan_memuat_lima_items(monkeypatch):
    badan = await badan_yang_dikirim(monkeypatch, "create_quote", {
        "customer_id": "c-1", "customer_name": "PT Uji",
        "quote_date": "2026-08-31", "items": lima_baris(),
    })
    assert "items" in badan, (
        "T187: `items` LENYAP dari badan POST /api/quotes — inilah 422 "
        "{'loc':['body','items'],'msg':'Field required'} di produksi"
    )
    assert len(badan["items"]) == 5, f"n_items={len(badan['items'])}, harusnya 5"


@pytest.mark.asyncio
@pytest.mark.parametrize("action_key", [
    "create_bill", "create_sales_invoice", "create_sales_order",
])
async def test_dokumen_berbaris_badan_memuat_items(monkeypatch, action_key):
    badan = await badan_yang_dikirim(monkeypatch, action_key, {
        "customer_id": "c-1", "vendor_id": "v-1",
        "customer_name": "PT Uji", "vendor_name": "PT Vendor",
        "issue_date": "2026-08-31", "items": lima_baris(),
    })
    assert "items" in badan, f"T187: `items` lenyap dari badan POST {action_key}"
    assert len(badan["items"]) == 5


# ─────────────── KONTROL SEHAT: create_item harus TAK BERUBAH ───────────────

@pytest.mark.asyncio
async def test_create_item_slide_bulk_tetap_dilucuti(monkeypatch):
    """KONTROL SEHAT TERPENTING. Blok itu ADA untuk ini: `items` bukan field
    CreateItemRequest, jadi ia wajib TETAP dibuang dari POST /api/items."""
    badan = await badan_yang_dikirim(monkeypatch, "create_item", {
        "name": "Kaos T187 Slide", "item_type": "product",
        "base_unit": "pcs", "sales_price": 50000,
        "items": lima_baris(),
    })
    assert "items" not in badan, (
        "REGRESI: `items` bocor ke POST /api/items -> 422 unknown field. "
        "Blok T171_SISA_BULK berhenti melindungi jalur yang dimaksudnya"
    )
    assert badan.get("name") == "Kaos T187 Slide", "field skalar ikut hilang"


@pytest.mark.asyncio
async def test_create_item_tunggal_tak_tergores(monkeypatch):
    """create_item tanpa `items` sama sekali: badan harus utuh apa adanya."""
    badan = await badan_yang_dikirim(monkeypatch, "create_item", {
        "name": "Kaos T187 Tunggal", "item_type": "product",
        "base_unit": "pcs", "sales_price": 75000, "purchase_price": 50000,
    })
    assert "items" not in badan
    for k, v in (("name", "Kaos T187 Tunggal"), ("base_unit", "pcs"),
                 ("sales_price", 75000), ("purchase_price", 50000)):
        assert badan.get(k) == v, f"{k} berubah: {badan.get(k)!r} != {v!r}"
