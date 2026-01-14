import grpc
import pytest
from google.protobuf import empty_pb2
from grpc_health.v1 import health_pb2_grpc, health_pb2


def test_healthcheck_serving():
    channel = grpc.insecure_channel("localhost:5019")
    stub = health_pb2_grpc.HealthStub(channel)
    request = health_pb2.HealthCheckRequest()
    response = stub.Check(request, timeout=3)

    assert response.status == health_pb2.HealthCheckResponse.SERVING
