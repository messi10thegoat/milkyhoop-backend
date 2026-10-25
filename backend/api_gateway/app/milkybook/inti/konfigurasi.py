"""Konfigurasi model Milkybook dari lingkungan (putusan MASTER 24 Okt, rancangan §11.11).

MILKYBOOK_MODEL       model penulis (bawaan MODEL_BAWAAN = claude-sonnet-5-5)
MILKYBOOK_MODEL_BACA  model giliran baca (bawaan MODEL_BACA = claude-haiku-5-5); KOSONG = Sonnet saja.
                      Dibaca SETIAP giliran lewat saklar -> mundur cepat tanpa deploy kode
                      (ubah .env + `compose up -d` = muat ulang env kontainer, bukan rilis).
MILKYBOOK_EFFORT      bawaan medium.
MILKYBOOK_BATAS_BULAN_USD  #11 pagar biaya per tenant per bulan (UTC), "bawaan=5,<tenant_id>=15"; bawaan kode =
                      BATAS_BULAN_BAWAAN (putusan pemilik LANGSUNG 25 Okt: grapgrap $15, lain $5; batas belanja Console
                      kunci Milkybook $20/bulan = batas keras semua tenant). "0" = tanpa pagar untuk tenant itu.
Kunci API: MILKYBOOK_ANTHROPIC_API_KEY (kunci Milkybook TERPISAH dengan batas belanja; tidak pernah dicetak).
"""
from __future__ import annotations

import os
from typing import Callable, Optional, Tuple

from .model import EFFORT_BAWAAN, MODEL_BACA, MODEL_BAWAAN


def model_baca_dari_env(env=os.environ) -> Optional[str]:
    v = env.get("MILKYBOOK_MODEL_BACA", MODEL_BACA)
    return v.strip() or None


def saklar_baca_dari_env(env=os.environ) -> Callable[[], bool]:
    return lambda: model_baca_dari_env(env) is not None


def pemanggil_dari_env(env=os.environ) -> Tuple[object, Optional[object], str]:
    """(pemanggil penulis, pemanggil baca atau None, effort). Impor SDK malas lewat PemanggilAnthropic."""
    from .model import PemanggilAnthropic
    kunci = env.get("MILKYBOOK_ANTHROPIC_API_KEY") or None
    penulis = PemanggilAnthropic(model=env.get("MILKYBOOK_MODEL", MODEL_BAWAAN), api_key=kunci)
    mb = model_baca_dari_env(env)
    baca = PemanggilAnthropic(model=mb, api_key=kunci) if mb else None
    return penulis, baca, env.get("MILKYBOOK_EFFORT", EFFORT_BAWAAN)


BATAS_BULAN_BAWAAN = "bawaan=5,grapgrap-manado=15"


def batas_bulan_dari_env(env=os.environ) -> Callable[[str], Optional[float]]:
    """tenant_id -> batas USD bulan ini (None = tanpa pagar). Dibaca SETIAP giliran (ubah .env tanpa rilis kode)."""
    def batas(tenant_id: str) -> Optional[float]:
        peta = {}
        for bagian in (env.get("MILKYBOOK_BATAS_BULAN_USD") or BATAS_BULAN_BAWAAN).split(","):
            if "=" in bagian:
                k, v = bagian.split("=", 1)
                try:
                    peta[k.strip()] = float(v)
                except ValueError:
                    continue
        v = peta.get(tenant_id, peta.get("bawaan"))
        return v if v and v > 0 else None
    return batas


def buat_putaran_dari_env(registri, audit, env=os.environ, **kw):
    """Putaran siap pakai router: hibrida bawaan (Haiku baca / Sonnet tulis), saklar mundur dibaca tiap giliran."""
    from .putaran import Putaran
    penulis, baca, effort = pemanggil_dari_env(env)
    return Putaran(registri, penulis, audit, model_nama=getattr(penulis, "model", ""), effort=effort,
                   pemanggil_baca=baca, saklar_baca=saklar_baca_dari_env(env), batas_bulan=batas_bulan_dari_env(env), **kw)
