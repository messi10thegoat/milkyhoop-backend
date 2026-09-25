"""Kenaikan GANDA param_idx sesudah blok ?search (26 Sep 2026; tiket BE3 + sapuan AST).

Pola: `param_idx += 1` sesudah if/else pencarian yang SEMUA cabangnya sudah menaikkan penghitung -> placeholder
berikutnya ($N untuk date_from/date_to/LIMIT/OFFSET) melompat satu -> jumlah parameter != placeholder -> 500
pada daftar ber-?search. Pemindai AST (kenaikan ganda; kontrol merah 1/1) menemukan TEPAT 4:
bank_transfers, fixed_assets, purchase_orders, stock_adjustments.
Uji: conn palsu memeriksa setiap kueri — himpunan placeholder $N HARUS == {1..jumlah argumen}.
"""
import ast
import re
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

APP = Path(__file__).resolve().parents[2] / "app"


class Conn:
    def __init__(self):
        self.langgar, self.n = [], 0

    def _cek(self, sql, args):
        self.n += 1
        ph = {int(x) for x in re.findall(r"\$(\d+)", sql)}
        if ph != set(range(1, len(args) + 1)):
            self.langgar.append((sorted(ph), len(args), " ".join(sql.split())[:120]))

    async def execute(self, sql, *a):
        self._cek(sql, a)

    async def fetchval(self, sql, *a):
        self._cek(sql, a)
        return 0

    async def fetch(self, sql, *a):
        self._cek(sql, a)
        return []

    async def fetchrow(self, sql, *a):
        self._cek(sql, a)
        return None

    def transaction(self):
        class _T:
            async def __aenter__(s):
                return None

            async def __aexit__(s, *e):
                return False
        return _T()


def _pasang(monkeypatch, mod, conn):
    class _Acq:
        async def __aenter__(self):
            return conn

        async def __aexit__(self, *a):
            return False

    async def _pool():
        return SimpleNamespace(acquire=lambda: _Acq())

    monkeypatch.setattr(mod, "get_pool", _pool)


def _req():
    return SimpleNamespace(state=SimpleNamespace(user={"tenant_id": "kaos-biru-konveksi",
                                                       "user_id": "22222222-2222-2222-2222-222222222222"}))


KASUS = {
    "bank_transfers": ("list_bank_transfers", dict(status=None, from_bank_id=None, to_bank_id=None, date_from=date(2026, 9, 1),
                                                    date_to=date(2026, 9, 30), skip=0, limit=20, sort_by="created_at", sort_order="desc")),
    "fixed_assets": ("list_fixed_assets", dict(skip=0, limit=20, status=None, category_id=None, warehouse_id=None)),
    "purchase_orders": ("list_purchase_orders", dict(status=None, vendor_id=None, date_from=date(2026, 9, 1), date_to=date(2026, 9, 30),
                                                      skip=0, limit=20, sort_by="created_at", sort_order="desc")),
    "stock_adjustments": ("list_stock_adjustments", dict(status=None, adjustment_type=None, date_from=date(2026, 9, 1),
                                                          date_to=date(2026, 9, 30), skip=0, limit=20, sort_by="created_at", sort_order="desc")),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("modul", sorted(KASUS))
@pytest.mark.parametrize("search", ["kaos", "kaos biru"])
async def test_placeholder_sama_dengan_argumen(monkeypatch, modul, search):
    import importlib
    mod = importlib.import_module(f"app.routers.{modul}")
    fn, kw = KASUS[modul]
    conn = Conn()
    _pasang(monkeypatch, mod, conn)
    await getattr(mod, fn)(_req(), search=search, **kw)
    assert conn.n >= 2 and not conn.langgar, conn.langgar


def _naik(st, nama):
    return (isinstance(st, ast.AugAssign) and isinstance(st.op, ast.Add) and isinstance(st.target, ast.Name)
            and st.target.id == nama and isinstance(st.value, ast.Constant) and st.value.value == 1)


def _akhir_naik(blok, nama):
    if not blok:
        return False
    a = blok[-1]
    if _naik(a, nama):
        return True
    if isinstance(a, (ast.For, ast.AsyncFor, ast.While)):
        return _akhir_naik(a.body, nama)
    if isinstance(a, ast.If):
        return _akhir_naik(a.body, nama) and bool(a.orelse) and _akhir_naik(a.orelse, nama)
    if isinstance(a, ast.Expr) and len(blok) >= 2:
        return _akhir_naik(blok[:-1], nama)
    return False


def _pindai(src):
    out = []
    for n in ast.walk(ast.parse(src)):
        for f in ("body", "orelse"):
            blok = getattr(n, f, None)
            if isinstance(blok, list):
                for i, st in enumerate(blok):
                    if i and isinstance(st, ast.AugAssign) and isinstance(st.target, ast.Name) and _naik(st, st.target.id):
                        p = blok[i - 1]
                        if isinstance(p, ast.If) and p.orelse and _akhir_naik(p.body, st.target.id) and _akhir_naik(p.orelse, st.target.id):
                            out.append(st.lineno)
    return out


def test_pemindai_bisa_merah():
    buruk = "def f(w, p):\n    i = 2\n    if w:\n        if len(w) == 1:\n            p.append(w)\n            i += 1\n        else:\n            for x in w:\n                p.append(x)\n                i += 1\n        i += 1\n"
    baik = "def f(w, p):\n    i = 2\n    if w:\n        p.append(w)\n        i += 1\n"
    assert _pindai(buruk) == [11] and _pindai(baik) == []


def test_tak_ada_kenaikan_ganda_di_app():
    temuan = []
    for p in APP.rglob("*.py"):
        try:
            for ln in _pindai(p.read_text()):
                temuan.append(f"{p.relative_to(APP)}:{ln}")
        except SyntaxError:
            pass
    assert not temuan, temuan
