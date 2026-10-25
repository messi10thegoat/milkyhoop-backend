"""Cabang aktivasi Milkybook: rute terdaftar di main.py, setiap rute terpetakan izin (first-match), inventaris rute tulis
memuatnya. Pin SDK ada di cabang feat/milkybook-image (langkah 6, bersama build image: test_requirements_kunci). Tes teks-sumber (main.py menarik DB/Redis bila diimpor)."""
import ast
import os
import re

from app.milkybook.pasang import milkybook_router

AKAR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _src(rel):
    return open(os.path.join(AKAR, rel), encoding="utf-8").read()


def _rute():
    return [("/api/milkybook" + r.path, sorted(r.methods)[0]) for r in milkybook_router().routes]


def test_main_memasang_router_milkybook():
    assert re.search(r'app\.include_router\(milkybook_router\(\), prefix="/api/milkybook"', _src("app/main.py"))


def test_setiap_rute_terpetakan_izin_quote_r_pola_pertama():
    pohon = ast.parse(_src("app/middleware/permission_middleware.py"))
    tabel = next(ast.literal_eval(n.value) for n in pohon.body
                 if isinstance(n, ast.AnnAssign) and getattr(n.target, "id", "") == "ROUTE_PERMISSIONS")
    for jalur, metode in _rute():
        konkret = re.sub(r"\{[a-z_]+\}", "00000000-0000-4000-8000-000000000000", jalur)
        pertama = next(((m, a) for p, ms, m, a in tabel if metode in ms and re.match(p, konkret)), None)
        assert pertama == ("quote", "R"), (jalur, pertama)


def test_inventaris_rute_tulis_memuat_rute_milkybook():
    inv = set(_src("tests/unit/inventaris_rute_tulis.txt").splitlines())
    for jalur, metode in _rute():
        if metode == "GET":  # inventaris = rute TULIS saja; GET dijaga tabel izin (READ-authz STEP 2)
            continue
        assert f"{metode} {jalur}" in inv


def test_sdk_anthropic_dipin_sama_dengan_eval():
    """Langkah 6 (bersama build image): pin SDK + transitif; test_requirements_kunci menuntut requirements == image."""
    s = _src("requirements.txt")
    for baris in ("anthropic==2.3.0", "httpx2==2.16.0", "httpcore2==2.16.0", "docstring_parser==0.18.0",
                  "opentelemetry-api==1.45.1", "truststore==0.10.5"):
        assert re.search(rf"^{re.escape(baris)}$", s, re.M), baris
    assert re.search(r"^idna==3\.20\b", s, re.M)
