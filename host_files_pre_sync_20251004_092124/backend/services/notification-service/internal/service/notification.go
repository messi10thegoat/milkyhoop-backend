package service

import (
	"encoding/json"
	"log"
	"regexp"
)

// HandleNotification adalah entry point modular untuk proses payload notifikasi
func HandleNotification(raw []byte) error {
	log.Printf("🔔 [NOTIF] Received payload: %s", string(raw))

	var payload map[string]interface{}
	if err := json.Unmarshal(raw, &payload); err != nil {
		log.Printf("❌ Gagal parsing JSON payload: %v", err)
		return err
	}

	// Deteksi apakah masih ada placeholder seperti {{input.message}} di seluruh nilai string
	placeholderRegex := regexp.MustCompile(`\{\{.*?\}\}`)
	hasPlaceholder := false

	// Cek recursive
	var checkPlaceholders func(interface{}) bool
	checkPlaceholders = func(v interface{}) bool {
		switch val := v.(type) {
		case map[string]interface{}:
			for _, item := range val {
				if checkPlaceholders(item) {
					return true
				}
			}
		case []interface{}:
			for _, item := range val {
				if checkPlaceholders(item) {
					return true
				}
			}
		case string:
			if placeholderRegex.MatchString(val) {
				return true
			}
		}
		return false
	}

	hasPlaceholder = checkPlaceholders(payload)
	if hasPlaceholder {
		log.Printf("⚠️ WARNING: Payload masih mengandung placeholder yang belum dirender: %s", string(raw))
	} else {
		log.Printf("✅ Payload siap diproses.")
	}

	// TODO: parsing lanjut → simpan ke DB, kirim ke WA/email, dll
	return nil
}
