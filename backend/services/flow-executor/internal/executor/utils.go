package executor

// MergeContextAndInput menggabungkan context map dan input user.
// Input dimasukkan sebagai nested key "input" agar bisa diakses via {{input.xxx}}.
func MergeContextAndInput(contextMap map[string]interface{}, input map[string]interface{}) map[string]interface{} {
	merged := make(map[string]interface{})

	// Salin context
	for k, v := range contextMap {
		merged[k] = v
	}

	// Tambahkan input sebagai nested map
	merged["input"] = input

	return merged
}
