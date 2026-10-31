"""Jam beku tes unit: "hari ini" tenant DIPATOK di SUMBERNYA, bukan hanya di satu nama modul.

Bom waktu 1 Nov 2026: test_ubah_quote_terkirim menambal `quotes.tanggal_dokumen`, tetapi _perpanjang_penawaran mengimpor
`tanggal_dokumen` LOKAL dari utils.tanggal_tenant -> tambalan terlewati, tanggal asli (datetime.now) dipakai, dan
`_BARU = date(2026, 10, 31)` jadi "masa lalu" sejak 1 Nov 00:00 WIB -> semua merge BE terblokir.

bekukan_hari menambal utils.tanggal_tenant.tanggal_dokumen (dua jalur impor: app.* dan backend.api_gateway.app.*) DAN
setiap modul termuat yang sudah mengikat fungsi aslinya lewat `from ... import tanggal_dokumen`. Impor lokal di dalam
fungsi (dibaca saat dipanggil) ikut kena karena atribut modul sumbernya yang ditambal. tanggal_dokumen_iso memanggil
tanggal_dokumen lewat nama global modulnya -> ikut beku. Sengaja BUKAN autouse di conftest (berkas gerbang; tes yang
memang menguji tanggal_dokumen harus melihat fungsi aslinya)."""
import sys
from datetime import date

_SUMBER = ("app.utils.tanggal_tenant", "backend.api_gateway.app.utils.tanggal_tenant")


def bekukan_hari(monkeypatch, hari: date) -> date:
    import app.utils.tanggal_tenant  # noqa: F401  pastikan sumber termuat
    asli = {sys.modules[n].tanggal_dokumen for n in _SUMBER if n in sys.modules}

    async def beku(conn, tenant_id):
        return hari
    for nama, m in list(sys.modules.items()):
        if m is None or not nama.startswith(("app.", "backend.api_gateway.app.")):
            continue
        f = getattr(m, "tanggal_dokumen", None)
        if nama in _SUMBER or any(f is a for a in asli):
            monkeypatch.setattr(m, "tanggal_dokumen", beku)
    return hari
