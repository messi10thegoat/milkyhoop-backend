"""Audit `asisten_aksi`: satu baris per tool call dan per keputusan kartu. Append-only.

Tanpa prompt mentah dan tanpa payload mentah: hanya hash + id. PII hanya sebagai id.
Skema tabel: backend/migrations/V465__asisten_aksi.sql (+ _ROLLBACK). BELUM diterapkan (paket aktivasi: izin pemilik + slot).
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import List, Optional, Protocol


@dataclass
class BarisAudit:
    tenant_id: str
    user_id: str
    sesi_id: str
    peristiwa: str  # tool | kartu_lahir | kartu_setuju | kartu_tolak | kartu_basi | penjaga_angka | model | giliran
    tool: Optional[str] = None
    kelas: Optional[str] = None
    model: Optional[str] = None
    prompt_hash: Optional[str] = None
    payload_hash: Optional[str] = None
    kunci_idempotensi: Optional[str] = None
    kartu_id: Optional[str] = None
    disetujui_oleh: Optional[str] = None
    status_be: Optional[int] = None
    hasil: Optional[str] = None
    token_masuk: int = 0
    token_keluar: int = 0
    token_cache_baca: int = 0
    token_cache_tulis: int = 0
    eskalasi: Optional[bool] = None   # peristiwa 'giliran': giliran baca dialihkan ke model penulis?
    biaya_usd: Optional[float] = None  # peristiwa 'giliran': biaya dari usage (bukan tagihan; tagihan = Billing)
    waktu: float = field(default_factory=time.time)


class PenulisAudit(Protocol):
    async def tulis(self, baris: BarisAudit) -> None: ...


class AuditMemori:
    def __init__(self):
        self.baris: List[BarisAudit] = []

    async def tulis(self, baris: BarisAudit) -> None:
        self.baris.append(baris)

    def sebagai_dict(self) -> List[dict]:
        return [asdict(b) for b in self.baris]


_SQL = """
INSERT INTO asisten_aksi (tenant_id, user_id, sesi_id, peristiwa, tool, kelas, model, prompt_hash,
  payload_hash, kunci_idempotensi, kartu_id, disetujui_oleh, status_be, hasil,
  token_masuk, token_keluar, token_cache_baca, token_cache_tulis, eskalasi, biaya_usd, dibuat)
VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20, to_timestamp($21))
"""


class AuditPostgres:
    """Dipakai sesudah migrasi diterapkan (izin pemilik + slot). Filter tenant eksplisit di baris."""

    def __init__(self, pool):
        self._pool = pool

    async def tulis(self, b: BarisAudit) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                _SQL, b.tenant_id, b.user_id, b.sesi_id, b.peristiwa, b.tool, b.kelas, b.model, b.prompt_hash,
                b.payload_hash, b.kunci_idempotensi, b.kartu_id, b.disetujui_oleh, b.status_be, b.hasil,
                b.token_masuk, b.token_keluar, b.token_cache_baca, b.token_cache_tulis, b.eskalasi, b.biaya_usd, b.waktu,
            )
