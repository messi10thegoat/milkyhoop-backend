package delivery

import (
	"context"
	"encoding/json"
	"fmt"
	"io/ioutil"
	"net"
	"os"
	"path/filepath"

	"github.com/rs/zerolog/log"
	"google.golang.org/grpc"
	"google.golang.org/grpc/health"
	"google.golang.org/grpc/health/grpc_health_v1"
	"google.golang.org/protobuf/proto"

	pb "milkyhoop/backend/services/visualhoop-compiler/internal/proto"
)

var jsonBasePath string

func init() {
	jsonBasePath = os.Getenv("JSON_BASE_PATH")
	if jsonBasePath == "" {
		jsonBasePath = "/root/milkyhoop/flows/compiled" // default base path jika env tidak di-set
	}
}

type CompilerServer struct {
	pb.UnimplementedVisualhoopCompilerServer
}

func (s *CompilerServer) CompileJsonToPb(ctx context.Context, req *pb.CompileRequest) (*pb.CompileResponse, error) {
	log.Info().Msg("🔧 Received CompileJsonToPb request")

	// Gabungkan base path dengan path JSON yang dikirim client
	fullJsonPath := filepath.Join(jsonBasePath, req.GetJsonPath())

	// Baca file JSON dari full path
	jsonData, err := ioutil.ReadFile(fullJsonPath)
	if err != nil {
		log.Error().Err(err).Str("path", fullJsonPath).Msg("❌ Failed to read JSON file")
		return nil, fmt.Errorf("failed to read JSON file '%s': %w", fullJsonPath, err)
	}

	// Unmarshal JSON ke struct proto Flow
	var flow pb.Flow
	if err := json.Unmarshal(jsonData, &flow); err != nil {
		log.Error().Err(err).Msg("❌ Failed to unmarshal JSON to Flow")
		return nil, fmt.Errorf("failed to unmarshal JSON: %w", err)
	}

	// Marshal struct proto ke binary .pb
	pbData, err := proto.Marshal(&flow)
	if err != nil {
		log.Error().Err(err).Msg("❌ Failed to marshal proto")
		return nil, fmt.Errorf("failed to marshal proto: %w", err)
	}

	// Simpan binary .pb ke path output yang diminta
	if err := ioutil.WriteFile(req.GetOutputPath(), pbData, 0644); err != nil {
		log.Error().Err(err).Msg("❌ Failed to write .pb file")
		return nil, fmt.Errorf("failed to write .pb file: %w", err)
	}

	log.Info().Str("output", req.GetOutputPath()).Msg("✅ .pb file generated successfully")
	return &pb.CompileResponse{Message: "Compile success!"}, nil
}

// RunCompilerServer menjalankan gRPC server dan health check
func RunCompilerServer(port string) error {
	lis, err := net.Listen("tcp", ":"+port)
	if err != nil {
		return fmt.Errorf("failed to listen: %w", err)
	}
	grpcServer := grpc.NewServer()

	// Register server compiler
	pb.RegisterVisualhoopCompilerServer(grpcServer, &CompilerServer{})

	// Register health check service
	healthServer := health.NewServer()
	grpc_health_v1.RegisterHealthServer(grpcServer, healthServer)

	log.Info().Msgf("🚀 Visualhoop-compiler server running on port %s", port)
	return grpcServer.Serve(lis)
}
