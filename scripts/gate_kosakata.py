"""Gerbang kosakata `status` — quotes / proformas / sales-invoices.

BENTUK: LAMA dan BARU berdampingan di basis data yang SAMA.
Kontrol merah DUA SISI, sesuai brief: hijau di BARU saja tak cukup dan merah di
LAMA saja tak cukup; yang dibuktikan adalah BEDANYA.

⚠️ BATAS CAKUPAN, DISEBUT DI SINI SUPAYA TAK SALAH DIBACA:
Lapis A memanggil handler LANGSUNG, bukan lewat HTTP. Untuk `quotes` itu bukan
pilihan gaya melainkan keharusan: akun uji (Collaborator) mendapat 403
"Permission denied ... view quote", dan menaikkan izinnya adalah keputusan
pemilik yang TIDAK diminta untuk modul ini. Jadi hijau di sini BUKAN bukti dari
jalur HTTP untuk quotes. Untuk proformas dan sales-invoices, jalur HTTP diuji
terpisah di lapis B (nilai asing) dan bisa diverifikasi dari tepi sesudah deploy.

Lapis B memakai TestClient dengan kolam yang SENGAJA meledak: nilai asing tak
pernah menyentuh basis data karena validasi menolak lebih dulu. Kalau LAMA
menjawab 500 di situ, itu justru buktinya -- permintaannya lolos validasi.
"""
import asyncio
import importlib.util
import inspect
import os
import sys

sys.path.insert(0, "/app/backend/api_gateway")

import asyncpg  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

TENANT = "kaos-biru-konveksi"
USER = "a2d3129a-91a6-4144-8b3b-39f149790d87"
BARU_DIR = os.environ.get("BARU_DIR", "/tmp/baru")

MODUL = ["quotes", "proformas", "sales_invoices"]

# (nilai sah, harus > 0 baris?) -- diambil dari constraint UNION cabang handler
SAH = {
    "quotes": ["all", "draft", "void", "converted", "accepted", "declined"],
    "proformas": ["all", "draft", "issued", "cancelled", "expired"],
    "sales_invoices": ["all", "draft", "posted", "partial", "paid", "void",
                       "overdue", "unpaid", "active"],
}
ASING = ["zzz", "DRAFT", "", "posted_"]


class Bawaan:
    """"Tanpa parameter" = DEFAULT TANDA TANGAN, bukan None.

    quotes dan proformas ber-default `Query("all")`; sales_invoices `Query(None)`.
    Mengoper None ke quotes menyaring `status = NULL` -> 0 baris, keadaan yang
    TIDAK BISA dicapai lewat HTTP karena FastAPI mengisi defaultnya. Kontrol yang
    memakai None menguji jalur yang tak ada penggunanya.
    """

    def __repr__(self):
        return "(tanpa parameter)"


BAWAAN = Bawaan()


def muat_baru(nama):
    path = f"{BARU_DIR}/{nama}.py"
    mod_nama = f"app.routers.{nama}_baru"
    spec = importlib.util.spec_from_file_location(mod_nama, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_nama] = mod
    spec.loader.exec_module(mod)
    return mod


class ReqPalsu:
    pass


def handler_daftar(mod):
    """Handler untuk GET "" -- dicari dari tabel rute, bukan ditebak dari nama."""
    for r in mod.router.routes:
        if r.path in ("", "/") and "GET" in r.methods:
            return r.endpoint
    raise RuntimeError(f"tak ada rute GET '' di {mod.__name__}")


def kwargs_default(fn, status):
    """Baca default dari TANDA TANGAN. Dipanggil langsung, FastAPI tak mengisinya."""
    ba = {}
    for nama, p in inspect.signature(fn).parameters.items():
        if nama == "request":
            ba[nama] = ReqPalsu()
        elif nama == "status":
            d = p.default
            ba[nama] = getattr(d, "default", d) if isinstance(status, Bawaan) else status
        else:
            d = p.default
            ba[nama] = getattr(d, "default", d)
            if ba[nama] is inspect.Parameter.empty:
                ba[nama] = None
    return ba


class TakTerhitung:
    """Penghitung yang menyerah -- BUKAN nol, dan BUKAN alasan untuk hijau.

    Percobaan pertama mengembalikan None di sini untuk `quotes`, yang responsnya
    model Pydantic (QuoteListResponse), bukan dict. Akibatnya gerbang
    membandingkan None dengan None dan melaporkan OK untuk SETIAP nilai quotes:
    hijau yang tak mengukur apa pun. Kelas yang sama dengan gerbang yang tak
    bisa merah -- yang salah bukan hasilnya, melainkan bahwa hasil itu menjawab
    pertanyaan lain dari yang ditanya.
    """

    def __init__(self, sebab):
        self.sebab = sebab

    def __repr__(self):
        return f"TAK-TERHITUNG({self.sebab})"


def jumlah(hasil):
    # dict biasa
    if isinstance(hasil, dict):
        for k in ("total", "totalItems", "count"):
            if k in hasil:
                return hasil[k]
        for v in hasil.values():
            if isinstance(v, list):
                return len(v)
        return TakTerhitung("dict tanpa total/daftar")
    if isinstance(hasil, list):
        return len(hasil)
    # model Pydantic / objek biasa
    for k in ("total", "totalItems", "count"):
        if hasattr(hasil, k):
            return getattr(hasil, k)
    for k in ("items", "quotes", "data", "results"):
        v = getattr(hasil, k, None)
        if isinstance(v, list):
            return len(v)
    return TakTerhitung(type(hasil).__name__)


async def lapis_a(lama, baru):
    kolam = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=3)

    async def get_pool():
        return kolam

    out = {}
    for nama in MODUL:
        for label, mods in (("LAMA", lama), ("BARU", baru)):
            m = mods[nama]
            for attr in ("get_pool",):
                if hasattr(m, attr):
                    setattr(m, attr, get_pool)
            m.get_user_context = lambda request: {"tenant_id": TENANT, "user_id": USER}
            fn = handler_daftar(m)
            for st in SAH[nama] + [BAWAAN]:
                try:
                    r = await fn(**kwargs_default(fn, st))
                    out[(nama, label, st)] = jumlah(r)
                except Exception as e:
                    out[(nama, label, st)] = f"MELEDAK: {type(e).__name__}"
    await kolam.close()
    return out


def lapis_b(lama, baru):
    class Meledak(Exception):
        pass

    async def kolam_meledak():
        raise Meledak("handler TERCAPAI: tak ada validasi di depannya")

    api = FastAPI()
    for nama in MODUL:
        for label, mods in (("lama", lama), ("baru", baru)):
            m = mods[nama]
            if hasattr(m, "get_pool"):
                m.get_pool = kolam_meledak
            api.include_router(m.router, prefix=f"/{label}/{nama}")
    c = TestClient(api, raise_server_exceptions=False)
    return {
        (nama, label.upper(), v): c.get(f"/{label}/{nama}?status={v}").status_code
        for nama in MODUL
        for label in ("lama", "baru")
        for v in ASING
    }


def utama():
    lama, baru = {}, {}
    for nama in MODUL:
        lama[nama] = importlib.import_module(f"app.routers.{nama}")
        baru[nama] = muat_baru(nama)

    gagal = []
    a = asyncio.run(lapis_a(lama, baru))
    b = lapis_b(lama, baru)

    print("A. NILAI SAH — jumlah baris (LAMA -> BARU)")
    for nama in MODUL:
        print(f"  {nama}:")
        for st in SAH[nama] + [BAWAAN]:
            la, ba = a[(nama, "LAMA", st)], a[(nama, "BARU", st)]
            # BARU tak boleh MELEDAK, dan tak boleh mengubah jawaban nilai yang
            # LAMA sudah jawab benar. Satu-satunya perubahan yang DIHARAPKAN:
            # sales_invoices `all`, dari 0 (bohong) jadi jumlah penuh.
            harap_beda = (nama == "sales_invoices" and st == "all")
            # TAK-TERHITUNG tak pernah dihitung hijau: dua sisi yang sama-sama
            # tak terbaca BUKAN bukti bahwa keduanya sama.
            terbaca = not isinstance(la, TakTerhitung) and not isinstance(ba, TakTerhitung)
            ok = terbaca and (not str(ba).startswith("MELEDAK")) and (
                (ba != la) if harap_beda else (ba == la)
            )
            if not terbaca:
                gagal.append(f"{nama} status={st}: jumlah TAK TERBACA ({la!r} / {ba!r}) "
                             "-- gerbang tak boleh hijau atas dasar ini")
            tanda = "OK  " if ok else "GAGAL"
            catat = "  <- SENGAJA berubah" if harap_beda else ""
            print(f"    {tanda} status={str(st):<10} {str(la):<8} -> {str(ba):<8}{catat}")
            if not ok and terbaca:
                gagal.append(f"{nama} status={st}: {la} -> {ba}")

    print("\nB. NILAI ASING — kode HTTP (LAMA -> BARU, harap 422 di BARU)")
    for nama in MODUL:
        print(f"  {nama}:")
        for v in ASING:
            lb, bb = b[(nama, "LAMA", v)], b[(nama, "BARU", v)]
            ok = bb == 422
            print(f"    {'OK  ' if ok else 'GAGAL'} status={v!r:<10} {lb} -> {bb}")
            if not ok:
                gagal.append(f"{nama} status={v!r} -> {bb}, harap 422")

    print("\nKONTROL MERAH DUA SISI:")
    # 1. LAMA WAJIB menerima nilai asing (kalau tidak, gerbang tak membedakan)
    for nama in MODUL:
        for v in ASING:
            if b[(nama, "LAMA", v)] == 422:
                gagal.append(f"KONTROL: LAMA {nama} sudah menolak {v!r}")
    print("   OK  LAMA menerima SELURUH nilai asing (lolos validasi, sampai ke handler)"
          if not any(g.startswith("KONTROL") for g in gagal) else "   GAGAL di atas")
    # 2. cacat `all` NYATA di LAMA
    if a[("sales_invoices", "LAMA", "all")] != 0:
        gagal.append("KONTROL: sales_invoices LAMA status=all bukan 0 -- cacatnya tak ada")
        print("   GAGAL: cacat `all` tak ada di kode lama")
    else:
        print(f"   OK  sales_invoices LAMA status=all -> 0 (cacat NYATA)")
    # 3. tanpa parameter TETAP mengembalikan semuanya
    for nama in MODUL:
        if a[(nama, "BARU", BAWAAN)] != a[(nama, "LAMA", BAWAAN)]:
            gagal.append(f"KONTROL: {nama} tanpa parameter BERUBAH -- parameter jadi wajib?")
    print("   OK  tanpa parameter tak berubah di ketiganya"
          if not any("tanpa parameter" in g for g in gagal) else "   GAGAL di atas")

    if gagal:
        print("\nGAGAL:")
        for g in gagal:
            print("  - " + g)
        return 1
    print("\nOK: gerbang hijau, dan terbukti bisa merah di kedua sisi.")
    print("BATAS: quotes TIDAK diuji lewat HTTP (akun uji 403). Lihat docstring.")
    return 0


if __name__ == "__main__":
    sys.exit(utama())
