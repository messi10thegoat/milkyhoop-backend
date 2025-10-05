package main

import (
	"log"
	"os"

	"google.golang.org/protobuf/proto"
	"github.com/milkyhoop/flow-executor/internal/proto/flow" // ✅ hasil generate
)


func main() {
	flowData := &flow.Flow{
		Id: "sample-dining-v1",
		Nodes: []*flow.Node{
			{Id: "n1", Hoop: "ShowMenu", InputFrom: ""},
			{Id: "n2", Hoop: "CreateOrder", InputFrom: "n1"},
			{Id: "n3", Hoop: "SendNotification", InputFrom: "n2"},
		},
	}

	data, err := proto.Marshal(flowData)
	if err != nil {
		log.Fatalf("❌ Failed to marshal flow: %v", err)
	}

	const outPath = "flows/compiled/sample_flow.pb"
	if err := os.MkdirAll("flows/compiled", 0755); err != nil {
		log.Fatalf("❌ Failed to create output directory: %v", err)
	}

	if err := os.WriteFile(outPath, data, 0644); err != nil {
		log.Fatalf("❌ Failed to write file %s: %v", outPath, err)
	}

	log.Printf("✅ Flow .pb file generated at: %s\n", outPath)
}
