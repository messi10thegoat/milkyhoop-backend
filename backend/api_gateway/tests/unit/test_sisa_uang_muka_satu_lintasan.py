"""PERF-DAFTAR-SO (2) (16 Okt 2026): compute_deposit_remaining_many = SATU lintasan (bukan subkueri berkorelasi per uang muka).
Kesetaraan angka lama==baru di data nyata + 400 skenario sintetik: scripts/ops/uji-kesetaraan-sisa-uang-muka.py (Law 1/16/29)."""
import asyncio
import re
import uuid
from decimal import Decimal as D

from app.routers import customer_deposits as CD


def test_sql_satu_lintasan_tanpa_subkueri_berkorelasi_per_uang_muka():
    q = CD.SQL_SISA_UANG_MUKA
    assert "is_effective_journal" in q and q.count("is_effective_journal") == 1       # SEKALI per jurnal kandidat, bukan per baris
    assert q.count("UNION") == 3 and "UNION ALL" not in q                       # 4 sumber; UNION (dedup) = tiap (jurnal, uang muka) sekali, sama dengan OR-per-baris lama
    assert not re.search(r"COALESCE\(\(\s*SELECT SUM\(jl\.credit\)", q)             # bentuk lama (skalar berkorelasi) hilang
    assert "d.id" in q and "unnest($2::uuid[])" in q and "jl.account_id = $3" in q
    for tabel in ("receive_payments", "credit_notes", "receive_payment_allocations"):
        assert f"{tabel}" in q and "tenant_id = $1" in q


def test_pembungkus_memetakan_baris_ke_decimal_dan_kosong_ke_nol(monkeypatch):
    a, b = uuid.uuid4(), uuid.uuid4()
    dipanggil = {}

    class Conn:
        async def fetch(self, sql, *args):
            dipanggil["sql"], dipanggil["args"] = sql, args
            return [{"id": a, "remaining": D("125000.50")}, {"id": b, "remaining": None}]

    async def akun(conn, tenant, role):
        return uuid.UUID(int=7)
    monkeypatch.setattr(CD, "resolve_account_id_by_role", akun)
    hasil = asyncio.run(CD.compute_deposit_remaining_many(Conn(), "t1", [a, b]))
    assert hasil == {str(a): D("125000.50"), str(b): D("0")}
    assert dipanggil["sql"] is CD.SQL_SISA_UANG_MUKA and dipanggil["args"][0] == "t1" and dipanggil["args"][1] == [str(a), str(b)]
    assert asyncio.run(CD.compute_deposit_remaining_many(Conn(), "t1", [])) == {}
