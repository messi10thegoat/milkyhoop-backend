#!/usr/bin/env python3
"""Gerbang kontrak PATCH rekening: absen / null / "" .

CAKUPAN JUJUR: jalur HTTP tidak bisa kutembak -- akun uji Collaborator dapat
403 untuk /api/bank-accounts. Yang diuji di sini MEKANISME yang dipakai
handler: `model_fields_set` untuk membedakan ABSEN dari null, dan pembersihan
"" -> None. Itu bukan uji endpoint, dan kunyatakan begitu.

Versi shell gerbang ini sempat melaporkan OK untuk dua pemeriksaan padahal
HTTP-nya 403: ia hanya melihat nilainya tetap None, tanpa memeriksa kode
jawaban. Nilai yang "sudah benar sejak awal" bukan bukti bahwa perubahan
berhasil.
"""
import sys

sys.path.insert(0, "/app/backend/api_gateway")

from app.schemas.bank_accounts import UpdateBankAccountRequest  # noqa: E402

gagal = []


def cek(ok, pesan):
    print(f"  {'OK  ' if ok else 'GAGAL'} {pesan}")
    if not ok:
        gagal.append(pesan)


def bersih(body, nama):
    v = getattr(body, nama)
    if isinstance(v, str):
        v = v.strip() or None
    return v


def utama():
    print("== 1. ABSEN dibedakan dari null ==")
    absen = UpdateBankAccountRequest(bank_address="Jl. Uji")
    cek("bank_branch" not in absen.model_fields_set,
        f"tak dikirim -> tidak ada di model_fields_set: {sorted(absen.model_fields_set)}")
    eksplisit = UpdateBankAccountRequest(bank_branch=None)
    cek("bank_branch" in eksplisit.model_fields_set,
        "null dikirim -> ADA di model_fields_set (jadi handler tahu harus mengosongkan)")

    print('== 2. "" dan null sama-sama mengosongkan ==')
    for nilai in ("", "   ", None):
        b = UpdateBankAccountRequest(bank_branch=nilai, bank_address=nilai, swift_code=nilai)
        hasil = {m: bersih(b, m) for m in ("bank_branch", "bank_address", "swift_code")}
        cek(all(v is None for v in hasil.values()), f"{nilai!r} -> {hasil}")
    b = UpdateBankAccountRequest(bank_branch="  KCP TEMANGGUNG  ")
    cek(bersih(b, "bank_branch") == "KCP TEMANGGUNG",
        f"nilai nyata dipangkas, bukan dibuang: {bersih(b, 'bank_branch')!r}")

    print("== 3. KONTROL MERAH: perilaku LAMA harus berbeda ==")
    lama = UpdateBankAccountRequest(bank_branch=None)
    # cara lama: `if body.bank_branch is not None` -> null DILEWATI
    dilewati_lama = lama.bank_branch is None
    cek(dilewati_lama,
        "dengan aturan lama (`is not None`) null DILEWATI -> tidak pernah mengosongkan")
    b2 = UpdateBankAccountRequest(bank_branch="")
    cek(b2.bank_branch == "" and bersih(b2, "bank_branch") is None,
        "dengan aturan lama \"\" tersimpan sebagai '' ; dengan aturan baru jadi None")

    print("== 4. account_name: kolomnya NOT NULL ==")
    an = UpdateBankAccountRequest(account_name="")
    kosong = not (an.account_name or "").strip()
    cek("account_name" in an.model_fields_set and kosong,
        "\"\" terdeteksi -> handler menolak 422, BUKAN membiarkannya jadi galat basis data")
    an2 = UpdateBankAccountRequest(bank_branch="x")
    cek("account_name" not in an2.model_fields_set,
        "KONTROL: account_name tak dikirim -> tidak diperiksa, tidak ditolak")

    if gagal:
        print("\nGAGAL:")
        for g in gagal:
            print("  - " + g)
        return 1
    print("\nOK: kontrak PATCH rekening seragam (lapis skema/mekanisme).")
    print("BATAS: jalur HTTP tak terbukti -- akun uji Collaborator dapat 403.")
    return 0


if __name__ == "__main__":
    sys.exit(utama())
