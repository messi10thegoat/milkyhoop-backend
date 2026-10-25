"""HOTFIX 25 Okt 2026: GET /api/bills/outstanding-summary = 500 LIVE (b78b361c, V457) -- koma daftar SELECT tertelan komentar
`-- V457: tagihan saja,` -> PostgresSyntaxError "at or near COALESCE". Tes ini menangkap kueri ASLI lewat koneksi tiruan lalu
memeriksa daftar SELECT sesudah komentar SQL dibuang: tiap ekspresi `... as <alias>` yang disusul ekspresi baru WAJIB berkoma.
Eksekusi nyata ke DB (READ ONLY, kaos + grapgrap-manado, kontrol merah pohon lama) = harness scripts/ops/uji-nyata-hf-outsum."""
import asyncio
import datetime
import re

import pytest

from app.services import bills_service as BS


class _Henti(Exception):
    pass


def _kueri_ringkasan() -> str:
    tangkap = []

    class Conn:
        async def fetchrow(self, q, *a):
            tangkap.append(q)
            raise _Henti

    class Pool:
        def acquire(self):
            class CM:
                async def __aenter__(s):
                    return Conn()

                async def __aexit__(s, *a):
                    return False
            return CM()

    async def tgl(conn, tenant_id):
        return datetime.date(2026, 10, 25)

    asli = BS.tanggal_dokumen
    BS.tanggal_dokumen = tgl
    try:
        with pytest.raises(_Henti):
            asyncio.run(BS.BillsService(Pool()).get_outstanding_summary("t"))
    finally:
        BS.tanggal_dokumen = asli
    assert len(tangkap) == 1
    return tangkap[0]


def _koma_hilang(sql: str) -> list:
    """Alias yang langsung disusul ekspresi baru tanpa koma (komentar SQL dibuang dulu)."""
    tanpa = re.sub(r"--[^\n]*", "", sql)
    daftar = tanpa[tanpa.index("SELECT", tanpa.index(")")):tanpa.rindex("FROM ap")]
    return re.findall(r"\bas (\w+)\s+(?=COUNT|COALESCE|\(|SUM|MAX)", daftar, flags=re.I)


def test_daftar_select_ringkasan_hutang_berkoma():
    q = _kueri_ringkasan()
    assert "as total_count," in q
    assert _koma_hilang(q) == []


def test_kontrol_merah_teks_lama_tertangkap():
    q = _kueri_ringkasan().replace("as total_count,  -- V457: tagihan saja", "as total_count  -- V457: tagihan saja,", 1)
    assert "as total_count," not in q
    assert _koma_hilang(q) == ["total_count"]
