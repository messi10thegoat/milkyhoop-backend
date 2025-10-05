package observer

import (
	"context"
	"fmt"

	"github.com/milkyhoop/flow-executor/internal/utils"
)

func HandleSendBotReply(ctx context.Context, input map[string]interface{}) (map[string]interface{}, error) {
	message, ok := input["message"].(string)
	if !ok || message == "" {
		utils.Log.Warn().Msg("🟡 SendBotReply: message kosong atau tidak valid")
		return nil, fmt.Errorf("SendBotReply: invalid or empty message")
	}

	utils.Log.Info().Str("message", message).Msg("📤 SendBotReply executed")

	output := map[string]interface{}{
		"message": message,
	}

	return output, nil
}
