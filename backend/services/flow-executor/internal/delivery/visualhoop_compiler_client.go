package delivery

import (
	"context"
	"log"
	"os"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"

	pb "github.com/milkyhoop/flow-executor/internal/proto/visualhoop_compiler"
)

// CompileJSON memanggil VisualhoopCompiler gRPC service untuk compile JSON ke .pb
func CompileJSON(jsonPath, outputPath string) error {
	// Ambil host Visualhoop-Compiler dari ENV (untuk mode lokal/testing)
	host := os.Getenv("VISUALHOOP_COMPILER_HOST")
	if host == "" {
		host = "visualhoop-compiler:5001" // default Docker Compose
	}

	// Dial ke service Visualhoop-Compiler
	conn, err := grpc.Dial(host, grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		return err
	}
	defer conn.Close()

	client := pb.NewVisualhoopCompilerClient(conn)

	// Konteks dengan timeout 10 detik
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	// RPC request
	req := &pb.CompileRequest{
		JsonPath:   jsonPath,
		OutputPath: outputPath,
	}

	// Eksekusi gRPC call
	resp, err := client.CompileJsonToPb(ctx, req)
	if err != nil {
		return err
	}

	log.Printf("✅ Visualhoop-Compiler Response: %s", resp.GetMessage())
	return nil
}
