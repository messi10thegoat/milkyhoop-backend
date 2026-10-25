"""(c) Peringkasan otomatis riwayat model (pola "Compacted" Claude Code; syarat MASTER 25 Okt).

- Pemicu: konteks panggilan model terakhir >= Putaran.ringkas_pada (meteran 100%), di AWAL giliran pengguna saja
  (tidak di tengah Setuju). Dua ucapan pengguna terakhir (dan semua sesudahnya) disimpan UTUH.
- Ringkasan model HANYA prosa: semua angka di ringkasan disamarkan "…" sebelum masuk riwayat.
- Angka / nomor dokumen / status kartu TIDAK berasal dari ringkasan: blok [fakta aplikasi] disusun dari data server
  (kartu sesi + nomor dokumen di hasil tool yang diringkas). Model diarahkan membaca ulang lewat tool bila butuh angka.
- Transkrip untuk peringkas: ucapan pengguna + teks asisten + NAMA tool; isi hasil tool tidak dikirim.
"""
from __future__ import annotations

import json
import re
from typing import Any, List, Optional

SIMPAN_UCAPAN = 2
MAKS_KODE = 20
_KODE_DOKUMEN = re.compile(r"\b[A-Z]{2,5}-\d{4}-\d{3,5}\b")  # QUO-2610-0039, SO-2610-0001
_ANGKA = re.compile(r"\d[\d.,]*")
_AWALAN_APLIKASI = ("[sistem]", "[penjaga]", "[ringkasan]")

PROMPT_RINGKAS = """Kamu meringkas percakapan antara pengguna aplikasi pembukuan dan asisten Milkybook supaya percakapan bisa dilanjutkan.
Tulis dalam bahasa Indonesia, maksimal 150 kata, prosa ringkas:
- tujuan pengguna dan apa yang sudah dikerjakan;
- keputusan, preferensi, dan koreksi pengguna;
- pertanyaan yang masih terbuka.
JANGAN menulis angka, jumlah, harga, persentase, tanggal, atau nomor dokumen: aplikasi menambahkan fakta itu dari server.
Sebut pelanggan/barang dengan namanya bila perlu. Jangan menambahkan apa pun yang tidak ada di percakapan."""


def _ucapan(m: dict) -> bool:
    """Pesan user yang berupa ucapan pengguna (bukan tool_result / pesan aplikasi)."""
    if m.get("role") != "user":
        return False
    c = m["content"]
    if isinstance(c, str):
        return not c.startswith(_AWALAN_APLIKASI)
    if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in c):
        return False
    return any(isinstance(b, dict) and b.get("type") == "text" and not b["text"].startswith(_AWALAN_APLIKASI) for b in c)


def titik_potong(messages: List[dict], simpan: int = SIMPAN_UCAPAN) -> Optional[int]:
    """Indeks pesan pertama yang disimpan utuh (ucapan ke-`simpan` dari belakang). None = terlalu pendek."""
    batas = [i for i, m in enumerate(messages) if _ucapan(m)]
    if len(batas) <= simpan:
        return None
    i = batas[-simpan]
    return i if i > 0 else None


def transkrip(messages: List[dict]) -> str:
    baris = []
    for m in messages:
        c = m["content"]
        blok = [{"type": "text", "text": c}] if isinstance(c, str) else c
        for b in blok:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text" and b["text"].strip():
                siapa = "Pengguna" if m["role"] == "user" and not b["text"].startswith(_AWALAN_APLIKASI) else (
                    "Aplikasi" if m["role"] == "user" else "Asisten")
                baris.append(f"{siapa}: {b['text'].strip()}")
            elif b.get("type") == "tool_use":
                baris.append(f"Asisten: (memakai alat {b.get('name')})")
    return "\n".join(baris)


def samarkan_angka(teks: str) -> str:
    return _ANGKA.sub("…", teks)


def _kode_dari(isi: Any, kumpul: List[str]) -> None:
    for k in _KODE_DOKUMEN.findall(isi if isinstance(isi, str) else json.dumps(isi, ensure_ascii=False, default=str)):
        if k not in kumpul and len(kumpul) < MAKS_KODE:
            kumpul.append(k)


def fakta_aplikasi(kartu: dict, dibuang: List[dict]) -> str:
    """Fakta dari data SERVER: status kartu (hasil tulis) + nomor dokumen di hasil tool yang diringkas."""
    kode: List[str] = []
    for m in dibuang:
        if m.get("role") == "user" and isinstance(m["content"], list):
            for b in m["content"]:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    _kode_dari(b.get("content"), kode)
    baris = []
    for k in kartu.values():
        nomor: List[str] = []
        if k.hasil is not None:
            _kode_dari(k.hasil, nomor)
        baris.append(f"- kartu {k.kartu_id} ({k.tool}): {k.status.value}" + (f", dokumen {', '.join(nomor)}" if nomor else ""))
    teks = ["[fakta aplikasi] Dari data server (bukan dari ringkasan):"]
    teks += baris or ["- belum ada kartu Tinjau."]
    if kode:
        teks.append("Nomor dokumen yang sudah dibahas: " + ", ".join(kode) + ".")
    teks.append("Ringkasan di atas tidak memuat angka. Untuk angka, total, tanggal, atau status terbaru, baca ulang lewat tool.")
    return "\n".join(teks)


def susun_riwayat(messages: List[dict], potong: int, ringkasan: str, konteks: str, fakta: str) -> List[dict]:
    """[ringkasan + fakta] digabung ke ucapan pertama yang disimpan (peran tetap berselang, awal = user)."""
    pembuka = "[ringkasan] Percakapan sebelumnya diringkas otomatis.\n" + (f"[konteks] {konteks}\n" if konteks else "")
    blok = {"type": "text", "text": f"{pembuka}{samarkan_angka(ringkasan.strip())}\n\n{fakta}"}
    simpan = [dict(m) for m in messages[potong:]]
    c = simpan[0]["content"]
    simpan[0]["content"] = [blok] + ([{"type": "text", "text": c}] if isinstance(c, str) else list(c))
    return simpan
