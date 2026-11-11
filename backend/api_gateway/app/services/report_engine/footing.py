"""PEMBULATAN PER BARIS, TOTAL = Σ TAMPIL (laporan-fix-2 (b); pola Accurate, putusan MASTER 12 Nov; SATU fungsi untuk SEMUA laporan PSAK).

Bukti TESTER nyawit: L/R blok HPP 19.085.162 − 1.202.136 + 14.052 tampil total 17.897.079 padahal Σ baris tampil 17.897.078 (total dibulatkan dari
jumlah bersen, bukan dari baris yang dilihat pengguna). Aturan: tiap baris AKUN dibulatkan ke presisi tenant (HALF_UP; 0 = rupiah utuh), semua
total/hasil DIJUMLAH ULANG dari baris tampil (urut tampil: seksi -> total seksi -> laba/total berikutnya), jadi pembaca yang menjumlah dengan
kalkulator selalu cocok. Hanya `baris[]` (tampilan); medan lama float tak berubah (Law 9/25: hitung tetap Decimal penuh di engine).
Kas awal/akhir (saldo, bukan hasil jumlah) hanya dibulatkan per baris."""
from decimal import ROUND_HALF_UP, Decimal
from . import baris as B

NOL = Decimal(0)

# kunci baris total/hasil -> [(tanda, "seksi:<judul>" = Σ baris akun seksi itu | "kunci:<kunci>" = nilai tampil baris sebelumnya)]
RUMUS = {
    "laba_rugi": {
        "total_pendapatan": [(1, "seksi:pendapatan")], "total_hpp": [(1, "seksi:hpp")],
        "total_beban_usaha": [(1, "seksi:beban_usaha")], "total_lain": [(1, "seksi:lain")],
        "laba_kotor": [(1, "kunci:total_pendapatan"), (-1, "kunci:total_hpp")],
        "laba_usaha": [(1, "kunci:laba_kotor"), (-1, "kunci:total_beban_usaha")],
        "laba_sebelum_pajak": [(1, "kunci:laba_usaha"), (1, "kunci:total_lain")],
        "laba_bersih": [(1, "kunci:laba_sebelum_pajak"), (-1, "kunci:beban_pajak")],
    },
    "neraca": {
        "total_aset_lancar": [(1, "seksi:aset_lancar")], "total_aset_tidak_lancar": [(1, "seksi:aset_tidak_lancar")],
        "total_aset": [(1, "kunci:total_aset_lancar"), (1, "kunci:total_aset_tidak_lancar")],
        "total_liabilitas_jp": [(1, "seksi:liabilitas_jp")], "total_liabilitas_jpp": [(1, "seksi:liabilitas_jpp")],
        "total_liabilitas": [(1, "kunci:total_liabilitas_jp"), (1, "kunci:total_liabilitas_jpp")],
        "total_ekuitas": [(1, "seksi:ekuitas")],
        "total_liabilitas_ekuitas": [(1, "kunci:total_liabilitas"), (1, "kunci:total_ekuitas")],
    },
    "arus_kas": {
        "kas_neto_operasi": [(1, "seksi:operasi")], "kas_neto_investasi": [(1, "seksi:investasi")],
        "kas_neto_pendanaan": [(1, "seksi:pendanaan")],
        "kenaikan_kas": [(1, "kunci:kas_neto_operasi"), (1, "kunci:kas_neto_investasi"), (1, "kunci:kas_neto_pendanaan")],
    },
}


def bulat(x, presisi: int) -> Decimal:
    return Decimal(str(x)).quantize(Decimal(1) if presisi == 0 else Decimal("0.01"), rounding=ROUND_HALF_UP)


def footing(jenis: str, baris: list, presisi: int) -> list:
    """Ubah `baris` (nilai, tanda, label kas neto) di tempat; -> baris. jenis: laba_rugi | neraca | arus_kas."""
    rumus, nilai, seksi_jml, seksi = RUMUS[jenis], {}, {}, None
    for b in baris:
        if b["jenis"] == "judul":
            seksi = b["kunci"]
            seksi_jml.setdefault(seksi, NOL)
            continue
        k = b["kunci"]
        if b["jenis"] != "akun" and k in rumus:
            v = sum((t * (seksi_jml.get(s[6:], NOL) if s.startswith("seksi:") else nilai.get(s[6:], NOL)) for t, s in rumus[k]), NOL)
        else:
            v = bulat(b["nilai"], presisi)
            if b["jenis"] == "akun" and seksi is not None:
                seksi_jml[seksi] = seksi_jml.get(seksi, NOL) + v
        nilai[k] = v
        b["nilai"], b["tanda"] = B.nilai(v), B.tanda(v)
        if k.startswith("kas_neto_"):
            b["label"] = B.kas_neto_label(k[len("kas_neto_"):], v)
    return baris
