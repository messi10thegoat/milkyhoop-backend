"""Pelaksana: menjalankan tool sebagai permintaan HTTP ke rute BE yang SAMA dengan yang dipakai FE.

Token pengguna diteruskan apa adanya, jadi middleware autentikasi, izin (permission_middleware
first-match), tenant, dan pembatas laju tetap berlaku (Law 10). Milkybook tidak punya jalan
pintas ke DB.

Produksi: `PelaksanaASGI` (httpx ASGITransport ke app FastAPI di proses yang sama).
Eval/unit: implementasi stub dengan antarmuka yang sama.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Protocol


@dataclass
class JawabanBE:
    status: int
    isi: Any
    header: Dict[str, str] = field(default_factory=dict)

    @property
    def replay(self) -> bool:
        return self.header.get("x-idempotent-replay", "").lower() == "true"


class Pelaksana(Protocol):
    async def kirim(
        self,
        metode: str,
        jalur: str,
        *,
        query: Optional[dict] = None,
        badan: Optional[bytes] = None,
        header: Optional[Dict[str, str]] = None,
    ) -> JawabanBE: ...


class PelaksanaASGI:
    def __init__(self, app, otorisasi: str, batas_waktu: float = 30.0):
        self._app = app
        self._otorisasi = otorisasi  # nilai header Authorization milik pengguna
        self._batas = batas_waktu

    async def kirim(self, metode, jalur, *, query=None, badan=None, header=None) -> JawabanBE:
        import httpx

        h = {"Authorization": self._otorisasi, "X-Milkybook": "1"}
        if badan is not None:
            h["Content-Type"] = "application/json"
        h.update(header or {})
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self._app), base_url="http://milkybook.internal", timeout=self._batas
        ) as c:
            r = await c.request(metode, jalur, params=query, content=badan, headers=h)
        try:
            isi = r.json()
        except ValueError:
            isi = {"teks": r.text[:2000]}
        return JawabanBE(r.status_code, isi, {k.lower(): v for k, v in r.headers.items()})
