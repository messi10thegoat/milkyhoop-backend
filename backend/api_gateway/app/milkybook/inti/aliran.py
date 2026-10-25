"""#10 aliran (SSE) + kotak masuk Milkybook (kontrak T3 kelima, disepakati FRONTEND/MASTER 25 Okt; opsi A).

Yang mengalir LANGSUNG: langkah tool, kartu, kartu_status, nav, galat. Teks jawaban dikirim UTUH sesudah lolos penjaga
angka/tanggal (tanpa potongan token). `langkah` hanya hidup (tidak masuk tampilan / GET /sesi).
Peristiwa dipancarkan lewat ContextVar: inti putaran tidak tahu apakah ada pendengar (JSON = pendengar mengumpulkan).
Kotak masuk: pesan pengguna yang datang saat giliran berjalan; dibaca inti SEBELUM tiap panggilan model (bukan saat
Setuju menulis: Setuju atomik, pesan diproses sesudahnya sebagai giliran baru).
"""
from __future__ import annotations

import asyncio
import hashlib
from contextvars import ContextVar
from typing import Any, Awaitable, Callable, List, Optional

MAKS_ANTRE = 5             # pesan per sesi (pagar MASTER 25 Okt)
MAKS_TEKS_ANTRE = 2000     # karakter per pesan antre
DETIK_DETAK = 15           # ": ping" (Cloudflare memutus koneksi tanpa byte ~100 dtk)

# Label langkah: Indonesia ringkas <= 4 kata, tanpa "(...)" (pagar MASTER). Cadangan per kelas, lalu "Memproses".
LABEL_LANGKAH = {
    "cari_pelanggan": "Mencari pelanggan", "cari_barang": "Mencari barang", "opsi_pajak": "Membaca kode pajak",
    "bawaan_penawaran": "Membaca bawaan penawaran", "daftar_penawaran": "Membaca daftar penawaran",
    "detail_penawaran": "Membaca penawaran", "ringkasan_penawaran": "Membaca ringkasan penawaran",
    "penawaran_segera_kedaluwarsa": "Memeriksa masa berlaku", "riwayat_penawaran": "Membaca riwayat penawaran",
    "hitung_penawaran": "Menghitung total", "hitung_tanggal": "Menghitung tanggal",
    "hitung_harga_sasaran": "Menghitung harga sasaran", "tutup_kartu_tinjau": "Menutup kartu Tinjau",
    "daftar_pesanan": "Membaca daftar pesanan", "daftar_faktur": "Membaca daftar faktur",
    "ringkasan_faktur": "Membaca ringkasan faktur", "ringkasan_piutang": "Membaca ringkasan piutang",
    "daftar_penerimaan": "Membaca penerimaan", "daftar_uang_muka": "Membaca uang muka",
    "daftar_pengiriman": "Membaca pengiriman", "ringkasan_usaha": "Membaca ringkasan usaha",
}
_LABEL_KELAS = {"READ": "Membaca data", "PREVIEW": "Memeriksa pratinjau", "WRITE": "Menyiapkan kartu Tinjau",
                "NAV": "Membuka halaman"}
TANPA_LANGKAH = {"perlu_penulis"}  # pengalihan model: tak dipancarkan


def label_langkah(nama: str, kelas: str = "") -> str:
    return LABEL_LANGKAH.get(nama) or _LABEL_KELAS.get(kelas) or "Memproses"


def sidik_pesan(teks: str) -> str:
    """Audit kotak masuk: hash + panjang, tanpa teks mentah (V465)."""
    return f"{hashlib.sha256(teks.encode()).hexdigest()[:16]}:{len(teks)}"


class Aliran:
    """Pendengar satu permintaan. pancar() sinkron (put_nowait) supaya bisa dipanggil dari mana saja di inti."""

    def __init__(self, ambil_antre: Optional[Callable[[], Awaitable[List[dict]]]] = None, boleh_sisip: bool = True):
        self.antrian: "asyncio.Queue[Optional[dict]]" = asyncio.Queue()
        self.semua: List[dict] = []          # urut, tanpa langkah/selesai (-> tampilan & JSON)
        self.terkirim: set = set()           # id() peristiwa yang sudah dipancarkan
        # Rujukan KUAT tiap peristiwa terkirim: tanpa ini dict yang sudah diambil konsumen dibuang GC, id()-nya dipakai
        # ulang dict baru, lalu peristiwa baru dianggap "sudah terkirim" dan HILANG (uji nyata kaos 25 Okt MB5:
        # 'selesai' + satu langkah tak sampai; reproduksi: 1 dari 2000 sampai).
        self._rujukan: List[dict] = []
        self.selesai: Optional[dict] = None  # hanya 'selesai' TERAKHIR yang dipancarkan, di akhir
        self.ambil_antre = ambil_antre
        self.boleh_sisip = boleh_sisip
        self.antre_dibaca: List[str] = []    # sidik pesan antre yang dibaca giliran ini (audit)

    def pancar(self, e: dict) -> None:
        if id(e) in self.terkirim:
            return
        self.terkirim.add(id(e))
        self._rujukan.append(e)
        if e.get("jenis") == "selesai":
            self.selesai = e
            return
        if e.get("jenis") != "langkah":
            self.semua.append(e)
        self.antrian.put_nowait(e)

    def tutup(self) -> None:
        if self.selesai is not None:
            self.antrian.put_nowait(self.selesai)
        self.antrian.put_nowait(None)


_ALIRAN: ContextVar[Optional[Aliran]] = ContextVar("milkybook_aliran", default=None)


def aktif() -> Optional[Aliran]:
    return _ALIRAN.get()


def pasang(a: Aliran):
    return _ALIRAN.set(a)


def lepas(token) -> None:
    _ALIRAN.reset(token)


def pancar(e: dict) -> None:
    a = _ALIRAN.get()
    if a is not None:
        a.pancar(e)


class Peristiwa(list):
    """Daftar peristiwa inti yang ikut memancarkan setiap anggota baru ke pendengar aktif (bila ada)."""

    def append(self, e: Any) -> None:  # type: ignore[override]
        super().append(e)
        pancar(e)

    def extend(self, es) -> None:  # type: ignore[override]
        for e in es:
            self.append(e)

    def __iadd__(self, es):  # type: ignore[override]
        self.extend(es)
        return self
