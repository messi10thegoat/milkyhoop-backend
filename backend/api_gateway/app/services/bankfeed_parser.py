"""Parser mutasi bank untuk Bank Feed (BF-1a; KONTRAK-bankfeed §3). MURNI: tanpa DB/jaringan; uang selalu Decimal.

Format: KlikBCA CSV "Download Mutasi" (latin-1; kepala `No. Rekening,=,'…`; baris `'dd/mm/yyyy,ket,'cabang,jumlah,CR|DB,saldo[,id_baris]`;
kaki Saldo Awal/Kredit/Debet/Saldo Akhir) + CSV generik (`peta_kolom` usulan/dari pengguna). PDF e-statement = BF-3 (bukan di sini).
- Pembaca csv BERKUTIP per baris: keterangan memuat koma (`"KARTU DEBIT …, ****0128"`); koma tak berkutip disambung defensif.
- Baris PEND (belum dibukukan bank) DILEWATI dan dihitung (`dilewati`); tak ikut kaki berkas (bank juga belum menghitungnya).
- Kontinuitas (TESTER-1): kaki berkas == Σ baris, Saldo Awal + Kredit - Debet == Saldo Akhir, saldo per baris berurutan -> blok
  BERKAS_TAK_SEIMBANG berkalimat (SATU blok: masalah pertama menurut urutan Kredit, Debet, Saldo akhir, baris).
- Sidik anti-duplikat: sha256(tenant | rekening | tanggal | arah | nominal | keterangan_normal | saldo); tanpa saldo -> kemunculan ke-n tupel yang sama."""
import csv
import hashlib
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Optional

from . import teks_galat as tg

MAKS_BARIS = 5000                       # O3 kontrak: batas baris per impor
SEN = Decimal("0.01")
_KUNCI_KAKI = {"saldo awal": "saldo_awal", "kredit": "kredit", "debet": "debet", "saldo akhir": "saldo_akhir"}


@dataclass
class BarisBank:
    baris_ke: int
    tanggal: Optional[date]
    keterangan: str
    nominal: Decimal
    arah: str                           # 'masuk' | 'keluar'
    saldo: Optional[Decimal]
    mentah: str
    pend: bool = False


@dataclass
class HasilBaca:
    format: str
    rekening_berkas: Optional[str] = None
    baris: list = field(default_factory=list)       # TANPA PEND
    dilewati: list = field(default_factory=list)    # [{alasan, n}]
    saldo_awal: Optional[Decimal] = None
    saldo_akhir: Optional[Decimal] = None
    kredit: Optional[Decimal] = None
    debet: Optional[Decimal] = None
    peta_kolom: Optional[dict] = None
    blocks: list = field(default_factory=list)      # [{code, message}]


def _blok(hasil: HasilBaca, code: str, message: str) -> None:
    hasil.blocks.append({"code": code, "message": message})


# ---------------------------------------------------------------- nilai dasar
def normalisasi_keterangan(teks) -> str:
    """Huruf kecil, spasi tunggal, buang kode transaksi BCA yang bervariasi (`0108/FTSCY/WS95031`, nomor QR). Fungsi MURNI."""
    s = str(teks or "").lower()
    s = re.sub(r"\b\d{4}/ftscy/ws\d+\b|\bftscy/ws\d+\b|\bws\d{4,}\b", " ", s)
    s = re.sub(r"\bqr[\s:-]*\d{6,}\b", " ", s)
    return " ".join(s.split())


def parse_uang(teks, gaya: str = "auto") -> Decimal:
    """'3900000.00' / '3.900.000,00' / 'Rp 1.250.000' / '1,250.50' / '150,5' -> Decimal. gaya='us' (KlikBCA): hanya digit + titik desimal.
    Ambigu ('1.500') dibaca ribuan (Indonesia). Sampah -> ValueError (tak pernah menebak diam-diam)."""
    if teks is None:
        raise ValueError("uang kosong")
    s = re.sub(r"^rp\.?", "", str(teks).strip().replace(" ", ""), flags=re.I)
    if not s:
        raise ValueError("uang kosong")
    negatif = s.startswith("-") or (s.startswith("(") and s.endswith(")"))
    s = s.lstrip("-").strip("()")
    tanda = -1 if negatif else 1
    try:
        if gaya == "us":
            if not re.fullmatch(r"\d+(\.\d+)?", s):
                raise ValueError("format uang")
            return tanda * Decimal(s)
        if "." in s and "," in s:
            des = "." if s.rfind(".") > s.rfind(",") else ","
            rib = "," if des == "." else "."
            bagian = s.split(des)
            if len(bagian) != 2 or not re.fullmatch(r"\d{1,3}(" + re.escape(rib) + r"\d{3})*", bagian[0]) or not re.fullmatch(r"\d+", bagian[1]):
                raise ValueError("format uang")
            return tanda * Decimal(bagian[0].replace(rib, "") + "." + bagian[1])
        for pisah in (",", "."):
            if pisah in s:
                if re.fullmatch(r"\d{1,3}(" + re.escape(pisah) + r"\d{3})+", s):
                    return tanda * Decimal(s.replace(pisah, ""))
                if re.fullmatch(r"\d+" + re.escape(pisah) + r"\d{1,2}", s) or (pisah == "." and re.fullmatch(r"\d+\.\d+", s)):
                    return tanda * Decimal(s.replace(pisah, "."))
                raise ValueError("format uang")
        if not re.fullmatch(r"\d+", s):
            raise ValueError("format uang")
        return tanda * Decimal(s)
    except InvalidOperation as e:
        raise ValueError("format uang") from e


def parse_tanggal(teks) -> date:
    s = str(teks or "").strip().lstrip("'").strip()
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        a, b, c = int(m[1]), int(m[2]), int(m[3])
    else:
        m = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})[/-](\d{2}|\d{4})", s)
        if not m:
            raise ValueError(f"tanggal tak dikenal: {s!r}")
        c, b, a = int(m[1]), int(m[2]), int(m[3])
        a = a + 2000 if a < 100 else a
    return date(a, b, c)


# ---------------------------------------------------------------- sidik
def beri_sidik(tenant_id: str, rekening_id, baris: list) -> list:
    """Sidik tiap baris. Bersaldo -> saldo ikut; tanpa saldo -> kemunculan ke-n tupel yang sama DALAM berkas (urutan, bukan nomor baris)."""
    hitung, hasil = {}, []
    for b in baris:
        norma = normalisasi_keterangan(b.keterangan)
        nominal = format(b.nominal.quantize(SEN), "f")
        if b.saldo is not None:
            ujung = format(b.saldo.quantize(SEN), "f")
        else:
            kunci = (b.tanggal, b.arah, nominal, norma)
            hitung[kunci] = hitung.get(kunci, 0) + 1
            ujung = f"#{hitung[kunci]}"
        bahan = "\x1f".join([str(tenant_id), str(rekening_id), b.tanggal.isoformat(), b.arah, nominal, norma, ujung])
        hasil.append(hashlib.sha256(bahan.encode("utf-8")).hexdigest())
    return hasil


# ---------------------------------------------------------------- KlikBCA CSV
def _baris_csv(teks: str) -> list:
    """[(nomor baris berkas 1-based, teks baris mentah, kolom)] -- csv.reader BERKUTIP per baris."""
    keluar = []
    for i, mentah in enumerate(teks.splitlines(), 1):
        if not mentah.strip():
            continue
        try:
            kolom = next(csv.reader([mentah]))
        except (csv.Error, StopIteration):
            kolom = mentah.split(",")
        keluar.append((i, mentah, kolom))
    return keluar


def _klikbca(teks: str) -> HasilBaca:
    h = HasilBaca(format="klikbca_csv")
    kolom_kepala, rusak, pend, mata_uang = None, [], 0, None
    for nomor, mentah, kolom in _baris_csv(teks):
        awal = kolom[0].strip()
        kunci = awal.lower().rstrip(".")
        if kunci in ("no. rekening", "no rekening") and len(kolom) >= 3:
            h.rekening_berkas = kolom[2].strip().lstrip("'")
            continue
        if kunci == "mata uang" and len(kolom) >= 3:
            mata_uang = kolom[2].strip().upper()
            continue
        if kunci == "nama" and len(kolom) >= 3:
            continue
        if kunci == "tanggal" and len(kolom) >= 4 and kolom[1].strip().lower() == "keterangan":
            kolom_kepala = kolom
            continue
        if awal.lower() in _KUNCI_KAKI and len(kolom) >= 3 and kolom[1].strip() == "=":
            try:
                setattr(h, _KUNCI_KAKI[awal.lower()], parse_uang(kolom[2], "us"))
            except ValueError:
                rusak.append((nomor, f"kaki '{awal}' tak terbaca"))
            continue
        if kolom_kepala is None:
            continue
        ekstra = max(len(kolom) - len(kolom_kepala), 0)   # koma tak berkutip di keterangan -> sambung
        ket = ",".join(kolom[1:2 + ekstra]).strip()
        sisa = kolom[2 + ekstra:]
        try:
            if awal.lstrip("'").upper() == "PEND":
                pend += 1
                continue
            tanggal = parse_tanggal(awal)
            if len(sisa) < 4:
                raise ValueError("kolom kurang")
            nominal = parse_uang(sisa[1], "us")
            dbcr = sisa[2].strip().upper()
            if dbcr not in ("CR", "DB") or nominal <= 0:
                raise ValueError("DB/CR atau jumlah tak sah")
            saldo = parse_uang(sisa[3], "us") if sisa[3].strip() else None
        except ValueError as e:
            rusak.append((nomor, str(e)))
            continue
        h.baris.append(BarisBank(baris_ke=nomor, tanggal=tanggal, keterangan=ket, nominal=nominal,
                                 arah="masuk" if dbcr == "CR" else "keluar", saldo=saldo, mentah=mentah))
    if pend:
        h.dilewati.append({"alasan": "PEND (belum dibukukan bank)", "n": pend})
    if mata_uang and mata_uang != "IDR":
        _blok(h, "MATA_UANG_BUKAN_IDR", f"Berkas bermata uang {mata_uang}; Bank Feed hanya menerima rupiah (IDR).")
    if rusak:
        contoh = "; ".join(f"baris {n}: {a}" for n, a in rusak[:3])
        _blok(h, "BARIS_TAK_TERBACA", f"{len(rusak)} baris tak bisa dibaca ({contoh}). Perbaiki berkas atau unduh ulang dari KlikBCA.")
    elif not h.baris:
        _blok(h, "BERKAS_KOSONG", "Berkas tidak memuat satu pun transaksi yang sudah dibukukan bank.")
    if len(h.baris) > MAKS_BARIS:
        _blok(h, "BERKAS_TERLALU_BESAR", f"Berkas memuat {len(h.baris)} baris; paling banyak {MAKS_BARIS} baris per impor. Pecah per bulan.")
    if not rusak and h.baris:
        salah = _periksa_kaki(h)
        if salah:
            _blok(h, "BERKAS_TAK_SEIMBANG", salah)
    return h


def _periksa_kaki(h: HasilBaca) -> Optional[str]:
    """Kalimat masalah PERTAMA (Kredit, Debet, Saldo akhir, urutan saldo per baris) atau None."""
    k = sum((b.nominal for b in h.baris if b.arah == "masuk"), Decimal(0))
    d = sum((b.nominal for b in h.baris if b.arah == "keluar"), Decimal(0))
    if h.kredit is not None and h.kredit != k:
        return f"Kredit di kaki berkas {tg.rp(h.kredit)} tidak sama dengan jumlah baris CR {tg.rp(k)} (selisih {tg.rp(abs(h.kredit - k))})."
    if h.debet is not None and h.debet != d:
        return f"Debet di kaki berkas {tg.rp(h.debet)} tidak sama dengan jumlah baris DB {tg.rp(d)} (selisih {tg.rp(abs(h.debet - d))})."
    if h.saldo_awal is not None and h.saldo_akhir is not None:
        hitung = h.saldo_awal + k - d
        if hitung != h.saldo_akhir:
            return (f"Saldo akhir di kaki berkas {tg.rp(h.saldo_akhir)} tidak sama dengan saldo awal + kredit - debet ({tg.rp(hitung)}); "
                    f"selisih {tg.rp(abs(h.saldo_akhir - hitung))}.")
    if h.saldo_awal is not None:
        sebelum = h.saldo_awal
        for b in h.baris:
            if b.saldo is None:
                continue
            harap = sebelum + b.nominal if b.arah == "masuk" else sebelum - b.nominal
            if harap != b.saldo:
                return (f"Saldo berkas tidak berurutan di baris {b.baris_ke} ({b.tanggal:%d/%m/%Y}): seharusnya {tg.rp(harap)} "
                        f"tetapi berkas {tg.rp(b.saldo)} (selisih {tg.rp(abs(b.saldo - harap))}).")
            sebelum = b.saldo
    return None


# ---------------------------------------------------------------- CSV generik
_SINONIM = {
    "tanggal": {"tanggal", "tgl", "date", "transaction date", "tanggal transaksi", "posting date"},
    "keterangan": {"keterangan", "uraian", "deskripsi", "description", "narrative", "remark", "remarks", "detail", "transaction details"},
    "debit": {"debit", "debet", "db", "keluar", "withdrawal", "uang keluar"},
    "kredit": {"kredit", "credit", "cr", "masuk", "deposit", "uang masuk"},
    "jumlah": {"jumlah", "amount", "nominal", "mutasi", "nilai"},
    "arah": {"type", "tipe", "dbcr", "db/cr", "jenis", "d/c", "cr/db"},
    "saldo": {"saldo", "balance", "running balance"},
}


def usulkan_peta(header: list) -> dict:
    """Usulan peta kolom (indeks) dari baris judul; kunci yang tak ditemukan tidak ada."""
    peta = {}
    for i, judul in enumerate(header):
        j = str(judul).strip().lower()
        for kunci, daftar in _SINONIM.items():
            if j in daftar and kunci not in peta:
                peta[kunci] = i
    return peta


def _peta_sah(p: dict) -> bool:
    return "tanggal" in p and "keterangan" in p and ("jumlah" in p or "debit" in p or "kredit" in p)


def _indeks(nilai, header: list) -> Optional[int]:
    if isinstance(nilai, int):
        return nilai
    j = str(nilai).strip().lower()
    return next((i for i, x in enumerate(header) if str(x).strip().lower() == j), None)


def _arah_dari(teks: str) -> str:
    t = teks.strip().upper()
    if t in ("CR", "C", "K", "KREDIT", "CREDIT", "MASUK"):
        return "masuk"
    if t in ("DB", "D", "DEBIT", "DEBET", "KELUAR"):
        return "keluar"
    raise ValueError(f"arah tak dikenal: {teks!r}")


def _generik(teks: str, peta_kolom: Optional[dict]) -> HasilBaca:
    h = HasilBaca(format="csv")
    contoh = next((b for b in teks.splitlines() if b.strip()), "")
    pemisah = max((";", ",", "\t"), key=contoh.count) if contoh else ","
    baris = [(r.line_num, k) for r in [csv.reader(teks.splitlines(), delimiter=pemisah)] for k in r if any(c.strip() for c in k)]
    if not baris:
        _blok(h, "BERKAS_KOSONG", "Berkas kosong.")
        return h
    header = baris[0][1]
    try:
        parse_tanggal(header[0] if not peta_kolom else header[_indeks(peta_kolom.get("tanggal", 0), header) or 0])
        ada_judul = False
    except (ValueError, IndexError):
        ada_judul = True
    peta = {k: _indeks(v, header) for k, v in (peta_kolom or usulkan_peta(header)).items()}
    peta = {k: v for k, v in peta.items() if v is not None}
    h.peta_kolom = peta
    if not _peta_sah(peta):
        _blok(h, "KOLOM_TAK_DIKENALI", "Kolom berkas tak dikenali. Pilih kolom Tanggal, Keterangan, dan Jumlah (atau Debit/Kredit).")
        return h
    rusak = []
    for nomor, k in baris[1:] if ada_judul else baris:
        try:
            def sel(nama):
                i = peta.get(nama)
                return k[i].strip() if i is not None and i < len(k) else ""
            tanggal = parse_tanggal(sel("tanggal"))
            if "jumlah" in peta:
                nominal = parse_uang(sel("jumlah"))
                arah = _arah_dari(sel("arah")) if "arah" in peta else ("keluar" if nominal < 0 else "masuk")
                nominal = abs(nominal)
            else:
                db = parse_uang(sel("debit")) if sel("debit") else Decimal(0)
                kr = parse_uang(sel("kredit")) if sel("kredit") else Decimal(0)
                if (db > 0) == (kr > 0):
                    raise ValueError("isi tepat salah satu: debit atau kredit")
                arah, nominal = ("keluar", db) if db > 0 else ("masuk", kr)
            if nominal <= 0:
                raise ValueError("jumlah harus lebih dari nol")
            saldo = parse_uang(sel("saldo")) if sel("saldo") else None
        except (ValueError, IndexError) as e:
            rusak.append((nomor, str(e)))
            continue
        h.baris.append(BarisBank(baris_ke=nomor, tanggal=tanggal, keterangan=sel("keterangan"), nominal=nominal, arah=arah, saldo=saldo,
                                 mentah=pemisah.join(k)))
    if rusak:
        contoh_rusak = "; ".join(f"baris {n}: {a}" for n, a in rusak[:3])
        _blok(h, "BARIS_TAK_TERBACA", f"{len(rusak)} baris tak bisa dibaca ({contoh_rusak}).")
    elif not h.baris:
        _blok(h, "BERKAS_KOSONG", "Berkas tidak memuat transaksi.")
    if len(h.baris) > MAKS_BARIS:
        _blok(h, "BERKAS_TERLALU_BESAR", f"Berkas memuat {len(h.baris)} baris; paling banyak {MAKS_BARIS} baris per impor.")
    return h


# ---------------------------------------------------------------- pintu masuk
def _teks(isi: bytes) -> str:
    for sandi in ("utf-8-sig", "latin-1"):
        try:
            return isi.decode(sandi)
        except UnicodeDecodeError:
            continue
    return isi.decode("latin-1", errors="replace")


def baca(isi: bytes, nama: str, peta_kolom: Optional[dict] = None) -> HasilBaca:
    """Berkas -> HasilBaca. KlikBCA CSV dikenali dari kepala `No. Rekening,=,`; selain itu CSV generik."""
    teks = _teks(isi)
    awal = next((b for b in teks.splitlines() if b.strip()), "")
    if re.match(r"^\s*no\.?\s*rekening\s*,\s*=\s*,", awal, re.I):
        return _klikbca(teks)
    if b"\x00" in isi[:512]:
        h = HasilBaca(format="csv")
        _blok(h, "FORMAT_TAK_DIKENALI", "Berkas bukan CSV teks yang dikenali. Unduh Mutasi dari KlikBCA dalam format CSV.")
        return h
    return _generik(teks, peta_kolom)
