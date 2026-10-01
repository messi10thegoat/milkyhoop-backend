-- Rollback V355: buang tabel tautan publik (tautan yang sudah dibagikan berhenti berlaku).
DROP TABLE IF EXISTS document_shares;
