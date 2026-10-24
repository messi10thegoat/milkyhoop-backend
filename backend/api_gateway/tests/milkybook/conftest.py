"""Fixture Milkybook: model palsu + BE palsu. Nol HTTP, nol LLM, nol DB.

Jalankan (Mac/kontainer): pytest -c pytest-milkybook.ini
Batas: menguji KODE KITA saat model mengembalikan bentuk X, bukan bahwa model memang begitu.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from app.milkybook.inti.model import HasilModel  # noqa: E402
from app.milkybook.inti.pelaksana import JawabanBE  # noqa: E402


class ModelPalsu:
    """Mengembalikan HasilModel dari naskah, mencatat setiap permintaan (salinan messages)."""

    def __init__(self, naskah):
        self.naskah = list(naskah)
        self.permintaan = []

    async def __call__(self, *, system, messages, tools, effort):
        import copy

        self.permintaan.append({"system": system, "messages": copy.deepcopy(messages), "tools": tools, "effort": effort})
        if not self.naskah:
            return HasilModel([{"type": "text", "text": "selesai"}], "end_turn", {}, "palsu")
        langkah = self.naskah.pop(0)
        return langkah(messages) if callable(langkah) else langkah


def teks(t):
    return HasilModel([{"type": "text", "text": t}], "end_turn", {"input_tokens": 10, "output_tokens": 5}, "claude-opus-5-5")


def panggil(*calls, teks_awal=""):
    konten = [{"type": "thinking", "thinking": "", "signature": "sig"}]
    if teks_awal:
        konten.append({"type": "text", "text": teks_awal})
    for i, (nama, masukan) in enumerate(calls):
        konten.append({"type": "tool_use", "id": f"tu_{nama}_{i}", "name": nama, "input": masukan})
    return HasilModel(konten, "tool_use", {"input_tokens": 100, "output_tokens": 20}, "claude-opus-5-5")


class BEPalsu:
    """Rute -> fungsi(metode, jalur, query, badan, header) -> JawabanBE. Mencatat semua panggilan."""

    def __init__(self, rute=None):
        self.rute = rute or {}
        self.panggilan = []

    async def kirim(self, metode, jalur, *, query=None, badan=None, header=None):
        self.panggilan.append({"metode": metode, "jalur": jalur, "query": query, "badan": badan, "header": dict(header or {})})
        f = self.rute.get((metode, jalur))
        if f is None:
            return JawabanBE(404, {"detail": "tak ada stub"})
        return f(query=query, badan=badan, header=header or {}) if callable(f) else f

    def tulis(self):
        return [p for p in self.panggilan if p["metode"] in ("POST", "PATCH", "PUT", "DELETE")]


async def izin_semua(jalur, metode):
    return {j: True for j in jalur}


@pytest.fixture
def be():
    return BEPalsu()
