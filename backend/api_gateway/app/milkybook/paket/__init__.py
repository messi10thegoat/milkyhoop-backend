"""Paket modul Milkybook: satu berkas per modul."""


def semua():
    """Paket yang dimuat pilot (urutan tetap: prefiks cache stabil)."""
    from . import penawaran, penjualan_baca
    return [penawaran.paket(), penjualan_baca.paket()]
