"""#41 -- tak ada kata sandi DB LITERAL di kode app/ (rotasi sandi Postgres 25 Sep 2026).

Dulu tiga koneksi HIDUP memakai sandi literal, bukan env: pool PolicyEngine (main.py), info tenant
publik (routers/tenant_chat.py), LISTEN realtime (services/realtime.py). Rotasi sandi akan
mematahkan ketiganya diam-diam sementara pool utama (config.settings) tetap sehat.

Pemindai AST: argumen kata kunci `password=` atau kunci dict "password" yang nilainya string
literal TAK KOSONG -> merah. Tes ini sengaja tak memuat sandi apa pun.
"""
import ast
import pathlib

APP = pathlib.Path(__file__).resolve().parents[2] / "app"


def _temuan(tree, nama):
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "password" and isinstance(kw.value, ast.Constant) \
                        and isinstance(kw.value.value, str) and kw.value.value:
                    out.append(f"{nama}:{kw.value.lineno}")
        elif isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                if isinstance(k, ast.Constant) and k.value == "password" and isinstance(v, ast.Constant) \
                        and isinstance(v.value, str) and v.value:
                    out.append(f"{nama}:{v.lineno}")
    return out


def pindai(akar=APP):
    temuan = []
    for p in sorted(akar.rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        temuan += _temuan(ast.parse(p.read_text(encoding="utf-8")), str(p.relative_to(akar)))
    return temuan


def test_app_tanpa_sandi_literal():
    assert APP.is_dir() and len(list(APP.rglob("*.py"))) > 100, "pemindai tak melihat app/"
    assert pindai() == []


def test_pemindai_bisa_merah():
    kasus = [
        'asyncpg.connect(host="h", password="rahasia")',
        '_DB = dict(user="u", password="rahasia")',
        'CFG = {"password": "rahasia"}',
    ]
    for src in kasus:
        assert _temuan(ast.parse(src), "x") == ["x:1"], src
    for src in ['connect(password=os.environ["P"])', 'connect(password="")', 'connect(**cfg)']:
        assert _temuan(ast.parse(src), "x") == [], src
