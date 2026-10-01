"""Terbilang rupiah -- SATU sumber (P3 SO-dokumen, 1 Okt 2026).

Dulu dua salinan privat IDENTIK (routers/receive_payments._terbilang, routers/customer_deposits._terbilang);
badan dipindah VERBATIM ke sini, keduanya kini menunjuk ke sini. Satu tambahan: bilangan negatif -> "Minus ..."
(dulu satuan[-n] = kata salah, diam-diam).
"""


def terbilang(n) -> str:
    """Konversi bilangan bulat rupiah ke kata Bahasa Indonesia ("Satu Juta Lima Ratus Ribu Rupiah")."""
    n = int(n)
    if n == 0:
        return "Nol Rupiah"
    if n < 0:
        return "Minus " + terbilang(-n)
    satuan = [
        "", "Satu", "Dua", "Tiga", "Empat", "Lima",
        "Enam", "Tujuh", "Delapan", "Sembilan", "Sepuluh", "Sebelas",
    ]

    def _to_words(x: int) -> str:
        if x < 12:
            return satuan[x]
        elif x < 20:
            return _to_words(x - 10) + " Belas"
        elif x < 100:
            return _to_words(x // 10) + " Puluh" + (
                " " + _to_words(x % 10) if x % 10 else ""
            )
        elif x < 200:
            return "Seratus" + (" " + _to_words(x - 100) if x - 100 else "")
        elif x < 1000:
            return _to_words(x // 100) + " Ratus" + (
                " " + _to_words(x % 100) if x % 100 else ""
            )
        elif x < 2000:
            return "Seribu" + (" " + _to_words(x - 1000) if x - 1000 else "")
        elif x < 1_000_000:
            return _to_words(x // 1000) + " Ribu" + (
                " " + _to_words(x % 1000) if x % 1000 else ""
            )
        elif x < 1_000_000_000:
            return _to_words(x // 1_000_000) + " Juta" + (
                " " + _to_words(x % 1_000_000) if x % 1_000_000 else ""
            )
        elif x < 1_000_000_000_000:
            return _to_words(x // 1_000_000_000) + " Miliar" + (
                " " + _to_words(x % 1_000_000_000) if x % 1_000_000_000 else ""
            )
        else:
            return _to_words(x // 1_000_000_000_000) + " Triliun" + (
                " " + _to_words(x % 1_000_000_000_000)
                if x % 1_000_000_000_000
                else ""
            )

    return _to_words(n).strip() + " Rupiah"
