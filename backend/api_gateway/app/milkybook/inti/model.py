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
# Hibrida (eval 24 Okt, §11.11): model murah untuk giliran BACA saja (tanpa tool tulis; eskalasi ke MODEL_BAWAAN).
# Aktif bila Putaran diberi pemanggil_baca. Belum disambung di router: menunggu putusan MASTER/pemilik.
MODEL_BACA = "claude-haiku-5-5"

# Harga per 1 juta token (USD), skill claude-api tabel 2026-10-06. Untuk laporan biaya eval saja.
HARGA = {
    "claude-opus-5-5": {"masuk": 4.00, "keluar": 20.00, "cache_baca": 0.20, "cache_tulis": 5.00},
    "claude-sonnet-5-5": {"masuk": 2.00, "keluar": 10.00, "cache_baca": 0.20, "cache_tulis": 2.50},
    "claude-haiku-5-5": {"masuk": 0.10, "keluar": 0.50, "cache_baca": 0.01, "cache_tulis": 0.125},
}
# cache_tulis (kolom lama) = 1,25x harga masuk (TTL 5 menit); biaya_usd memakai rincian TTL (1h = 2x).
# cache_baca Haiku 5.5 = 0,1x masuk -> HIPOTESIS (tabel skill tidak memuat cache read Haiku 5.5).


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
    """Tulis cache: 1,25x harga masuk untuk TTL 5 menit, 2x untuk 1 jam (skill claude-api, prompt-caching Economics).
    Rincian per TTL dari usage.cache_creation; tanpa rincian = dianggap 5 menit."""
    h = HARGA.get(model)
    if not h:
        return 0.0
    m = 1_000_000
    rinci = usage.get("cache_creation") or {}
    tulis_1h = rinci.get("ephemeral_1h_input_tokens", 0) or 0
    tulis_5m = rinci.get("ephemeral_5m_input_tokens")
    if tulis_5m is None:
        tulis_5m = (usage.get("cache_creation_input_tokens", 0) or 0) - tulis_1h
    return (
        usage.get("input_tokens", 0) * h["masuk"] / m
        + usage.get("output_tokens", 0) * h["keluar"] / m
        + usage.get("cache_read_input_tokens", 0) * h["cache_baca"] / m
        + tulis_5m * h["masuk"] * 1.25 / m
        + tulis_1h * h["masuk"] * 2.0 / m
    )


def sistem_berbreakpoint(system: List[dict], ttl: Optional[str]) -> List[dict]:
    """Breakpoint EKSPLISIT di blok system terakhir: prefiks tools+system (identik lintas tenant/pengguna ber-set
    izin sama) jadi entri cache BERSAMA. Diukur eval 24 Okt: tanpa ini, panggilan pertama tiap percakapan membaca 0 dan
    menulis ulang ~14,8K token (74% biaya). Ekor percakapan tetap lewat cache_control top-level (5 menit).
    Urutan TTL: 1h harus sebelum 5m (skill claude-api prompt-caching) -> breakpoint 1h di system, otomatis 5m di ekor.
    Isi volatil (tanggal, nama usaha) TIDAK boleh di system: ia di pesan [konteks] pertama (sesudah breakpoint)."""
    if not system or ttl is None:
        return system
    sal = [dict(b) for b in system]
    sal[-1]["cache_control"] = {"type": "ephemeral", **({"ttl": ttl} if ttl != "5m" else {})}
    return sal


class PemanggilModel(Protocol):
    async def __call__(
        self, *, system: List[dict], messages: List[dict], tools: List[dict], effort: str
    ) -> HasilModel: ...


class PemanggilAnthropic:
    """Kunci API: dari argumen eksplisit, TIDAK dibaca dari berkas repo. Tidak pernah dicetak."""

    def __init__(self, model: str = MODEL_BAWAAN, api_key: Optional[str] = None, max_tokens: int = 16000,
                 ttl_prefiks: Optional[str] = "1h"):
        import anthropic  # impor malas: suite unit tak butuh paketnya

        self.model = model
        self.max_tokens = max_tokens
        self.ttl_prefiks = ttl_prefiks
        self._client = anthropic.AsyncAnthropic(api_key=api_key) if api_key else anthropic.AsyncAnthropic()

    async def __call__(self, *, system, messages, tools, effort="medium") -> HasilModel:
        kw: Dict[str, Any] = dict(
            model=self.model,
            max_tokens=self.max_tokens,
            system=sistem_berbreakpoint(system, self.ttl_prefiks),
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
