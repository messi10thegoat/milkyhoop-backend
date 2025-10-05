package ragclient

import (
	"context"
	"fmt"
	"log"
	"os"
	"sync"
	"time"

	"google.golang.org/grpc"
	ragcrud_pb "github.com/milkyhoop/flow-executor/internal/proto/ragcrud"
)

var (
	ragCrudClient   ragcrud_pb.RagCrudServiceClient
	ragCrudConnOnce sync.Once
)

func getRagCrudClient() ragcrud_pb.RagCrudServiceClient {
	ragCrudConnOnce.Do(func() {
		ragCrudHost := os.Getenv("RAGCRUD_GRPC_HOST")
		ragCrudPort := os.Getenv("RAGCRUD_GRPC_PORT")
		if ragCrudHost == "" {
			ragCrudHost = "ragcrud_service"
		}
		if ragCrudPort == "" {
			ragCrudPort = "5001"
		}
		ragCrudAddr := fmt.Sprintf("%s:%s", ragCrudHost, ragCrudPort)
			

		ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
		defer cancel()

		conn, err := grpc.DialContext(ctx, ragCrudAddr, grpc.WithInsecure(), grpc.WithBlock())
		if err != nil {
			log.Fatalf("❌ Gagal konek ke RAG CRUD service: %v", err)
		}

		ragCrudClient = ragcrud_pb.NewRagCrudServiceClient(conn)
	})
	return ragCrudClient
}

func UpdateRagDocument(id int32, title, content string) (*ragcrud_pb.RagDocumentResponse, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()

	req := &ragcrud_pb.UpdateRagDocumentRequest{
		Id:      id,
		Title:   title,
		Content: content,
	}

	resp, err := getRagCrudClient().UpdateRagDocument(ctx, req)
	if err != nil {
		return nil, fmt.Errorf("❌ Gagal update RAG document: %w", err)
	}

	return resp, nil
}

func DeleteRagDocument(id int32) (*ragcrud_pb.RagDocumentResponse, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()

	req := &ragcrud_pb.DeleteRagDocumentRequest{
		Id: id,
	}

	resp, err := getRagCrudClient().DeleteRagDocument(ctx, req)
	if err != nil {
		return nil, fmt.Errorf("❌ Gagal delete RAG document: %w", err)
	}

	return resp, nil
}

func UpdateRAGDocument(id int32, title, content string) (string, error) {
	resp, err := UpdateRagDocument(id, title, content)
	if err != nil {
		return "", err
	}

	return fmt.Sprintf("✅ Document ID %d berhasil diupdate: %s", resp.Id, resp.Title), nil
}

func DeleteRAGDocument(id int32) (string, error) {
	resp, err := DeleteRagDocument(id)
	if err != nil {
		return "", err
	}

	return fmt.Sprintf("✅ Document ID %d berhasil dihapus: %s", resp.Id, resp.Title), nil
}

func UpdateRagDocumentBySearch(tenantID, searchContent, newContent string) (*ragcrud_pb.RagDocumentResponse, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()

	req := &ragcrud_pb.UpdateRagDocumentBySearchRequest{
		TenantId:      tenantID,
		SearchContent: searchContent,
		NewContent:    newContent,
	}

	resp, err := getRagCrudClient().UpdateRagDocumentBySearch(ctx, req)
	if err != nil {
		return nil, fmt.Errorf("❌ Gagal update RAG document by search: %w", err)
	}

	return resp, nil
}

func UpdateRAGDocumentBySearch(tenantID, searchContent, newContent string) (string, error) {
	resp, err := UpdateRagDocumentBySearch(tenantID, searchContent, newContent)
	if err != nil {
		return "", err
	}

	return fmt.Sprintf("✅ Document berhasil diupdate: %s", resp.Title), nil
}


func QueryRAG(query, tenantID string) (string, error) {
    log.Printf("🔍 QueryRAG called with query: %s, tenant: %s", query, tenantID)
    
    ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
    defer cancel()
    
    log.Printf("🔗 Attempting gRPC call to ragcrud_service...")
    
    // Use new FuzzySearchDocuments gRPC method
    req := &ragcrud_pb.FuzzySearchRequest{
        TenantId: tenantID,
        SearchContent: query,
        SimilarityThreshold: 0.7,
    }
    
    resp, err := getRagCrudClient().FuzzySearchDocuments(ctx, req)
    if err != nil {
        log.Printf("❌ FuzzySearch failed: %v", err)
        return "", fmt.Errorf("❌ FuzzySearch failed: %w", err)
    }
    
    log.Printf("✅ FuzzySearch success, found %d documents", len(resp.Documents))
    
    // Return first matching document
    if len(resp.Documents) > 0 {
        return resp.Documents[0].Content, nil
    }
    
    return fmt.Sprintf("Tidak ditemukan FAQ untuk: %s", query), nil
}


func CreateRagDocument(tenantID, title, content string) (*ragcrud_pb.RagDocumentResponse, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()

	req := &ragcrud_pb.CreateRagDocumentRequest{
		TenantId: tenantID,
		Title:    title,
		Content:  content,
		Source:   "conversational_faq",
		Tags:     []string{"faq"},
	}

	resp, err := getRagCrudClient().CreateRagDocument(ctx, req)
	if err != nil {
		return nil, fmt.Errorf("❌ Gagal create RAG document: %w", err)
	}

	return resp, nil
}

func CreateRAGDocument(tenantID, title, content string) (string, error) {
	resp, err := CreateRagDocument(tenantID, title, content)
	if err != nil {
		return "", err
	}

	return fmt.Sprintf("✅ FAQ berhasil dibuat: %s", resp.Title), nil
}