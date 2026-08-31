"""FASE A — bentuk data TUNGGAL untuk hasil pencarian entitas (PIPELINE V2).

Kenapa berkas ini ada. Jalur lama menjawab "entitas ini apa?" dengan
MENGUBAH payload di tempat: mengisi `item_id`, menimpa `description`,
kadang menulis `None` ke keduanya. Akibatnya jawaban resolver dan payload
adalah benda yang sama, sehingga tidak ada satu titik pun di mana kita bisa
bertanya "apa sebenarnya yang ditemukan untuk baris ke-3?" tanpa menebak
dari sisa-sisa payload. Tiga bug besar sesi ini (T168, T187, BUG-item-slot)
semuanya lahir dari lapisan yang MENGHAPUS nilai yang meragukan.

V2 memisahkan keduanya: pencarian menghasilkan `HasilResolve` — nilai beku,
append-only, yang SELALU membawa `mentah` (string persis yang user tulis)
apa pun hasilnya. Payload tidak disentuh sampai gerbang keputusan.

Invarian di `__post_init__` adalah PENGAMAN STRUKTURAL, bukan komentar:
bentuk yang mustahil tidak bisa dibuat sama sekali, jadi ia tidak bisa
mengalir diam-diam ke pesan user. "AMBIGU dengan satu kandidat" adalah bug
yang di jalur lama muncul sebagai pil disambiguasi berisi satu pilihan;
di sini ia meledak di tempat lahirnya.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Tuple

Status = Literal["DITEMUKAN", "AMBIGU", "TIDAK_ADA"]
Tipe = Literal["item", "customer", "vendor", "akun", "bank"]

STATUS_SAH: frozenset = frozenset({"DITEMUKAN", "AMBIGU", "TIDAK_ADA"})
TIPE_SAH: frozenset = frozenset({"item", "customer", "vendor", "akun", "bank"})


@dataclass(frozen=True)
class Kandidat:
    """Satu calon dari master. `id` string, `nama` nama MASTER (bukan ketikan user)."""

    id: str
    nama: str

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            raise ValueError("Kandidat.id wajib string tidak kosong")
        if not isinstance(self.nama, str) or not self.nama.strip():
            raise ValueError("Kandidat.nama wajib string tidak kosong")


@dataclass(frozen=True)
class HasilResolve:
    """Jawaban pencarian untuk SATU entitas.

    `mentah` = string persis yang user tulis. Ia TIDAK PERNAH hilang, tidak
    peduli status. Kaidah 3 tiket ini bersandar padanya: pesan yang disusun
    gerbang harus bisa menyebut nama yang user kenali, dan satu-satunya
    tempat nama itu masih utuh adalah di sini.

    `baris_index` = indeks baris items[] asalnya (None untuk pihak/akun/bank).
    Ia ikut sampai ke pesan supaya jawaban user ditulis ke BARIS ITU, bukan
    selalu baris 0 — kekeliruan yang di jalur lama tak terlihat karena
    kebanyakan uji hanya punya satu baris.
    """

    status: Status
    mentah: str
    tipe: Tipe
    baris_index: int | None = None
    id: str | None = None
    nama: str | None = None
    kandidat: Tuple[Kandidat, ...] = field(default=())

    def __post_init__(self) -> None:
        if self.status not in STATUS_SAH:
            raise ValueError(f"status tak dikenal: {self.status!r}")
        if self.tipe not in TIPE_SAH:
            raise ValueError(f"tipe tak dikenal: {self.tipe!r}")

        # Kaidah 3: nama mentah tidak pernah hilang. Kalau ia sudah kosong di
        # sini, tidak ada lapisan di hilir yang bisa memulihkannya.
        if not isinstance(self.mentah, str) or not self.mentah.strip():
            raise ValueError(
                "mentah kosong/whitespace — nama yang user tulis sudah hilang "
                "sebelum pencarian; itu bug hulu, bukan keadaan yang sah"
            )

        if self.baris_index is not None and (
            not isinstance(self.baris_index, int)
            or isinstance(self.baris_index, bool)
            or self.baris_index < 0
        ):
            raise ValueError("baris_index wajib int >= 0 atau None")

        if not isinstance(self.kandidat, tuple):
            raise ValueError("kandidat wajib tuple (nilai beku)")
        for k in self.kandidat:
            if not isinstance(k, Kandidat):
                raise ValueError("kandidat wajib berisi Kandidat")

        if self.status == "DITEMUKAN":
            if not (isinstance(self.id, str) and self.id.strip()):
                raise ValueError("DITEMUKAN wajib punya id")
            if not (isinstance(self.nama, str) and self.nama.strip()):
                raise ValueError("DITEMUKAN wajib punya nama")
        elif self.status == "AMBIGU":
            if len(self.kandidat) < 2:
                raise ValueError(
                    "AMBIGU wajib >= 2 kandidat — satu kandidat berarti "
                    "DITEMUKAN, dan pil berisi satu pilihan adalah bug"
                )
            if self.id is not None:
                raise ValueError("AMBIGU tidak boleh punya id (belum diputuskan)")
        else:  # TIDAK_ADA
            if self.id is not None:
                raise ValueError("TIDAK_ADA tidak boleh punya id")
            if self.kandidat:
                raise ValueError("TIDAK_ADA tidak boleh punya kandidat")
