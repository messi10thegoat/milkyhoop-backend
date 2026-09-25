"""KEBOCORAN LINTAS-TENANT tier1_profile (26 Sep 2026).

Kedelapan kueri konteks bot (top pelanggan/vendor/item, pola bayar, gudang, tarif pajak, hitungan jatuh
tempo) dulu TANPA predikat tenant_id, bersandar pada set_config('app.tenant_id') — gateway = BYPASSRLS,
jadi RLS tak menyaring apa pun. Diukur baca-saja: konteks grapgrap/kaos/adhita IDENTIK (campuran), mis.
grapgrap (non-PKP) diberi "Tax rate default: 11%" milik kaos, nama pelanggan grapgrap masuk prompt kaos.
"""
import ast
import re
from pathlib import Path

import pytest

from app.services.unified_agent import tier1_profile as T1

SRC = Path(T1.__file__).read_text()
TENANT = "grapgrap-manado"
FUNGSI = ["_query_top_entities", "_query_payment_patterns", "_query_warehouse_defaults", "_query_overdue_counts"]


def _sql_tanpa_tenant(src: str):
    """String SQL (ber-FROM) yang TIDAK memfilter `tenant_id = $1` pada tiap tabel/alias utama."""
    t = ast.parse(src)
    salah = []
    for n in ast.walk(t):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and re.search(r"\bFROM\b", n.value) \
                and re.search(r"\bSELECT\b", n.value):
            q = n.value
            if not re.search(r"\btenant_id\s*=\s*\$1\b", q):
                salah.append(n.lineno)
    return salah


def test_semua_sql_tier1_memfilter_tenant_eksplisit():
    assert _sql_tanpa_tenant(SRC) == []
    jumlah = len([n for n in ast.walk(ast.parse(SRC)) if isinstance(n, ast.Constant) and isinstance(n.value, str)
                  and re.search(r"\bFROM\b", n.value) and re.search(r"\bSELECT\b", n.value)])
    assert jumlah == 8, jumlah


def test_penjaga_bisa_merah():
    contoh = 'async def f(conn):\n    await conn.fetch("""SELECT c.nama FROM sales_invoices si JOIN customers c ON c.id = si.customer_id""")\n'
    assert _sql_tanpa_tenant(contoh) == [2]


def test_tanpa_current_date():
    assert "CURRENT_DATE" not in SRC.upper().replace("# ", "")


class _Conn:
    def __init__(self, log):
        self.log = log

    async def execute(self, q, *a):
        return "OK"

    async def fetch(self, q, *a):
        self.log.append((q, a))
        return []

    async def fetchrow(self, q, *a):
        self.log.append((q, a))
        return None

    def transaction(self):
        class _T:
            async def __aenter__(s):
                return None

            async def __aexit__(s, *e):
                return False
        return _T()


class _Pool:
    def __init__(self):
        self.log = []

    def acquire(self):
        pool = self

        class _Ctx:
            async def __aenter__(self):
                return _Conn(pool.log)

            async def __aexit__(self, *e):
                return False
        return _Ctx()


@pytest.mark.parametrize("nama", FUNGSI)
@pytest.mark.asyncio
async def test_setiap_kueri_mengoper_tenant_sebagai_param_1(nama):
    pool = _Pool()
    await getattr(T1, nama)(pool, TENANT)
    assert pool.log, nama
    for q, a in pool.log:
        assert a and a[0] == TENANT, (nama, a)
        pakai = {int(x) for x in re.findall(r"\$(\d+)", q)}
        assert pakai == set(range(1, len(a) + 1)), (nama, sorted(pakai), len(a))
        assert re.search(r"\btenant_id\s*=\s*\$1\b", q), nama


@pytest.mark.asyncio
async def test_jatuh_tempo_pakai_tanggal_bisnis_tenant():
    pool = _Pool()
    await T1._query_overdue_counts(pool, TENANT)
    assert len(pool.log) == 2
    for q, a in pool.log:
        assert "due_date < tanggal_bisnis($1)" in q
