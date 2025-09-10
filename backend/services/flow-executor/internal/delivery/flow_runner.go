package delivery

import (
	"fmt"
	"os"
	"path/filepath"

	"github.com/milkyhoop/flow-executor/internal/utils"
	flowpb "github.com/milkyhoop/flow-executor/internal/proto/flow"

	"google.golang.org/protobuf/proto"
)

// Fungsi ini bertugas membaca file .pb dan mengembalikan FlowSpec hasil parsing
func LoadFlowFromProtobufFile(path string) (flowpb.Flow, error) {
	_, file := filepath.Split(path)
	jsonPath := file[:len(file)-3] + "json"
	pbPath := path

	err := CompileJSON(jsonPath, pbPath)
	if err != nil {
		return flowpb.Flow{}, fmt.Errorf("failed to compile JSON to .pb: %w", err)
	}

	utils.Log.Info().
		Str("json_path", jsonPath).
		Str("pb_path", pbPath).
		Msg("✅ JSON compiled to .pb via Visualhoop-compiler")

	data, err := os.ReadFile(pbPath)
	if err != nil {
		return flowpb.Flow{}, fmt.Errorf("failed to read protobuf file: %w", err)
	}

	var protoFlow flowpb.Flow
	if err := proto.Unmarshal(data, &protoFlow); err != nil {
		return flowpb.Flow{}, fmt.Errorf("failed to unmarshal .pb: %w", err)
	}

	return protoFlow, nil
}
