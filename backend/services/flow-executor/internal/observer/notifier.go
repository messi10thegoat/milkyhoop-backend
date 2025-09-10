package observer

import (
	"context"
	"fmt"
	"log"
	"os"
	"sync"
	"time"
	"google.golang.org/grpc"
	"github.com/segmentio/kafka-go"
	pb "github.com/milkyhoop/flow-executor/internal/proto"
)

var kafkaWriter *kafka.Writer
var (
	ragClient pb.RagLlmServiceClient
	connOnce  sync.Once
)

func InitKafkaWriter(brokers []string) {
	kafkaWriter = &kafka.Writer{
		Addr:     kafka.TCP(brokers...),
		Balancer: &kafka.LeastBytes{},
	}
}

func PublishKafkaMessage(ctx context.Context, topic string, payload []byte) error {
	if kafkaWriter == nil {
		return fmt.Errorf("kafka writer not initialized")
	}
	msg := kafka.Message{
		Topic: topic,
		Value: payload,
	}
	return kafkaWriter.WriteMessages(ctx, msg)
}

func DummyShowMenu(ctx context.Context, input map[string]interface{}) (map[string]interface{}, error) {
	return map[string]interface{}{"menu": "Dummy menu"}, nil
}

func DummyCreateOrder(ctx context.Context, input map[string]interface{}) (map[string]interface{}, error) {
	return map[string]interface{}{"order_id": "12345"}, nil
}

func DummySendNotification(ctx context.Context, input map[string]interface{}) (map[string]interface{}, error) {
	return map[string]interface{}{"status": "sent"}, nil
}

func LogComplaint(userID string, message string) (string, error) {
	return "complaint-xyz", nil
}

func getRagClient() pb.RagLlmServiceClient {
	connOnce.Do(func() {
		ragHost := os.Getenv("RAGLLM_GRPC_HOST")
		ragPort := os.Getenv("RAGLLM_GRPC_PORT")
		if ragHost == "" {
			ragHost = "ragllm_service"
		}
		if ragPort == "" {
			ragPort = "5000"
		}
		target := fmt.Sprintf("%s:%s", ragHost, ragPort)
		
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		
		conn, err := grpc.DialContext(ctx, target, grpc.WithInsecure(), grpc.WithBlock())
		if err != nil {
			log.Printf("❌ Gagal konek ke RAG LLM service: %v", err)
			return
		}
		ragClient = pb.NewRagLlmServiceClient(conn)
	})
	return ragClient
}

func QueryRAG(query, tenantID string) (string, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	
	req := &pb.GenerateAnswerRequest{
		Question: query,
		TenantId: tenantID,
	}
	
	res, err := getRagClient().GenerateAnswer(ctx, req)
	if err != nil {
		return "", fmt.Errorf("❌ Gagal query ke RAG LLM: %w", err)
	}
	return res.GetAnswer(), nil
}

func PublishNotification(userID string, message string) error {
	fmt.Printf("📢 Notification sent to %s: %s\n", userID, message)
	return nil
}
