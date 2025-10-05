#!/bin/bash

case $1 in
    "snap")
        ./scripts/rollback/snapshot.sh milkyhoop "${2:-manual}"
        ;;
    "list")
        echo "📋 Available snapshots:"
        ls snapshots/*.json 2>/dev/null | sed 's/snapshots\///g' | sed 's/.json//g' | sort -r
        ;;
    "rollback")
        ./scripts/rollback/rollback.sh "$2"
        ;;
    "health")
        docker ps --filter name=milkyhoop --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
        ;;
    *)
        echo "MilkyHoop Quick Rollback Commands:"
        echo "  ./quick.sh snap [description]     - Create snapshot"
        echo "  ./quick.sh list                   - List snapshots"
        echo "  ./quick.sh rollback <snapshot_id> - Rollback to snapshot"
        echo "  ./quick.sh health                 - Check system status"
        ;;
esac
