from typing import Optional, List, Dict, Union
from pydantic import BaseModel
from typing_extensions import Literal

class Customer(BaseModel):
    customer_name: Optional[str]
    customer_type: Optional[str]
    contact_info: Optional[str]
    location: Optional[str]
    platform: Optional[str]
    device_info: Optional[str]
    emotion: Optional[str]
    account_number: Optional[str]

class OrderTransaction(BaseModel):
    order_id: Optional[str]
    date: Optional[str]
    item_name: Union[str, List[str], None]
    quantity: Union[int, List[int], None]
    item_attributes: Optional[Union[str, List[str]]]
    product_variant_code: Optional[str]
    price: Optional[str]
    shipping_cost: Optional[str]
    payment_method: Optional[str]
    payment_status: Optional[str]
    bank_info: Optional[Dict[str, str]]
    invoice_number: Optional[str]
    transaction_id: Optional[str]
    voucher_code: Optional[str]
    promo_applied: Optional[str]
    additional_notes: Optional[str]
    dropship_info: Optional[str]
    delivery_note: Optional[str]

class DeliveryShipping(BaseModel):
    delivery_status: Optional[str]
    delivery_method: Optional[str]
    courier_name: Optional[str]
    shipping_time_estimate: Optional[str]
    packing_request: Optional[str]
    shipping_request: Optional[str]

class ProductServiceIssue(BaseModel):
    ticket_id: Optional[str]
    reason: Optional[str]
    product_condition: Optional[str]
    return_status: Optional[str]
    refund_status: Optional[str]
    refund_amount: Optional[str]
    response_requested: Optional[str]
    followup_needed: Optional[str]
    related_ticket_id: Optional[str]

class SellerInternal(BaseModel):
    stock_status: Optional[str]
    sales_channel: Optional[str]
    reseller_info: Optional[str]
    admin_name: Optional[str]
    assigned_to: Optional[str]
    department: Optional[str]
    sla_status: Optional[str]
    case_status: Optional[str]
    internal_notes: Optional[str]

class FeedbackReview(BaseModel):
    rating_feedback: Optional[str]

class IntentOutput(BaseModel):
    intent: Literal[
        "order_request", "inquiry", "complaint", "return_request",
        "cancel_order", "tracking_request", "confirmation", "others"
    ]
    entities: Dict[str, Union[
        Customer, OrderTransaction, DeliveryShipping,
        ProductServiceIssue, SellerInternal, FeedbackReview,
        Dict[str, Union[str, int, float, List[str]]]
    ]]
