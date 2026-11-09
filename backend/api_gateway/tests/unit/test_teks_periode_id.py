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


# ---- PERIODE-TEKS (1 Nov 2026, putusan MASTER): tak ada f-string periode dengan status MENTAH di luar penentu ----
# Dulu UMP (pratinjau + tulis), ND, transfer bank, reklas beban, jurnal reklas: "Periode akuntansi sudah CLOSED";
# produksi: teks Inggris "closed/locked". Semua -> tg.periode_tertutup. Log (logger.*) bukan teks pengguna -> dikecualikan.
APP = ROUTERS.parent
_LOG = {"debug", "info", "warning", "error", "exception", "critical"}


def _status_mentah(akar=APP):
    temu = []
    for p in sorted(list((akar / "routers").glob("*.py")) + list((akar / "services").glob("*.py"))):
        if p.name == "teks_galat.py":
            continue
        pohon = ast.parse(p.read_text())
        dalam_log = set()
        for n in ast.walk(pohon):
            if isinstance(n, ast.Call) and getattr(n.func, "attr", None) in _LOG:
                dalam_log |= {id(x) for x in ast.walk(n)}
        for n in ast.walk(pohon):
            if id(n) in dalam_log:
                continue
            if isinstance(n, ast.JoinedStr):
                teks = "".join(v.value for v in n.values if isinstance(v, ast.Constant) and isinstance(v.value, str)).lower()
                mentah = any(isinstance(v, ast.FormattedValue) and isinstance(v.value, ast.Subscript)
                             and isinstance(v.value.slice, ast.Constant) and v.value.slice.value == "status" for v in n.values)
                if mentah and "period" in teks:
                    temu.append(f"{p.name}:{n.lineno}")
            elif isinstance(n, ast.Constant) and isinstance(n.value, str) and "period is closed" in n.value.lower():
                temu.append(f"{p.name}:{n.lineno}")
    return temu


def test_tak_ada_status_periode_mentah():
    assert _status_mentah() == []


def test_pemindai_status_mentah_bisa_merah(tmp_path):
    (tmp_path / "routers").mkdir()
    (tmp_path / "services").mkdir()
    (tmp_path / "routers" / "a.py").write_text(
        "def f(r, log):\n"
        "    x = f\"Periode akuntansi sudah {r['status']}\"\n"
        "    log.error(f\"Cannot post to {r['status']} period\")\n"
        "    y = 'Cannot void: target period is closed/locked'\n")
    (tmp_path / "services" / "b.py").write_text("def g(p):\n    return {'message': f\"Periode {p['status']} untuk x.\"}\n")
    assert _status_mentah(tmp_path) == ["a.py:2", "a.py:4", "b.py:2"]


def test_situs_periode_teks_memakai_penentu():
    harap = {"routers/vendor_deposits.py": 4, "routers/production.py": 2, "routers/journals.py": 4,
             "services/uang_muka_vendor_rencana.py": 2, "services/nota_debit_rencana.py": 1, "services/reklas_kelas_beban.py": 2,
             "services/kernel_document_executor.py": 1}
    # Kas&Bank standar SO (9 Nov 2026): routers/bank_transfers.py keluar -- void transfer memeriksa periode lewat penentu bersama
    # KT.rencana_void_transfer -> bayar_keluar_rencana.periode_terbuka (teks = tg.periode_tertutup, satu penentu).
    for rel, n in harap.items():
        assert (APP / rel).read_text().count("tg.periode_tertutup(") >= n, rel


def test_alasan_gagal_intake_periode_tertutup_kalimat_indonesia():
    """Jalur dokumen masuk (kernel): periode tertutup -> _mark_failed(..., <alasan>) = kalimat penentu, bukan teks Inggris."""
    src = (APP / "services/kernel_document_executor.py").read_text()
    pohon = ast.parse(src)
    alasan = []
    for n in ast.walk(pohon):
        if isinstance(n, ast.If) and isinstance(n.test, ast.UnaryOp) and isinstance(n.test.operand, ast.Name) \
                and n.test.operand.id == "period_ok":
            for m in ast.walk(n):
                if isinstance(m, ast.Call) and getattr(m.func, "attr", None) == "_mark_failed":
                    alasan.append(ast.unparse(m.args[-1]))
    assert alasan == ["tg.periode_tertutup(None, 'CLOSED')"], alasan
    assert tg.periode_tertutup(None, "CLOSED") == "Periode akuntansi sudah ditutup. Buka kembali di Akuntansi › Tutup Buku, atau ubah tanggalnya."
