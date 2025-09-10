from dataclasses import dataclass

@dataclass
class PaymentResponse:
    payment_link: str
    session_id: str
    provider: str
