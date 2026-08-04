# DRAFT — Iron Law 33: Keheningan Alat Verifikasi Bukan Kelulusan

**Status:** DRAFT / USULAN. **JANGAN diimplement tanpa GO owner.**
**Tanggal:** 2026-08-04  **Diusulkan setelah:** instance KETIGA "alat verifikasi gagal dengan diam".

---

## Bunyi hukum

> **Law 33 — Verification Tool Liveness.**
> Alat verifikasi harus dibuktikan **bisa BERBICARA** sebelum keheningannya dianggap sebagai lulus.
>
> Setiap gate yang meluluskan berdasarkan **ketiadaan output** (nol baris, nol match, nol error,
> exit 0) WAJIB terlebih dahulu diperlihatkan menghasilkan output pada masukan yang seharusnya
> membuatnya bicara. Sampai itu ditunjukkan, hasil "bersih" dari gate tersebut **tidak bernilai
> sebagai bukti** dan tidak boleh dijadikan dasar verdict.

**Rumusan operasional:** *no output* punya dua sebab yang tak dapat dibedakan dari luar —
(1) benar-benar tidak ada temuan, atau (2) alatnya tidak pernah mampu melihatnya.
Membedakan keduanya menuntut satu pengamatan positif. Tanpa pengamatan itu, verdict bersandar
pada `[INFER]`, yang dilarang.

## Hubungan dengan hukum yang sudah ada

Ini generalisasi dari pelajaran yang sudah tercatat di Law 15 ("every new fail-loud guard MUST be
validated 2-sided: healthy GREEN + injected-material RED"). Law 15 mensyaratkan uji-2-sisi untuk
*guard kesehatan DB*. Law 33 memperluasnya ke **setiap alat verifikasi apa pun** — grep, diff,
dry-run, ekstraksi teks, health probe, cek CI — karena ketiga instance di bawah **tak satu pun**
berupa guard DB, sehingga tak tercakup Law 15.

## Bukti: tiga instance, tiga mekanisme kegagalan berbeda

### Instance 1 — BANK_GAP order-by
Guard rekonsiliasi bank melaporkan NOL selisih. Sebabnya bukan nol selisih, melainkan urutan
`ORDER BY` yang salah sehingga baris yang dibandingkan bukan pasangan yang benar. Guard hijau
karena membandingkan hal yang keliru, bukan karena datanya benar.
**Kelas: gate memeriksa hal yang salah.**

### Instance 2 — zlib-grep pada PDF
Verifikasi isi PDF dengan mencari string di dalam stream ter-kompresi. Selalu nol match →
disimpulkan "teks tidak ada". Sebenarnya WeasyPrint memakai **subsetted font**, sehingga teks
tersimpan sebagai glyph ID, bukan karakter. String yang dicari **tak akan pernah** muncul betapapun
benarnya PDF itu.
**Kelas: gate yang secara struktural mustahil menyala.** Diperbaiki dengan `pdf_text.sh`
(pdfminer, FAIL HARD bila absen).

### Instance 3 — `rsync --delete` dry-run (2026-08-04, sesi ini)
Rencana deploy memakai gate: "`rsync -av --delete --dry-run` harus menunjukkan nol baris
`deleting Dockerfile/nginx.conf/50x.html`" sebagai bukti `--exclude` bekerja.
Dry-run mengembalikan **nol baris `deleting` sama sekali** — dibaca sebagai aman.
Nyatanya perintah itu akan menghapus **302 file**. Mode `--delete` default (*delete-during*)
tidak menstream laporan hapus ke stdout dalam kondisi ini; laporan baru muncul dengan
`--delete-after -i`.
Kalau keheningan itu diterima sebagai lulus, deploy berjalan **buta** terhadap seluruh 302 penghapusan,
dan klaim "exclude terbukti bekerja" akan jadi klaim kosong — kebetulan benar, tapi tanpa bukti.
**Kelas: gate bisu karena mode alat, bukan karena tak ada temuan.**

Deteksinya: hasil "nol" dicurigai justru karena **terlalu bersih** dibanding keadaan yang diketahui
(`main.5558c404.js` terbukti ada di target dan pasti harus tersapu). Perbedaan antara yang diketahui
ada dan yang dilaporkan alat = sinyal alatnya rusak, bukan sinyal target bersih.

## Prosedur yang dituntut (bila diratifikasi)

Untuk setiap gate berbasis-ketiadaan, sebelum dipercaya:

1. **Uji-bicara.** Beri masukan yang pasti seharusnya memicu output; pastikan outputnya muncul.
   Contoh: jalankan dry-run tanpa `--exclude` dan pastikan wrapper MUNCUL sebagai akan-dihapus;
   jalankan grep pada file yang jelas memuat pola.
2. **Jalankan yang persis diuji.** Perintah yang dieksekusi sungguhan harus **identik** dengan yang
   diaudit — beda flag = gate lain. (Di sesi ini: deploy dijalankan dengan `--delete-after -i`,
   bukan `-av --delete`, justru agar sama dengan yang diaudit.)
3. **Bandingkan dengan keadaan yang diketahui.** Kalau ada fakta independen bahwa X seharusnya
   muncul, dan alat tak menyebut X → alat gagal, bukan X tidak ada.
4. **Catat kelas buktinya.** Gate yang lulus tanpa uji-bicara berstatus `[INFER]`, dan
   `[INFER]` tidak boleh menopang verdict.

## Anti-pola yang dilarang

- "Grep-nya kosong, jadi aman" — tanpa pernah melihat grep itu menghasilkan match.
- "Exit 0, jadi lulus" — untuk alat yang exit 0 juga saat tak menemukan apa pun untuk diperiksa.
- "Nol baris `deleting`, jadi exclude bekerja" — instance 3 persisnya.
- Mengganti flag alat setelah audit ("hasilnya sama saja, kan") — mengubah flag = mengubah gate.
- Health probe yang mustahil lulus juga masuk kelas ini dari sisi berlawanan: `/api/health`
  auth-gated selalu 401 → gate yang tak pernah bisa HIJAU sama rusaknya dengan yang tak pernah bisa
  MERAH. Yang benar `/healthz` (tanpa `/api`, unauth, 200).

## Biaya

Satu pengamatan positif per gate, sekali. Instance 3 menghabiskan ~2 menit untuk dibuktikan bisu.
Biaya tak-melakukannya: deploy buta atas 302 penghapusan pada served tree produksi, dengan
kepercayaan palsu bahwa hal itu sudah diverifikasi.

## Yang perlu diputuskan owner

1. Ratifikasi sebagai **Law 33** di `milkyhoop-ironlaws` (nomor berikutnya setelah 32), atau
   perluas Law 15 dari "guard DB" jadi "alat verifikasi apa pun"?
   *Rekomendasi: hukum tersendiri.* Law 15 tentang disaster-recovery/kesehatan DB; ketiga instance
   ini di luar domain itu, dan mengubur aturan umum di dalam Law 15 membuatnya tak ditemukan saat
   orang sedang menulis skrip deploy atau parser PDF.
2. Utang langsung yang jatuh di bawah hukum ini: **gate `BUILD_INFO.json` belum diuji-merah.**
   Ia belum pernah diperlihatkan MERAH pada sha yang salah, jadi menurut Law 33 ia belum boleh
   dipercaya. Uji: deploy sha keliru sekali, pastikan gate menolak.
