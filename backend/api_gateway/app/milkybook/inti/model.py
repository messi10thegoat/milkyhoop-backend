"""Seam model: SATU antarmuka antara inti dan vendor LLM (putusan MASTER 24 Okt).

Paket modul tidak pernah memanggil vendor. Ganti vendor = implementasi baru `PemanggilModel`.
Implementasi pertama: Anthropic Messages API (skill claude-api, cache 2026-10-06):
- `claude-opus-5-5`: thinking selalu aktif (jangan kirim `disabled`); effort lewat output_config
  (bawaan `medium`); tool_choice any/tool = 400 -> `auto` + `strict`.
- `fallbacks: "default"` + beta `server-side-fallback-2026-07-01` untuk penolakan classifier.
- cache_control top-level (auto) atas prefiks stabil tools -> system.
- Konten asisten dikembalikan UTUH (termasuk blok thinking) dan wajib ditambahkan apa adanya
  ke riwayat: riwayat append-only (preserved thinking).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol

# Putusan MASTER 24 Okt sesudah eval 60 kasus x 3 model (rancangan §11.8): Sonnet 5.5 effort medium = bawaan
# (setara Opus di syarat mutlak, separuh biaya). Opus = cadangan analisis berat. Haiku belum untuk tulis.
MODEL_BAWAAN = "claude-sonnet-5-5"
EFFORT_BAWAAN = "medium"

# Harga per 1 juta token (USD), skill claude-api tabel 2026-10-06. Untuk laporan biaya eval saja.
HARGA = {
    "claude-opus-5-5": {"masuk": 4.00, "keluar": 20.00, "cache_baca": 0.20, "cache_tulis": 5.00},
    "claude-sonnet-5-5": {"masuk": 2.00, "keluar": 10.00, "cache_baca": 0.20, "cache_tulis": 2.50},
    "claude-haiku-5-5": {"masuk": 0.10, "keluar": 0.50, "cache_baca": 0.01, "cache_tulis": 0.125},
}
# cache_tulis = 1,25x harga masuk (TTL 5 menit); cache_baca Haiku 5.5 = 0,1x masuk -> HIPOTESIS
# untuk Haiku (tabel skill tidak memuat cache read Haiku 5.5).


@dataclass
class PanggilanTool:
    id: str
    nama: str
    masukan: Dict[str, Any]


@dataclass
class HasilModel:
    konten: List[Dict[str, Any]]  # blok asisten apa adanya (dict), untuk riwayat
    stop_reason: str
    usage: Dict[str, Any] = field(default_factory=dict)
    model: str = ""

    @property
    def teks(self) -> str:
        return "".join(b.get("text", "") for b in self.konten if b.get("type") == "text")

    @property
    def tool_calls(self) -> List[PanggilanTool]:
        return [
            PanggilanTool(b["id"], b["name"], b.get("input") or {})
            for b in self.konten
            if b.get("type") == "tool_use"
        ]


def biaya_usd(model: str, usage: Dict[str, Any]) -> float:
    h = HARGA.get(model)
    if not h:
        return 0.0
    m = 1_000_000
    return (
        usage.get("input_tokens", 0) * h["masuk"] / m
        + usage.get("output_tokens", 0) * h["keluar"] / m
        + usage.get("cache_read_input_tokens", 0) * h["cache_baca"] / m
        + usage.get("cache_creation_input_tokens", 0) * h["cache_tulis"] / m
    )


class PemanggilModel(Protocol):
    async def __call__(
        self, *, system: List[dict], messages: List[dict], tools: List[dict], effort: str
    ) -> HasilModel: ...


class PemanggilAnthropic:
    """Kunci API: dari argumen eksplisit, TIDAK dibaca dari berkas repo. Tidak pernah dicetak."""

    def __init__(self, model: str = MODEL_BAWAAN, api_key: Optional[str] = None, max_tokens: int = 16000):
        import anthropic  # impor malas: suite unit tak butuh paketnya

        self.model = model
        self.max_tokens = max_tokens
        self._client = anthropic.AsyncAnthropic(api_key=api_key) if api_key else anthropic.AsyncAnthropic()

    async def __call__(self, *, system, messages, tools, effort="medium") -> HasilModel:
        kw: Dict[str, Any] = dict(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=messages,
            tools=tools,
            tool_choice={"type": "auto"},
            output_config={"effort": effort},
            cache_control={"type": "ephemeral"},
        )
        if self.model in ("claude-opus-5-5", "claude-sonnet-5-5"):
            resp = await self._client.beta.messages.create(
                betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kw
            )
        else:
            # Haiku 5.5: tanpa fallback sisi server (skill: refusals have no server-side fallback).
            resp = await self._client.messages.create(**kw)
        konten = [b.model_dump(exclude_none=True) for b in resp.content]
        usage = resp.usage.model_dump(exclude_none=True) if resp.usage else {}
        return HasilModel(konten=konten, stop_reason=resp.stop_reason or "", usage=usage, model=resp.model)
