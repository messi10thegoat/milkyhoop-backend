"""Kartu Tinjau: satu usulan WRITE yang menunggu Setuju.

Kontrak (rancangan §2):
- Payload dibekukan saat kartu lahir; Setuju mengirim byte yang SAMA (`badan`).
- `payload_hash` = hash_payload BE (utils/idempotency.py:291): sha256 json sort_keys, separators (",", ":").
- Satu kunci idempotency (uuid4) per kartu; dipakai ulang hanya untuk coba-ulang 5xx/timeout.
- Ubah isian = pratinjau ulang = kartu BARU + kunci BARU; kartu lama jadi BASI.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


def hash_payload(payload: Any) -> str:
    """Sama persis dengan app/utils/idempotency.py hash_payload (dikunci tes teks-sumber)."""
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode()
    ).hexdigest()


def badan_kanonik(payload: Any) -> bytes:
    return json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode()


class StatusKartu(str, Enum):
    MENUNGGU = "menunggu"
    DIJALANKAN = "dijalankan"
    SELESAI = "selesai"
    DITOLAK = "ditolak"
    BASI = "basi"
    GAGAL = "gagal"


@dataclass
class Kartu:
    sesi_id: str
    tool: str
    metode: str
    jalur: str
    payload: dict
    pratinjau: Any = None
    judul: str = ""
    kartu_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    kunci: str = field(default_factory=lambda: str(uuid.uuid4()))
    status: StatusKartu = StatusKartu.MENUNGGU
    dibuat: float = field(default_factory=time.time)
    hasil: Any = None
    tool_use_id: Optional[str] = None

    def __post_init__(self):
        self.badan: bytes = badan_kanonik(self.payload)
        self.payload_hash: str = hash_payload(self.payload)

    def untuk_klien(self) -> dict:
        return {
            "kartu_id": self.kartu_id,
            "tool": self.tool,
            "judul": self.judul,
            "payload": self.payload,
            "payload_hash": self.payload_hash,
            "pratinjau": self.pratinjau,
            "status": self.status.value,
        }
