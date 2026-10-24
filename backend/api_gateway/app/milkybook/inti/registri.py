"""Registri tool Milkybook: Tool, Paket, dan filter per giliran.

Inti tidak mengenal modul. Modul = Paket (daftar Tool + kartu pengetahuan).
Kelas tool (rancangan §0.3, §11):
  READ    GET bebas
  PREVIEW POST .../preview atau .../calculate, nol tulis, bebas
  WRITE   hanya lewat kartu Tinjau -> Setuju; payload identik pratinjau + X-Idempotency-Key
  NAV     tidak menyentuh buku; mengembalikan halaman FE untuk dibuka pengguna

Aturan turun-kelas: WRITE yang `butuh` tiket belum mendarat (mis. AS-G1 idempotency
Penawaran) diturunkan otomatis jadi NAV. Aksi tanpa pratinjau/idempotency tidak ditulis
oleh Milkybook (rancangan §2.3).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Awaitable, Callable, Dict, Iterable, List, Optional, Sequence, Tuple


class Kelas(str, Enum):
    READ = "READ"
    PREVIEW = "PREVIEW"
    WRITE = "WRITE"
    NAV = "NAV"


# Pengganti parameter jalur saat menanyakan izin (pola izin = regex atas jalur konkret).
_ID_CONTOH = "00000000-0000-4000-8000-000000000000"
_PARAM_JALUR = re.compile(r"\{([a-z_]+)\}")


@dataclass(frozen=True)
class Tool:
    nama: str
    kelas: Kelas
    deskripsi: str
    skema: dict
    metode: str = "GET"
    jalur: str = ""
    # WRITE: nama tool PREVIEW yang dijalankan inti dengan payload yang sama sebelum kartu lahir.
    pratinjau: Optional[str] = None
    # WRITE: tiket yang harus mendarat di BE sebelum tool ini aktif (selain itu -> NAV).
    butuh: Tuple[str, ...] = ()
    # NAV (dan WRITE yang diturunkan): rute FE, boleh memuat {param}.
    halaman: Optional[str] = None
    # Tool search: True = dimuat lewat pencarian, bukan dikirim tiap giliran.
    tunda: bool = False
    # Judul kartu Tinjau di FE.
    judul_kartu: Optional[str] = None

    def param_jalur(self) -> List[str]:
        return _PARAM_JALUR.findall(self.jalur)

    def jalur_contoh(self) -> str:
        return _PARAM_JALUR.sub(_ID_CONTOH, self.jalur)

    def jalur_konkret(self, masukan: dict) -> Tuple[str, dict]:
        """(jalur terisi, sisa masukan). Parameter jalur WAJIB ada; nilai di-escape."""
        sisa = dict(masukan)
        def _isi(m):
            kunci = m.group(1)
            if kunci not in sisa or sisa[kunci] in (None, ""):
                raise ValueError(f"parameter jalur '{kunci}' wajib")
            nilai = str(sisa.pop(kunci))
            if not re.fullmatch(r"[A-Za-z0-9_-]+", nilai):
                raise ValueError(f"parameter jalur '{kunci}' tidak sah")
            return nilai
        return _PARAM_JALUR.sub(_isi, self.jalur), sisa

    def untuk_api(self) -> dict:
        """Definisi tool untuk Messages API: strict, additionalProperties false."""
        d = {
            "name": self.nama,
            "description": self.deskripsi,
            "input_schema": self.skema,
            "strict": True,
        }
        if self.tunda:
            d["defer_loading"] = True
        return d


@dataclass
class Paket:
    modul: str
    tools: List[Tool]
    pengetahuan: str = ""  # isi kartu pengetahuan (markdown), dimuat utuh di system

    def __post_init__(self):
        nama = [t.nama for t in self.tools]
        if len(nama) != len(set(nama)):
            raise ValueError(f"paket {self.modul}: nama tool kembar")
        for t in self.tools:
            periksa_skema_strict(t.skema, t.nama)
            if t.kelas == Kelas.WRITE and not t.pratinjau:
                raise ValueError(f"{t.nama}: WRITE wajib punya pratinjau (rancangan §2.3)")
            if t.kelas in (Kelas.WRITE, Kelas.NAV) and not t.halaman:
                raise ValueError(f"{t.nama}: WRITE/NAV wajib punya halaman (jalur turun-kelas)")


def periksa_skema_strict(skema: dict, nama: str) -> None:
    """Setiap object: additionalProperties false; tanpa batas numerik/panjang (tak didukung strict)."""
    if not isinstance(skema, dict):
        return
    if skema.get("type") == "object":
        if skema.get("additionalProperties") is not False:
            raise ValueError(f"{nama}: object tanpa additionalProperties:false")
        for k in skema.get("required", []):
            if k not in skema.get("properties", {}):
                raise ValueError(f"{nama}: required '{k}' tak ada di properties")
    for terlarang in ("minimum", "maximum", "minLength", "maxLength", "multipleOf"):
        if terlarang in skema:
            raise ValueError(f"{nama}: '{terlarang}' tidak didukung strict")
    for v in skema.get("properties", {}).values():
        periksa_skema_strict(v, nama)
    if "items" in skema:
        periksa_skema_strict(skema["items"], nama)
    for k in ("anyOf", "allOf"):
        for v in skema.get(k, []):
            periksa_skema_strict(v, nama)


def turunkan_ke_nav(t: Tool, alasan: str) -> Tool:
    return replace(
        t,
        kelas=Kelas.NAV,
        deskripsi=f"{t.deskripsi} (Saat ini hanya membuka halaman; {alasan}.)",
        pratinjau=None,
    )


# izin_fn(jalur_list, metode) -> {jalur: bool}; produksi = izin_rute_pengguna (permission_middleware).
IzinFn = Callable[[List[str], str], Awaitable[Dict[str, bool]]]


class Registri:
    def __init__(self, paket: Iterable[Paket]):
        self.paket: List[Paket] = list(paket)
        semua = [t.nama for p in self.paket for t in p.tools]
        if len(semua) != len(set(semua)):
            raise ValueError("nama tool kembar lintas paket")
        self._indeks: Dict[str, Tool] = {t.nama: t for p in self.paket for t in p.tools}

    def ambil(self, nama: str) -> Optional[Tool]:
        return self._indeks.get(nama)

    def pengetahuan(self) -> str:
        return "\n\n".join(p.pengetahuan for p in self.paket if p.pengetahuan)

    async def tools_untuk(self, izin_fn: IzinFn, gap_mendarat: Sequence[str] = ()) -> List[Tool]:
        """Daftar tool efektif untuk satu pengguna pada satu giliran.

        1) WRITE dengan `butuh` belum mendarat -> NAV.
        2) Tool yang rute BE-nya tidak diizinkan -> dibuang (model tidak melihatnya).
           WRITE juga butuh izin rute pratinjaunya. NAV tanpa jalur BE selalu ada.
        Urutan stabil (urutan paket, lalu urutan tool) supaya cache prompt tidak pecah.
        """
        mendarat = set(gap_mendarat)
        kandidat: List[Tool] = []
        for p in self.paket:
            for t in p.tools:
                belum = [g for g in t.butuh if g not in mendarat]
                if belum and t.kelas == Kelas.WRITE:
                    t = turunkan_ke_nav(t, f"menunggu {', '.join(belum)}")
                elif belum:
                    # READ/PREVIEW yang rute BE-nya belum ada (kontrak T0) tidak ditawarkan sama sekali.
                    continue
                kandidat.append(t)

        per_metode: Dict[str, List[str]] = {}
        for t in kandidat:
            for metode, jalur in self._rute_wajib(t):
                per_metode.setdefault(metode, []).append(jalur)
        boleh: Dict[Tuple[str, str], bool] = {}
        for metode, daftar in per_metode.items():
            hasil = await izin_fn(sorted(set(daftar)), metode)
            for j in daftar:
                boleh[(metode, j)] = bool(hasil.get(j, False))

        return [t for t in kandidat if all(boleh[r] for r in self._rute_wajib(t))]

    def _rute_wajib(self, t: Tool) -> List[Tuple[str, str]]:
        if t.kelas == Kelas.NAV:
            # NAV (asli atau turunan WRITE) butuh izin rute aksinya bila ada: aksi yang tak boleh
            # dilakukan tidak ditawarkan. Rute itu TIDAK pernah dipanggil oleh NAV.
            return [(t.metode, t.jalur_contoh())] if t.jalur else []
        rute = [(t.metode, t.jalur_contoh())]
        if t.kelas == Kelas.WRITE and t.pratinjau:
            pr = self._indeks.get(t.pratinjau)
            if pr is None:
                raise ValueError(f"{t.nama}: pratinjau '{t.pratinjau}' tak terdaftar")
            if pr.butuh and not set(pr.butuh) <= set(t.butuh):
                raise ValueError(f"{t.nama}: pratinjau butuh {pr.butuh} di luar butuh WRITE")
            rute.append((pr.metode, pr.jalur_contoh()))
        return rute
