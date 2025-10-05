package delivery

import (
	"encoding/json"
	"net/http"
	"os"
	"path/filepath"

	"github.com/milkyhoop/flow-executor/internal/executor"
	"github.com/milkyhoop/flow-executor/internal/utils"
)

// HandleFlowExecute menangani POST /flow/execute
func HandleFlowExecute(w http.ResponseWriter, r *http.Request) {
	type Req struct {
		FlowPath string                 `json:"flow_path"`
		Input    map[string]interface{} `json:"input"`
	}

	var req Req
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "❌ Gagal parse JSON: "+err.Error(), http.StatusBadRequest)
		return
	}

	fullpath := filepath.Join("flows/global", req.FlowPath)
	if _, err := os.Stat(fullpath); err != nil {
		http.Error(w, "❌ File tidak ditemukan: "+fullpath, http.StatusNotFound)
		return
	}

	// ✅ FIX: Gunakan RunFlowAndReturnOutput untuk mendapatkan hasil
	result, err := executor.RunFlowAndReturnOutput(fullpath, req.Input)
	if err != nil {
		http.Error(w, "❌ Gagal eksekusi flow: "+err.Error(), http.StatusInternalServerError)
		return
	}

	utils.Log.Info().
		Str("flow_path", req.FlowPath).
		Str("fullpath", fullpath).
		Interface("result", result).
		Msg("✅ Flow executed successfully")

	// ✅ FIX: Kirim hasil sebagai JSON response
	w.Header().Set("Content-Type", "application/json")
	response := map[string]interface{}{
		"status":    "success",
		"flow_path": req.FlowPath,
		"result":    result,
	}

	if err := json.NewEncoder(w).Encode(response); err != nil {
		http.Error(w, "❌ Gagal encode response: "+err.Error(), http.StatusInternalServerError)
		return
	}
}