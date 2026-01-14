package main

import (
	"context"
	"log"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"

	pb "milkyhoop/backend/services/visualhoop-compiler/internal/proto"
)

func main() {
	conn, err := grpc.Dial("localhost:5001", grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		log.Fatalf("❌ Failed to connect: %v", err)
	}
	defer conn.Close()

	client := pb.NewVisualhoopCompilerClient(conn)

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	req := &pb.CompileRequest{
		JsonPath:   "backend/services/visualhoop-compiler/tests/testdata/sample_flow.json",
		OutputPath: "backend/services/visualhoop-compiler/tests/testdata/sample_flow.pb",
	}

	resp, err := client.CompileJsonToPb(ctx, req)
	if err != nil {
		log.Fatalf("❌ CompileJsonToPb failed: %v", err)
	}

	log.Printf("✅ Response: %s", resp.GetMessage())
}
