package delivery

import (
	"context"
	"fmt"
	"log"
	"net"

	pb "github.com/milkyhoop/notification-service/internal/delivery/pb/notification"
	"google.golang.org/grpc"
	"google.golang.org/grpc/health/grpc_health_v1"
)

type NotificationHandler struct {
	pb.UnimplementedNotificationServiceServer
}

func (h *NotificationHandler) SendNotification(
	ctx context.Context,
	req *pb.NotificationRequest,
) (*pb.NotificationResponse, error) {
	return &pb.NotificationResponse{
		Status:    "ok",
		MessageId: "demo-id-123",
	}, nil
}

func StartGRPCServer() {
	lis, err := net.Listen("tcp", ":5005")
	if err != nil {
		log.Fatalf("❌ Failed to listen: %v", err)
	}

	grpcServer := grpc.NewServer()

	// ✅ Register Notification Service
	pb.RegisterNotificationServiceServer(grpcServer, &NotificationHandler{})

	// ✅ Register Health Service
	healthSvc := InitHealthCheck()
	grpc_health_v1.RegisterHealthServer(grpcServer, healthSvc)

	fmt.Println("✅ gRPC NotificationService running on :5005")
	if err := grpcServer.Serve(lis); err != nil {
		log.Fatalf("❌ Failed to serve: %v", err)
	}
}
