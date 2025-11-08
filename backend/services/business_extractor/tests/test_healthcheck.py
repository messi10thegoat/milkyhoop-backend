import grpc
from grpc_health.v1 import health_pb2_grpc, health_pb2

def test_healthcheck_serving():
    channel = grpc.insecure_channel("localhost:5319")
    stub = health_pb2_grpc.HealthStub(channel)
    response = stub.Check(health_pb2.HealthCheckRequest())
    assert response.status == health_pb2.HealthCheckResponse.SERVING
