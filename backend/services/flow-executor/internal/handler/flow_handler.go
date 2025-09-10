package handler

import (
	"encoding/json"
	"net/http"
	"os"
	"path/filepath"
	"strings"

	"github.com/milkyhoop/flow-executor/internal/executor"
	"github.com/milkyhoop/flow-executor/internal/utils"
)

// Handler aman tanpa siklus import
func HandleFlowExecute(w http.ResponseWriter, r *http.Request) {
	filename := strings.TrimPrefix(r.URL.Path, "/run-flow/")
	fullpath := filepath.Join("flows/examples", filename)

	globalPath := filepath.Join("flows/global", filename)
	if _, err := os.Stat(globalPath); err == nil {
		fullpath = globalPath
	}

	var input map[string]interface{}
	if r.Method == http.MethodPost {
		if err := json.NewDecoder(r.Body).Decode(&input); err != nil {
			utils.Log.Warnf("⚠️ Tidak bisa parse input JSON: %v", err)
			input = map[string]interface{}{}
		}
	}

	utils.Log.Debugf("🟡 Received Input: %+v", input)

	output, err := executor.RunFlowAndReturnOutput(fullpath, input)
	if err != nil {
		utils.Log.Errorf("❌ Error running flow %s: %v", filename, err)
		http.Error(w, "❌ Error running flow: "+err.Error(), http.StatusInternalServerError)
		return
	}

	// Ambil hasil akhir (contoh: dari static_reply)
	reply := output["message"]
	resp := map[string]interface{}{
		"reply": reply,
	}

	w.Header().Set("Content-Type", "application/json")
	if err := json.NewEncoder(w).Encode(resp); err != nil {
		utils.Log.Errorf("❌ Gagal encode output: %v", err)
		http.Error(w, "❌ Gagal encode output", http.StatusInternalServerError)
	}
}
