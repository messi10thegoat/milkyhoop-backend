"""Validator mini JSON-Schema (subset kontrak dashboard-sidebar: type/enum/pattern/required/properties/
items/maxItems/$ref). Image gateway tak memuat jsonschema. Dibuktikan BISA MERAH di
test_dashboard_v2.test_validator_bisa_merah (Law 33). Dipakai tes unit DAN scripts/gate_d3_dashboard.py."""
import json
import re
from pathlib import Path

DATA = Path(__file__).parent / "data" / "dashboard_v2"


def _cocok(nilai, skema, akar, jalur="$"):
    if "$ref" in skema:
        return _cocok(nilai, akar["$defs"][skema["$ref"].split("/")[-1]], akar, jalur)
    galat = []
    tipe = skema.get("type")
    if tipe is not None:
        tipe = tipe if isinstance(tipe, list) else [tipe]
        peta = {"object": dict, "array": list, "string": str, "integer": int, "number": (int, float),
                "boolean": bool, "null": type(None)}
        if not any(isinstance(nilai, peta[x]) and not (x in ("integer", "number") and isinstance(nilai, bool))
                   for x in tipe):
            return [f"{jalur}: tipe {type(nilai).__name__} bukan {tipe}"]
    if "enum" in skema and nilai not in skema["enum"]:
        galat.append(f"{jalur}: {nilai!r} tak di enum")
    if isinstance(nilai, str) and "pattern" in skema and not re.search(skema["pattern"], nilai):
        galat.append(f"{jalur}: {nilai!r} tak cocok pola")
    if isinstance(nilai, dict):
        for k in skema.get("required", []):
            if k not in nilai:
                galat.append(f"{jalur}.{k}: wajib")
        for k, s in skema.get("properties", {}).items():
            if k in nilai:
                galat += _cocok(nilai[k], s, akar, f"{jalur}.{k}")
    if isinstance(nilai, list):
        if "maxItems" in skema and len(nilai) > skema["maxItems"]:
            galat.append(f"{jalur}: > maxItems")
        for i, x in enumerate(nilai):
            galat += _cocok(x, skema.get("items", {}), akar, f"{jalur}[{i}]")
    return galat


def _skema(nama):
    return json.loads((DATA / nama).read_text())


