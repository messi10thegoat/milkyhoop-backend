"""FASE C — gerbang keputusan PIPELINE ENTITAS V2. MURNI: tanpa DB, tanpa LLM.

Satu pertanyaan yang dijawab berkas ini: sesudah semua entitas dicari,
GELEMBUNG APA yang muncul di layar user? Jawabannya ditentukan oleh sebuah
tabel urut, dan hanya oleh itu — tidak oleh urutan pemanggilan, tidak oleh
LLM, tidak oleh apa yang kebetulan tersisa di payload.

TABEL URUT (dibaca dari atas, yang pertama cocok menang):

  1. ada >= 1 TIDAK_ADA  -> TAWARAN_DAFTAR
  2. ada >= 1 AMBIGU     -> PIL
  3. semua DITEMUKAN     -> KARTU

TIDAK_ADA MENANG ATAS AMBIGU, dan itu keputusan sadar, bukan urutan yang
kebetulan. Alasannya: dua pertanyaan yang berbeda jenis tidak boleh
dicampur dalam satu gelembung. "Barang X belum ada, daftarkan?" menuntut
user MEMBUAT sesuatu; "yang mana dari 3 ini?" menuntut user MEMILIH.
Menggabungkannya menghasilkan gelembung yang tak bisa dijawab dengan satu
tindakan. Kalau keduanya ada, yang ditanyakan lebih dulu adalah yang
menghalangi paling keras: entitas yang sama sekali tidak ada.

Daftar KOSONG menghasilkan KARTU: "semua DITEMUKAN" benar secara hampa
untuk nol entitas, dan tak ada apa pun yang perlu ditanyakan. Ini
dinyatakan supaya tidak jadi perilaku yang tak sengaja.

LARANGAN KUTIP KALIMAT (kaidah yang diuji, bukan imbauan). Yang masuk ke
pesan hanyalah POTONGAN nama entitas. Bila potongan itu ternyata sebuah
kalimat — lebih dari BATAS_POTONGAN huruf, atau memuat kata perintah —
maka nama itu bukan nama, melainkan sisa kalimat user yang bocor dari
ekstraksi. Mengutipnya menghasilkan gelembung seperti
`"buat penawaran untuk toko merdeka 50 kaos" belum terdaftar di master
barang` — kalimat yang membuat user merasa sistemnya rusak, dan tak bisa
ditindaklanjuti. Karena itu potongan seperti itu DITOLAK (tidak dikutip
sama sekali) dan digantikan kalimat yang jujur bahwa namanya tak terbaca.

Ketegangan yang disadari: daftar kata perintah adalah daftar kata, dan
`gerbang_entitas` sudah pernah memperingatkan bahwa daftar semacam itu akan
salah untuk sebagian tenant (bayangkan barang bernama "Kaos Untuk Anak").
Batasnya dipersempit supaya kerugiannya sekecil mungkin: kata perintah
hanya berarti bila potongan itu punya LEBIH DARI DUA kata — nama pendek
seperti "Kaos Untuk Anak" tetap dikutip utuh, sementara kalimat penuh
ditolak. Ini kompromi yang dipilih sadar, bukan kelalaian.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Sequence, Tuple

from .hasil_resolve import HasilResolve

# Batas panjang potongan yang boleh muncul di pesan.
BATAS_POTONGAN = 80

# Kata perintah: penanda bahwa yang kita pegang adalah kalimat, bukan nama.
KATA_PERINTAH = frozenset({"buat", "catat", "penawaran", "untuk"})

# Berapa kata minimum sebelum KATA_PERINTAH dianggap bukti kalimat.
MIN_KATA_KALIMAT = 3

JENIS_TAWARAN = "TAWARAN_DAFTAR"
JENIS_PIL = "PIL"
JENIS_KARTU = "KARTU"

# Kata benda + nama master per tipe. Kalimat untuk `item` sengaja PERSIS
# seperti spesifikasi tiket; tipe lain memakai bentuk yang sama dengan kata
# yang benar, supaya pelanggan yang tak terdaftar tidak disebut "barang".
_KATA: Dict[str, Tuple[str, str]] = {
    "item": ("barang", "master barang"),
    "customer": ("pelanggan", "master pelanggan"),
    "vendor": ("vendor", "master vendor"),
    "akun": ("akun", "daftar akun"),
    "bank": ("rekening", "master kas & bank"),
}


@dataclass(frozen=True)
class Keputusan:
    """Apa yang harus digambar. `extra_data` masuk apa adanya ke amplop FE."""

    jenis: str
    pesan: str
    opsi: Tuple[Dict[str, str], ...] = field(default=())
    extra_data: Dict[str, Any] = field(default_factory=dict)


def _kata(tipe: str) -> Tuple[str, str]:
    return _KATA.get(tipe, ("entitas", "master"))


def potongan_aman(mentah: str) -> str | None:
    """Potongan yang boleh dikutip, atau None bila ia ternyata kalimat.

    None BUKAN kegagalan: ia jawaban "ini tidak layak dikutip". Pemanggil
    wajib menyusun kalimat yang tetap bisa dimengerti tanpa potongan itu —
    bukan mencetak string kosong dan bukan mencetak kalimatnya.
    """
    t = " ".join(str(mentah or "").split())
    if not t:
        return None
    if len(t) > BATAS_POTONGAN:
        return None
    kata = t.casefold().split()
    if len(kata) >= MIN_KATA_KALIMAT and any(k in KATA_PERINTAH for k in kata):
        return None
    return t


def _rangkai(bagian: Sequence[str]) -> str:
    return ", ".join(bagian)


def _pesan_tawaran(tidak_ada: List[HasilResolve]) -> str:
    kata, master = _kata(tidak_ada[0].tipe)
    if len(tidak_ada) == 1:
        p = potongan_aman(tidak_ada[0].mentah)
        if p is None:
            return (
                "Satu " + kata + " belum terdaftar di " + master + ", dan "
                "namanya tidak terbaca dari pesan Anda. Tulis nama " + kata +
                "nya saja, lalu saya daftarkan."
            )
        return p + " belum terdaftar di " + master + ". Daftarkan sekarang?"

    terbaca = [p for p in (potongan_aman(h.mentah) for h in tidak_ada) if p]
    n = len(tidak_ada)
    if not terbaca:
        return (
            str(n) + " " + kata + " belum terdaftar di " + master + ", dan "
            "namanya tidak terbaca dari pesan Anda. Tulis nama-namanya saja, "
            "lalu saya daftarkan."
        )
    pesan = str(n) + " " + kata + " belum terdaftar: " + _rangkai(terbaca) + "."
    if len(terbaca) < n:
        pesan += (
            " (" + str(n - len(terbaca)) + " lagi namanya tidak terbaca dari "
            "pesan Anda.)"
        )
    return pesan + " Daftarkan sekarang?"


def _opsi_tawaran(tipe: str) -> Tuple[Dict[str, str], ...]:
    kata, _ = _kata(tipe)
    return (
        {"label": "Daftarkan", "value": "daftarkan", "description": ""},
        {
            "label": "Ganti " + kata + " lain",
            "value": "ganti",
            "description": "",
        },
        {"label": "Batal", "value": "batal", "description": ""},
    )


def _pesan_pil(h: HasilResolve) -> str:
    kata, _ = _kata(h.tipe)
    p = potongan_aman(h.mentah)
    if p is None:
        return (
            "Ada " + str(len(h.kandidat)) + " " + kata + " yang mungkin cocok, "
            "tapi nama yang Anda tulis tidak terbaca utuh. Yang mana?"
        )
    return '"' + p + '" cocok dengan ' + str(len(h.kandidat)) + " " + kata + ". Yang mana?"


def putuskan(hasil: List[HasilResolve]) -> Keputusan:
    """Tabel urut. Fungsi MURNI — hasil hanya bergantung pada argumennya."""
    tidak_ada = [h for h in hasil if h.status == "TIDAK_ADA"]
    if tidak_ada:
        return Keputusan(
            jenis=JENIS_TAWARAN,
            pesan=_pesan_tawaran(tidak_ada),
            opsi=_opsi_tawaran(tidak_ada[0].tipe),
            extra_data={
                "tipe": tidak_ada[0].tipe,
                # Nama MENTAH diteruskan utuh ke lapisan berikutnya walaupun
                # ia tidak dikutip di kalimat: yang dilarang adalah
                # MENAMPILKANNYA, bukan MENYIMPANNYA. Kaidah 3 — nama mentah
                # tidak pernah hilang.
                "mentah": [h.mentah for h in tidak_ada],
                "baris_index": [h.baris_index for h in tidak_ada],
            },
        )

    ambigu = [h for h in hasil if h.status == "AMBIGU"]
    if ambigu:
        # SATU BARIS SAJA, yang AMBIGU pertama. Menanyakan beberapa baris
        # sekaligus menghasilkan gelembung yang jawabannya tak bisa
        # dipetakan kembali ke baris mana pun dengan pasti.
        h = ambigu[0]
        return Keputusan(
            jenis=JENIS_PIL,
            pesan=_pesan_pil(h),
            opsi=tuple(
                {"label": k.nama, "value": k.id, "description": ""}
                for k in h.kandidat
            )
            + ({"label": "Bukan semuanya", "value": "bukan_semuanya", "description": ""},),
            extra_data={
                "tipe": h.tipe,
                "mentah": h.mentah,
                # baris_index IKUT supaya jawaban ditulis ke BARIS ITU, bukan
                # selalu baris 0. Tanpa ini, dokumen tiga baris yang ambigu di
                # baris ketiga akan menimpa baris pertama — kekeliruan yang
                # tak terlihat selama semua uji hanya punya satu baris.
                "baris_index": h.baris_index,
                "sisa_ambigu": len(ambigu) - 1,
            },
        )

    return Keputusan(
        jenis=JENIS_KARTU,
        pesan="",
        opsi=(),
        extra_data={"terikat": [{"baris_index": h.baris_index, "id": h.id} for h in hasil]},
    )
