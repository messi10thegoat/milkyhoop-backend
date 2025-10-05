package tests

import (
	"encoding/json"
	"os"
	"testing"

	"github.com/milkyhoop/flow-executor/internal/executor"
	"github.com/milkyhoop/flow-executor/internal/observer"
)

func TestComplaintFlow(t *testing.T) {
	// ✅ Init logger dulu (wajib sebelum RunFlow)
	observer.InitLogger("flow-executor-test")

	// Inject input test
	input := map[string]interface{}{
		"message": "Roti gosong dan keras",
		"user_id": "user_001",
	}

	// ✅ Path ke flow testdata (pastikan sudah disalin ke sini)
	path := "testdata/complaint-handler.json"

	// Validasi eksistensi file
	if _, err := os.Stat(path); os.IsNotExist(err) {
		t.Fatalf("❌ File tidak ditemukan: %s", path)
	}

	// Log input yang akan dipakai
	inputJSON, _ := json.MarshalIndent(input, "", "  ")
	t.Logf("🔍 Input yang di-inject:\n%s", string(inputJSON))

	// Eksekusi flow
	err := executor.RunFlowFromFileWithInput(path, input)
	if err != nil {
		t.Fatalf("❌ Flow gagal dijalankan: %v", err)
	}

	t.Log("✅ Flow dijalankan sukses.")
}
