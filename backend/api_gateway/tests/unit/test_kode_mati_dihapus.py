"""Kode mati dihapus (26 Sep 2026): routers/streaming_chat.py (tak dipasang di main.py, 0 rujukan) dan paket
services/insight/ (ragllm InsightOrchestrator/ContextService dkk., 0 pengimpor; router ragllm dicabut 14 Sep).

Mengimpor app.main di runner unit butuh env penuh (kunci LLM) -> penjaga di sini STATIS: setiap impor relatif
di app/ (termasuk `from .routers import a, b` milik main.py) wajib menunjuk berkas/paket modul yang ADA.
Menghapus modul yang masih diimpor = merah di sini, bukan crash saat restart.
"""
import ast
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[2] / "app"
DIHAPUS = ["routers/streaming_chat.py", "services/insight"]


def _ada_modul(pkg_dir: Path, nama: str) -> bool:
    return (pkg_dir / f"{nama}.py").is_file() or (pkg_dir / nama / "__init__.py").is_file()


def impor_rusak(akar: Path):
    """[(berkas, baris, modul)] impor relatif yang menunjuk modul TAK ADA."""
    out = []
    for p in sorted(akar.rglob("*.py")):
        try:
            t = ast.parse(p.read_text())
        except SyntaxError:
            continue
        for n in ast.walk(t):
            if not (isinstance(n, ast.ImportFrom) and n.level > 0):
                continue
            base = p.parent
            for _ in range(n.level - 1):
                base = base.parent
            if n.module:
                bagian = n.module.split(".")
                pkg = base
                for i, b in enumerate(bagian):
                    if i == len(bagian) - 1:
                        if not _ada_modul(pkg, b):
                            out.append((str(p.relative_to(akar)), n.lineno, n.module))
                    pkg = pkg / b
                target = base.joinpath(*bagian)
            else:
                target = base
            # `from .routers import chat` -> chat harus submodul ATAU atribut __init__; nama yang
            # BUKAN submodul dan tak didefinisikan di __init__ tak diperiksa (bisa atribut dinamis)
            # modul BERKAS (`x.py`) menang atas folder `x/` tanpa __init__ (namespace) -> nama = atribut
            berkas_modul = target.with_suffix(".py").is_file() if n.module else False
            if target.is_dir() and not berkas_modul:
                init = target / "__init__.py"
                teks_init = init.read_text() if init.is_file() else ""
                for a in n.names:
                    if a.name == "*" or _ada_modul(target, a.name):
                        continue
                    if a.name not in teks_init:
                        out.append((str(p.relative_to(akar)), n.lineno, f"{n.module or '.'}.{a.name}"))
    return out


def test_berkas_kode_mati_sudah_hilang():
    for rel in DIHAPUS:
        assert not (APP / rel).exists(), rel


def test_semua_impor_relatif_app_menunjuk_modul_yang_ada():
    assert impor_rusak(APP) == []


def test_main_mengimpor_router_yang_ada():
    t = ast.parse((APP / "main.py").read_text())
    router = [a.name for n in ast.walk(t) if isinstance(n, ast.ImportFrom) and n.level == 1 and n.module == "routers"
              for a in n.names]
    assert len(router) > 50
    hilang = [r for r in router if not _ada_modul(APP / "routers", r)]
    assert hilang == [], hilang


def test_penjaga_bisa_merah(tmp_path):
    (tmp_path / "routers").mkdir()
    (tmp_path / "routers" / "__init__.py").write_text("")
    (tmp_path / "routers" / "ada.py").write_text("x = 1\n")
    (tmp_path / "cfg.py").write_text("settings = 1\n")
    (tmp_path / "cfg").mkdir()          # folder namespace berbayang cfg.py -> tak boleh merah
    (tmp_path / "main.py").write_text("from .routers import ada, streaming_chat\nfrom .services.insight import X\nfrom .cfg import settings\n")
    rusak = {m for _, _, m in impor_rusak(tmp_path)}
    assert "routers.streaming_chat" in rusak and "services.insight" in rusak and "routers.ada" not in rusak
    assert not any(m.startswith("cfg") for m in rusak)
