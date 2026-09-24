"""
Unit PIN-FREEZE: requirements.txt = KUNCI LENGKAP image gateway.

Kenapa ada: 24 Sep 2026 image gateway prod (c1ab0fc94faf) TIDAK bisa
direproduksi dari Dockerfile repo. requirements.txt memakai `>=` di lima paket
dan tak menyebut dependensi turunan sama sekali; build penuh menggeser 12 paket
diam-diam (cryptography 49 -> 50 mayor, resend, google-auth, pymupdf, deps
weasyprint). Satu `compose build api_gateway` = image lain di prod, nol galat.

Tiga penjaga, masing-masing bisa merah sendiri:
  1. BENTUK   -- setiap baris `nama==versi`; `>=`, `~=`, nama telanjang = merah.
  2. TERTUTUP -- setiap dependensi (penanda dievaluasi untuk Python ini) dari
                 setiap paket terkunci juga terkunci DAN versinya memenuhi
                 syarat. Inilah yang membuat `pip install --no-deps` di
                 Dockerfile aman: tak ada yang tertinggal untuk ditarik resolver.
  3. TERPASANG -- versi yang benar-benar terpasang di image yang menjalankan
                 tes == versi terkunci. Runner (scripts/jalankan_unit.sh)
                 memakai image :latest, jadi ini menjaga "image prod == repo".

Plus Dockerfile: memasang dengan --no-deps + pip check, dan image dasar
dikunci per digest (tag `python:3.11-slim` bergerak: Python patch & Debian).
"""
import importlib.metadata as md
import re
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

AKAR = Path(__file__).resolve().parents[2]
REQ = AKAR / "requirements.txt"
DOCKERFILE = AKAR / "Dockerfile"

# Milik image dasar / Dockerfile, bukan requirements.txt.
DI_LUAR_KUNCI = {"pip", "setuptools", "wheel"}

BARIS_KUNCI = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([A-Za-z0-9.+!-]+)$")


def _baris_bermakna(teks):
    for mentah in teks.splitlines():
        baris = mentah.split("#", 1)[0].strip()
        if baris:
            yield baris


def _kunci(teks):
    """{nama-kanonik: Version}; baris yang bukan `nama==versi` -> ValueError."""
    kunci = {}
    for baris in _baris_bermakna(teks):
        m = BARIS_KUNCI.match(baris)
        if not m:
            raise ValueError(f"bukan pin persis: {baris!r}")
        nama = canonicalize_name(m.group(1))
        if nama in kunci:
            raise ValueError(f"ganda: {nama}")
        kunci[nama] = Version(m.group(2))
    return kunci


def _dependensi_tak_terpenuhi(kunci, requires_of):
    """Daftar (paket, syarat, alasan) yang tak dipenuhi oleh daftar kunci."""
    bolong = []
    for nama in sorted(kunci):
        for mentah in requires_of(nama) or []:
            r = Requirement(mentah)
            if r.marker is not None and not r.marker.evaluate({"extra": ""}):
                continue
            dep = canonicalize_name(r.name)
            if dep in DI_LUAR_KUNCI:
                continue
            if dep not in kunci:
                bolong.append((nama, mentah, "tak terkunci"))
            elif not r.specifier.contains(kunci[dep], prereleases=True):
                bolong.append((nama, mentah, f"terkunci {kunci[dep]}"))
    return bolong


def _requires_terpasang(nama):
    return md.distribution(nama).requires


def _dependensi_terpasang_lewat_extra(kunci):
    """Extra yang DIPAKAI (mis. fonttools[woff] dari weasyprint) ikut diperiksa."""
    bolong = []
    for nama in sorted(kunci):
        for mentah in _requires_terpasang(nama) or []:
            r = Requirement(mentah)
            if r.marker is not None and not r.marker.evaluate({"extra": ""}):
                continue  # syarat milik extra opsional paket ini -- tak dipakai
            for extra in r.extras:
                for dmentah in _requires_terpasang(r.name) or []:
                    d = Requirement(dmentah)
                    if d.marker is None or not d.marker.evaluate({"extra": extra}):
                        continue
                    if d.marker.evaluate({"extra": ""}):
                        continue  # bukan khusus extra; sudah dicek jalur biasa
                    if canonicalize_name(d.name) not in kunci:
                        bolong.append((r.name, extra, dmentah))
    return bolong


# ---------------------------------------------------------------- 1. BENTUK

def test_setiap_baris_pin_persis():
    kunci = _kunci(REQ.read_text())
    assert len(kunci) >= 70, f"kunci terlalu sedikit ({len(kunci)}) -- bukan freeze lengkap?"


def test_bentuk_menolak_rentang_dan_nama_telanjang():
    # Sisi merah pemeriksa bentuk: isi berkas LAMA (sebelum pin-freeze).
    for buruk in ("pymupdf>=1.27.0", "cachetools", "resend~=2.0", "bcrypt>=4.0.0  # x"):
        try:
            _kunci(f"fastapi==0.116.1\n{buruk}\n")
        except ValueError:
            continue
        raise AssertionError(f"pemeriksa bentuk meloloskan {buruk!r}")


# -------------------------------------------------------------- 2. TERTUTUP

def test_kunci_tertutup_terhadap_dependensinya():
    kunci = _kunci(REQ.read_text())
    bolong = _dependensi_tak_terpenuhi(kunci, _requires_terpasang)
    assert not bolong, "dependensi di luar kunci (pip --no-deps akan melewatkannya):\n" + "\n".join(
        f"  {p}: {s} ({a})" for p, s, a in bolong
    )


def test_kunci_tertutup_juga_untuk_extra_yang_dipakai():
    kunci = _kunci(REQ.read_text())
    bolong = _dependensi_terpasang_lewat_extra(kunci)
    assert not bolong, f"extra dipakai tapi dependensinya tak terkunci: {bolong}"


def test_ketertutupan_bisa_merah():
    # Sisi merah: buang satu dependensi turunan (cryptography, ditarik
    # google-auth) dan satu versi yang tak memenuhi syarat (pydantic-core
    # harus == 2.33.2 untuk pydantic 2.11.7).
    kunci = _kunci(REQ.read_text())
    tanpa = {k: v for k, v in kunci.items() if k != "cryptography"}
    assert any(
        d == "google-auth" and "cryptography" in s
        for d, s, _ in _dependensi_tak_terpenuhi(tanpa, _requires_terpasang)
    )
    geser = dict(kunci, **{"pydantic-core": Version("2.33.1")})
    assert any(
        d == "pydantic" and "pydantic-core" in s
        for d, s, _ in _dependensi_tak_terpenuhi(geser, _requires_terpasang)
    )


# ------------------------------------------------------------- 3. TERPASANG

def _selisih_terpasang(kunci):
    beda = []
    for nama, versi in sorted(kunci.items()):
        try:
            ada = Version(md.version(nama))
        except md.PackageNotFoundError:
            beda.append(f"{nama}: terkunci {versi}, TIDAK terpasang")
            continue
        if ada != versi:
            beda.append(f"{nama}: terkunci {versi}, terpasang {ada}")
    return beda


def test_versi_terpasang_sama_dengan_kunci():
    beda = _selisih_terpasang(_kunci(REQ.read_text()))
    assert not beda, "image yang menjalankan tes != requirements.txt:\n  " + "\n  ".join(beda)


def test_selisih_terpasang_bisa_merah():
    kunci = _kunci(REQ.read_text())
    palsu = dict(kunci, cryptography=Version("50.0.0"), **{"paket-hantu-tak-ada": Version("1.0")})
    beda = _selisih_terpasang(palsu)
    assert any(b.startswith("cryptography: terkunci 50.0.0") for b in beda)
    assert any(b.startswith("paket-hantu-tak-ada") for b in beda)


# ------------------------------------------------------------- Dockerfile

def _dockerfile():
    return DOCKERFILE.read_text()


def test_dockerfile_pasang_tanpa_resolver_lalu_pip_check():
    teks = _dockerfile()
    assert re.search(r"pip install [^\n]*--no-deps[^\n]*-r requirements\.txt", teks), (
        "requirements.txt harus dipasang dengan --no-deps"
    )
    assert re.search(r"\bpip check\b", teks), "pip check wajib sesudah pemasangan"
    assert not re.search(r"pip install --upgrade pip(?!==)", teks), "pip di-upgrade tanpa pin"


def test_dockerfile_image_dasar_dikunci_per_digest():
    froms = re.findall(r"^FROM\s+(\S+)", _dockerfile(), flags=re.M)
    assert froms, "tak ada FROM"
    for ref in froms:
        assert re.search(r"@sha256:[0-9a-f]{64}$", ref), f"image dasar tanpa digest: {ref}"
