"""T179-Q3 FASE 0 — GATE OFFLINE skema (berdiri sendiri, TANPA pytest).

pytest TIDAK terpasang di image api_gateway maupun di host droplet (diukur
2026-08-30), jadi gate ini dijalankan langsung dengan `python`.

Empat kasus yang semuanya hijau tidak memberi tahu apakah alat ini menguji
sesuatu. Karena itu SETIAP assertion positif dipasangkan dengan KONTROL NEGATIF
yang membuktikan assertion itu BISA MENYALA.

Jalankan:
  docker run --rm \
    -v /root/<worktree>/backend/api_gateway:/app/backend/api_gateway:ro \
    -w /app/backend/api_gateway -e PYTHONPATH=/app/backend/api_gateway \
    --entrypoint python milkyhoop-dev-api_gateway \
    tests/chat/t179q3_gate_skema.py
"""

import copy
import json
import sys

from app.services.llm.gemini_client import GeminiClient
from app.services.unified_agent.direct_action_registry import (
    DIRECT_ACTIONS,
    get_direct_action,
)
from app.services.unified_agent.entity_extractor import build_intent_schema

AKSI_LAIN = [
    "create_sales_invoice",
    "create_quote",
    "create_sales_order",
    "create_stock_adjustment",
    "create_journal_entry",
    "create_credit_note",
    "create_vendor_credit",
    "create_expense",
    "update_item",
    "update_customer",
    "update_vendor",
]

HASIL = []


def kasus(nama, fn):
    try:
        fn()
        HASIL.append(("HIJAU", nama, ""))
    except Exception as e:  # noqa: BLE001
        HASIL.append(("MERAH", nama, f"{type(e).__name__}: {e}"))


def _raw(intent):
    s = build_intent_schema(intent)
    return s["json_schema"]["schema"] if s else None


def _cleaned(intent):
    r = _raw(intent)
    return GeminiClient._clean_schema(copy.deepcopy(r)) if r else None


def _cek_array(schema):
    """Assertion inti — dipakai kasus positif MAUPUN kontrol negatif."""
    items = schema["properties"]["items"]
    assert items["type"] == "array", f"items bukan array: {items.get('type')!r}"
    baris = items["items"]
    assert baris["type"] == "object", f"baris bukan object: {baris.get('type')!r}"
    for k in ("product_name", "qty", "price"):
        assert k in baris["properties"], f"kunci per-baris hilang: {k}"


def _items_spec():
    return next(f for f in get_direct_action("create_bill").fields if f.name == "items")


# ── 1 create_bill memuat array sungguhan ─────────────────────────────
def t1():
    _cek_array(_raw("create_bill"))


# ── 1K KONTROL: assertion di atas BISA menyala ───────────────────────
def t1k():
    spec = _items_spec()
    asli = spec.item_schema
    try:
        spec.item_schema = None
        menyala = False
        try:
            _cek_array(_raw("create_bill"))
        except AssertionError as e:
            menyala = "items bukan array" in str(e)
        assert menyala, "KONTROL GAGAL: skema sengaja salah tetap lolos"
    finally:
        spec.item_schema = asli
    _cek_array(_raw("create_bill"))  # pulih


# ── 2 array LOLOS _clean_schema tanpa diruntuhkan ────────────────────
def t2():
    c = _cleaned("create_bill")
    _cek_array(c)
    assert c["properties"]["items"]["type"] == "array", "array DIRUNTUHKAN"
    unit = c["properties"]["items"]["items"]["properties"]["unit"]
    assert unit["type"] == "string" and unit["nullable"] is True


# ── 2K KONTROL: _clean_schema memang MERUNTUHKAN union ───────────────
def t2k():
    h = GeminiClient._clean_schema({"type": ["string", "null"]})
    assert h["type"] == "string" and h.get("nullable") is True, (
        "KONTROL GAGAL: _clean_schema tak melakukan apa pun, "
        "maka t2 hijau tanpa arti"
    )


def _sidik(intent):
    return json.dumps(
        {"raw": _raw(intent), "cleaned": _cleaned(intent)},
        sort_keys=True,
        ensure_ascii=False,
    )


# ── 3 hanya create_bill yang membawa item_schema ─────────────────────
def t3():
    pembawa = [
        (k, f.name)
        for k, cfg in DIRECT_ACTIONS.items()
        for f in cfg.fields
        if getattr(f, "item_schema", None)
    ]
    assert pembawa == [("create_bill", "items")], f"pembawa item_schema: {pembawa}"


# ── 4 sidik jari aksi lain + KONTROL bahwa sidik jari peka ───────────
def t4(intent):
    def _f():
        cfg = get_direct_action(intent)
        assert cfg, f"{intent} tak ada di registry"
        sebelum = _sidik(intent)
        assert sebelum not in ("null", '{"cleaned": null, "raw": null}'), (
            f"KONTROL GAGAL: {intent} tak menghasilkan skema sama sekali"
        )
        target = next(
            (f for f in cfg.fields if not f.hidden and not f.display_only),
            cfg.fields[0],
        )
        asli = target.item_schema
        try:
            target.item_schema = {
                "type": "object",
                "properties": {"x": {"type": "string"}},
            }
            assert _sidik(intent) != sebelum, (
                "KONTROL GAGAL: sidik jari TIDAK berubah walau field diubah — alat buta"
            )
        finally:
            target.item_schema = asli
        assert _sidik(intent) == sebelum, "sidik jari tak pulih"

    return _f


def main():
    kasus("1  create_bill.items = array sungguhan", t1)
    kasus("1K KONTROL cek_array bisa MERAH", t1k)
    kasus("2  array lolos _clean_schema tanpa diruntuhkan", t2)
    kasus("2K KONTROL _clean_schema memang meruntuhkan union", t2k)
    kasus("3  hanya create_bill yang membawa item_schema", t3)
    for i in AKSI_LAIN:
        kasus(f"4K KONTROL sidik jari peka — {i}", t4(i))

    lebar = max(len(n) for _, n, _ in HASIL)
    for st, nama, pesan in HASIL:
        print(f"[{st}] {nama.ljust(lebar)}  {pesan}")
    merah = [h for h in HASIL if h[0] == "MERAH"]
    print(f"\nTOTAL {len(HASIL)} kasus, MERAH {len(merah)}")
    return 1 if merah else 0


if __name__ == "__main__":
    sys.exit(main())
