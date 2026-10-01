"""Samarkan token tautan publik di log (P5 SO-dokumen, syarat tinjauan keamanan MASTER 1 Okt 2026).

Token ada di PATH (/api/public/d/<token>, /d/<token>) -> log akses uvicorn mencatatnya -> siapa pun yang membaca log
bisa membuka dokumen. Saringan ini mengganti token jadi *** di pesan DAN argumen rekaman log sebelum ditulis.
Nginx disamarkan terpisah (log_format + map di /etc/nginx).
"""
import logging
import re

POLA = re.compile(r"(/(?:api/public/)?d/)[A-Za-z0-9_-]{43}")


def samarkan(teks):
    return POLA.sub(r"\1***", teks) if isinstance(teks, str) else teks


class SaringTokenTautan(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = samarkan(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(samarkan(a) for a in record.args)
        elif isinstance(record.args, dict):
            record.args = {k: samarkan(v) for k, v in record.args.items()}
        return True


def pasang(nama_logger=("uvicorn.access", "uvicorn.error", "")) -> None:
    """Pasang SEKALI pada logger akses uvicorn (+ error + root). Idempoten."""
    for nama in nama_logger:
        lg = logging.getLogger(nama)
        if not any(isinstance(f, SaringTokenTautan) for f in lg.filters):
            lg.addFilter(SaringTokenTautan())
