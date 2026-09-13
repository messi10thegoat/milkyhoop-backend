-- FASE LAMA (sebelum V244 di transaksi ini): G1 — ubah nominal dokumen berjurnal HARUS BERHASIL.
\set ON_ERROR_STOP 1
SELECT pg_temp.coba('G1 merah-lama', w.t || '.' || w.kol || ' +1 pada dokumen berjurnal',
       format('UPDATE %I SET %I = %I + 1 WHERE id = %L', w.t, w.kol, w.kol, s.id), 'lolos')
FROM wakil w JOIN subjek s ON s.t = w.t;

SELECT pg_temp.coba('G1 merah-lama', w.t || '.journal_id -> NULL',
       format('UPDATE %I SET journal_id = NULL WHERE id = %L', w.t, s.id), 'lolos')
FROM wakil w JOIN subjek s ON s.t = w.t WHERE w.t = 'bills';
