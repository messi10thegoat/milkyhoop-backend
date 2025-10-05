package delivery

import (
	"context"
	"log"
	"os"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"

	pb "github.com/milkyhoop/flow-executor/internal/proto/tenant_manager"
	"google.golang.org/protobuf/types/known/emptypb"
)

// ListTenants memanggil gRPC ke TenantManager service untuk mengambil daftar tenant.
func ListTenants() {
	host := os.Getenv("TENANT_MANAGER_HOST")
	if host == "" {
		host = "localhost:5000" // default Docker Compose
	}

	conn, err := grpc.Dial(host, grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		log.Fatalf("❌ Gagal konek tenant manager: %v", err)
	}
	defer conn.Close()

	client := pb.NewTenantManagerClient(conn)

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	// RPC request pakai google.protobuf.Empty{}
	res, err := client.ListTenants(ctx, &emptypb.Empty{})
	if err != nil {
		log.Fatalf("❌ Error ListTenants: %v", err)
	}

	log.Printf("✅ Tenants: %v\n", res.Tenants)
}
