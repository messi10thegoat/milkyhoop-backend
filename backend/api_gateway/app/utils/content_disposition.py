"""Content-Disposition untuk PDF dokumen.

Nama file = komponen disanitasi (karakter tak sah -> '-') + `.pdf`, plus `filename*` UTF-8
(RFC 5987) supaya nama non-ASCII (mis. display_name tenant) utuh di browser modern, dengan
fallback ASCII di `filename=` untuk klien lama.
"""
import re
from urllib.parse import quote

# Karakter tak sah untuk nama file lintas-OS + kontrol.
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_filename(name: str) -> str:
    """Bersihkan satu komponen nama file. Karakter tak sah -> '-'. Kosong -> 'dokumen'."""
    s = _ILLEGAL.sub("-", (name or "").strip())
    s = s.strip(". ")  # titik/spasi di ujung bermasalah di Windows
    return s or "dokumen"


def pdf_content_disposition(base_name: str, disposition: str = "inline") -> str:
    """Nilai header Content-Disposition untuk sebuah PDF.

    base_name TANPA '.pdf'. Balas: `<disposition>; filename="<ascii>.pdf"; filename*=UTF-8''<pct>`.
    Nomor ber-'/' menjadi '-'; nama non-ASCII utuh di filename*.
    """
    full = sanitize_filename(base_name) + ".pdf"
    ascii_fallback = full.encode("ascii", "replace").decode("ascii").replace("?", "-")
    star = quote(full, safe="")
    return f"{disposition}; filename=\"{ascii_fallback}\"; filename*=UTF-8''{star}"
