package delivery

import (
	"context"
	"fmt"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"

	pb "github.com/milkyhoop/flow-executor/internal/gen"
	"github.com/milkyhoop/flow-executor/internal/utils"
)

// LogComplaint memanggil gRPC ke complaint_service.CreateComplaint
func LogComplaint(userID string, message string) (string, error) {
	utils.Log.Info().
		Str("user_id", userID).
		Str("message", message).
		Msg("📨 Logging complaint via gRPC")

	conn, err := grpc.Dial("complaint_service:5010", grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		return "", fmt.Errorf("❌ Gagal konek ke complaint_service: %w", err)
	}
	defer conn.Close()

	client := pb.NewComplaintServiceClient(conn)

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	req := &pb.CreateComplaintRequest{
		UserId:  userID,
		Message: message,
		Product: "unknown",
		Source:  "flow-executor",
		Emotion: "neutral",
	}

	resp, err := client.CreateComplaint(ctx, req)
	if err != nil {
		return "", fmt.Errorf("❌ Gagal kirim complaint: %w", err)
	}

	utils.Log.Info().
		Str("complaint_id", resp.ComplaintId).
		Msg("✅ Complaint gRPC sukses")

	return resp.ComplaintId, nil
}
