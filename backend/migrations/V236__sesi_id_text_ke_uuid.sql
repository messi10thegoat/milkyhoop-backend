-- V236: id sesi chat yang disimpan sebagai TEKS dijadikan `uuid`.
--
-- LATAR TERUKUR 2026-09-03
-- `chat_sessions.id` bertipe `uuid`, jadi Postgres selalu membakukannya menjadi
-- huruf kecil. Tapi `pending_actions.conversation_id` dan
-- `chat_workflow_state.chat_session_id` bertipe TEXT, jadi keduanya menyimpan
-- apa adanya dari klien. Satu identitas, dua tipe: yang satu membakukan, yang
-- satu tidak.
--
-- Akibatnya terukur, bukan hipotetis: pada 29-30 Agustus 2026 seorang pengguna
-- memakai klien yang membangkitkan UUID huruf KAPITAL, dan 153 baris terbaca
-- sebagai YATIM padahal induknya hidup. `WHERE conversation_id = $1` MELESET
-- tanpa galat, dan setiap JOIN antar keduanya menjatuhkan baris tanpa suara.
--
-- KENAPA TIPE, BUKAN SEKADAR PEMBAKUAN DI KODE
-- Pembakuan di kode sudah mendarat (BE `5886a7e7`) dan sudah cukup untuk
-- menghentikan pendarahan. Perubahan tipe ini menutup sumbernya: sesudah ini
-- kolomnya TIDAK BISA lagi menyimpan bentuk yang tak baku, siapa pun
-- penulisnya dan lewat jalur apa pun.
--
-- PRASYARAT YANG SUDAH DIPENUHI
--   P1  id cacat ditolak 422 di tepi, bukan 500 dari driver   (BE `bb969a2d`)
--   P2  sentinel "unknown" dibuang; ia kunci BERSAMA antar-tenant karena
--       UNIQUE (chat_session_id, workflow_type) tidak memuat tenant_id
--                                                             (BE `4d9a71bd`)
-- Tanpa P2, jalur mati itu akan berubah dari "menulis teks aneh" menjadi galat
-- keras justru oleh migrasi ini.
--
-- SENSUS ULANG tepat sebelum diterapkan (angka sebelumnya diambil pada tabel
-- yang baru dipangkas, jadi tidak boleh dipercaya begitu saja):
--   pending_actions      58 baris · 4 NULL · 0 kosong · 0 tak-bisa-dicast
--   chat_workflow_state   1 baris · 0 NULL · 0 kosong · 0 tak-bisa-dicast
--
-- SENGAJA TIDAK MENAMBAH FOREIGN KEY ke `chat_sessions`. Godaannya besar saat
-- tipenya sudah sama, tapi FK mengubah perilaku pemangkasan sesi (meng-CASCADE
-- atau menolak) -- jauh lebih besar daripada perubahan tipe, dan harus
-- diputuskan terpisah. Hari ini FK ke chat_sessions berjumlah NOL.

BEGIN;

-- Gagal-tutup: kalau ada SATU baris yang tak bisa di-cast, batalkan SEBELUM
-- ALTER, dengan pesan yang menyebut jumlahnya. Sensus di atas adalah hipotesis
-- sampai penjaga ini menyetujuinya.
DO $$
DECLARE n bigint;
BEGIN
  SELECT count(*) INTO n FROM pending_actions
   WHERE conversation_id IS NOT NULL AND conversation_id <> ''
     AND conversation_id !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$';
  IF n > 0 THEN
    RAISE EXCEPTION 'V236 dibatalkan: pending_actions.conversation_id punya % baris yang tak bisa di-cast ke uuid', n;
  END IF;

  SELECT count(*) INTO n FROM chat_workflow_state
   WHERE chat_session_id IS NOT NULL AND chat_session_id <> ''
     AND chat_session_id !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$';
  IF n > 0 THEN
    RAISE EXCEPTION 'V236 dibatalkan: chat_workflow_state.chat_session_id punya % baris yang tak bisa di-cast ke uuid', n;
  END IF;
END $$;

-- String kosong bukan uuid dan bukan pula "tak diketahui yang bermakna";
-- NULL adalah bentuk yang jujur untuknya. Nol baris saat diukur.
UPDATE pending_actions     SET conversation_id = NULL WHERE conversation_id = '';
UPDATE chat_workflow_state SET chat_session_id  = NULL WHERE chat_session_id  = '';

ALTER TABLE pending_actions
  ALTER COLUMN conversation_id TYPE uuid USING conversation_id::uuid;

ALTER TABLE chat_workflow_state
  ALTER COLUMN chat_session_id TYPE uuid USING chat_session_id::uuid;

COMMIT;
