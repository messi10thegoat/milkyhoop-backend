"""TEKS-PERIODE-ID (16 Okt 2026): keluarga "Cannot post ... period" diterjemahkan ke teks_galat.periode_tertutup
(FE r285 mencocokkan /periode.*sudah (ditutup|dikunci)/ dan memakai teks BE apa adanya). Status kode tak berubah."""
import ast
import pathlib

from app.services import teks_galat as tg

ROUTERS = pathlib.Path(__file__).resolve().parents[2] / "app" / "routers"
POLA = ("Cannot post to", "Cannot post opening balance to", "Cannot post reversal to", "Cannot create adjustment journals in")


def _detail_inggris():
    temu = []
    for p in ROUTERS.glob("*.py"):
        for n in ast.walk(ast.parse(p.read_text())):
            if isinstance(n, ast.keyword) and n.arg == "detail" and isinstance(n.value, ast.JoinedStr):
                teks = "".join(v.value for v in n.value.values if isinstance(v, ast.Constant) and isinstance(v.value, str))
                if any(teks.startswith(x) for x in POLA) and "period" in teks:
                    temu.append(f"{p.name}:{n.lineno}")
    return temu


def test_tak_ada_detail_periode_inggris_di_router():
    assert _detail_inggris() == []


def test_teks_cocok_regex_fe():
    import re
    fe = re.compile(r"periode.*sudah (ditutup|dikunci)", re.I)
    assert tg.periode_tertutup("2026-07", "CLOSED") == "Periode 2026-07 sudah ditutup. Buka kembali di Akuntansi › Tutup Buku, atau ubah tanggalnya."
    assert tg.periode_tertutup("2026-07", "locked").startswith("Periode 2026-07 sudah dikunci.")
    assert tg.periode_tertutup(None, "CLOSED").startswith("Periode akuntansi sudah ditutup.")
    for t in (tg.periode_tertutup("2026-07", "CLOSED"), tg.periode_tertutup(None, "LOCKED")):
        assert fe.search(t)


def test_semua_situs_memakai_helper():
    n = sum(p.read_text().count("tg.periode_tertutup(") for p in ROUTERS.glob("*.py"))
    assert n >= 19
