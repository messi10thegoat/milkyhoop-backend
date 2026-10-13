"""
Pydantic schemas for Vendors module.

This module defines request and response models for the /api/vendors endpoints.
"""

from decimal import Decimal
from pydantic import AliasChoices, BaseModel, Field, field_validator, model_validator
from typing import Optional, List, Dict, Any, Literal, Union
import re

from .identitas_pajak import IdentitasPajakDasar


# =============================================================================
# REQUEST MODELS
# =============================================================================


# =============================================================================
# FIELD FORM PEMASOK YANG DULU DIBUANG DIAM (13 Okt 2026, W-20261013-VENDORFIELD)
# =============================================================================
# FE (useVendorForm.ts) mengirim: mobile, nik, nitku, negara, default_*, tax_address/shipping_address sebagai OBJEK,
# contacts[]. Skema lama tak punya/menolak (tax_address objek -> 422 string_type) atau membuangnya diam.
# Pola NetSuite/Accurate: alamat terstruktur, kontak = sublist entitas (tabel vendor_contacts / vendor_addresses).
# Aturan "kunci absen = tak tersentuh; null = tak ada perubahan; objek/daftar = ganti seluruhnya".

NIK_16_DIGIT = "NIK harus 16 digit"


def _teks_atau_none(v):
    if v is None:
        return None
    v = str(v).strip()
    return v or None


class AlamatTerstruktur(BaseModel):
    """Alamat terstruktur (bentuk yang dikirim/dibaca FE): street/city/province/postal_code."""

    street: Optional[str] = None
    city: Optional[str] = Field(None, max_length=100)
    province: Optional[str] = Field(None, max_length=100)
    postal_code: Optional[str] = Field(None, max_length=20, validation_alias=AliasChoices("postal_code", "postcode"))

    @field_validator("street", "city", "province", "postal_code", mode="before")
    @classmethod
    def _bersih(cls, v):
        return _teks_atau_none(v)

    def kosong(self) -> bool:
        return not any((self.street, self.city, self.province, self.postal_code))


class KontakPemasok(BaseModel):
    """Satu baris vendor_contacts."""

    name: Optional[str] = Field(None, max_length=255)
    position: Optional[str] = Field(None, max_length=255)
    email: Optional[str] = Field(None, max_length=255)
    phone: Optional[str] = Field(None, max_length=50)
    mobile: Optional[str] = Field(None, max_length=50)
    is_primary: bool = False

    @field_validator("name", "position", "email", "phone", "mobile", mode="before")
    @classmethod
    def _bersih(cls, v):
        return _teks_atau_none(v)

    @field_validator("email")
    @classmethod
    def _email(cls, v):
        if v and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", v):
            raise ValueError("Invalid email format")
        return v

    def kosong(self) -> bool:
        return not any((self.name, self.position, self.email, self.phone, self.mobile))

    @model_validator(mode="after")
    def _nama_wajib(self):
        if not self.kosong() and not self.name:
            raise ValueError("Nama kontak wajib diisi")
        return self


def _pecah_tax_address(data):
    """Sebelum validasi: tax_address objek -> tax_address (jalan) + tax_city/province/postal_code (kolom terstruktur).
    Kolom datar yang dikirim eksplisit menang. tax_address null = tidak ada perubahan (FE selalu mengirim null bila kosong)."""
    if not isinstance(data, dict):
        return data
    if "tax_address" not in data:
        return data
    ta = data["tax_address"]
    if ta is None:
        data = dict(data)
        data.pop("tax_address")
        return data
    if isinstance(ta, dict):
        alamat = AlamatTerstruktur.model_validate(ta)
        data = dict(data)
        data["tax_address"] = alamat.street
        for kunci, nilai in (("tax_city", alamat.city), ("tax_province", alamat.province),
                             ("tax_postal_code", alamat.postal_code)):
            data.setdefault(kunci, nilai)
    return data


def _nik_sah(v):
    v = _teks_atau_none(v)
    if v is None:
        return None
    digit = re.sub(r"\D", "", v)
    if len(digit) != 16:
        raise ValueError(NIK_16_DIGIT)
    return digit


class FieldFormPemasok(IdentitasPajakDasar):
    """Field form pemasok yang dulu dibuang: dipakai sebagai induk Create & Update (nitku/negara dari IdentitasPajakDasar, V420)."""

    nik: Optional[str] = Field(None, description="NIK 16 digit (hanya vendor_type ORANG_PRIBADI)")
    mobile_phone: Optional[str] = Field(
        None, max_length=50, validation_alias=AliasChoices("mobile_phone", "mobile"), description="Alias: mobile"
    )
    default_tax_code: Optional[str] = Field(None, max_length=20)
    default_pph_type: Optional[str] = Field(None, max_length=20)
    default_pph_rate: Optional[Decimal] = Field(None, ge=0, le=100)
    tax_address: Optional[str] = None  # jalan; objek FE dipecah oleh _pecah_tax_address
    shipping_address: Optional[AlamatTerstruktur] = None
    contacts: Optional[List[KontakPemasok]] = Field(None, max_length=100)

    @model_validator(mode="before")
    @classmethod
    def _tax_address_objek(cls, data):
        return _pecah_tax_address(data)

    @field_validator("nik", mode="before")
    @classmethod
    def _nik(cls, v):
        return _nik_sah(v)

    @field_validator("default_tax_code", "default_pph_type", mode="before")
    @classmethod
    def _kode_bersih(cls, v):
        return _teks_atau_none(v)

    @model_validator(mode="after")
    def _nik_hanya_orang_pribadi(self):
        tipe = getattr(self, "vendor_type", None)
        if self.nik and "vendor_type" in self.model_fields_set and tipe != "ORANG_PRIBADI":
            raise ValueError("NIK hanya untuk pemasok Orang Pribadi")
        return self



class CreateVendorRequest(FieldFormPemasok):
    """Request body for creating a vendor."""

    code: Optional[str] = Field(
        None, max_length=50, description="Vendor code (e.g., PBF-001)"
    )
    name: str = Field(
        ..., min_length=1, max_length=255, description="Vendor name (required)"
    )
    contact_person: Optional[str] = Field(None, max_length=255)
    phone: Optional[str] = Field(None, max_length=50)
    email: Optional[str] = Field(None, max_length=255)
    address: Optional[str] = None
    city: Optional[str] = Field(None, max_length=100)
    province: Optional[str] = Field(None, max_length=100)
    postal_code: Optional[str] = Field(None, max_length=20)
    tax_id: Optional[str] = Field(None, max_length=50, description="NPWP")
    payment_terms_days: int = Field(
        30, ge=0, le=365, description="Default payment terms in days"
    )
    credit_limit: Optional[int] = Field(
        None, ge=0, description="Credit limit in Rupiah"
    )
    notes: Optional[str] = None

    # Extended fields (m1, m2, m3, m4)
    account_number: Optional[str] = Field(
        None, max_length=50, description="Vendor internal account number"
    )
    vendor_type: Optional[Literal["BADAN", "ORANG_PRIBADI", "LUAR_NEGERI"]] = Field(
        "BADAN", description="Business type"
    )

    # Bank details (m3)
    bank_name: Optional[str] = Field(None, max_length=100)
    bank_account_number: Optional[str] = Field(None, max_length=50)
    bank_account_holder: Optional[str] = Field(None, max_length=255)

    # Tax address (m4) - separate from main address
    tax_city: Optional[str] = Field(None, max_length=100)
    tax_province: Optional[str] = Field(None, max_length=100)
    tax_postal_code: Optional[str] = Field(None, max_length=20)

    # Opening balance (m1)
    opening_balance: Optional[int] = Field(
        None, ge=0, description="Opening balance in Rupiah"
    )
    opening_balance_date: Optional[str] = Field(
        None, description="Opening balance date YYYY-MM-DD"
    )
    
    # Extended fields (QB/Xero/Zoho aligned)
    company_name: Optional[str] = Field(None, max_length=255, description="Legal company name")
    display_name: Optional[str] = Field(None, max_length=255, description="Display name for invoices")
    website: Optional[str] = Field(None, max_length=255, description="Website URL")
    is_pkp: Optional[bool] = Field(False, description="Is PKP (Pengusaha Kena Pajak)")
    currency: Optional[str] = Field("IDR", max_length=3, description="Default currency code")

    @field_validator("email")
    @classmethod
    def validate_email(cls, v):
        if v and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", v):
            raise ValueError("Invalid email format")
        return v

    @model_validator(mode="after")
    def _nik_create_butuh_orang_pribadi(self):
        if self.nik and self.vendor_type != "ORANG_PRIBADI":
            raise ValueError("NIK hanya untuk pemasok Orang Pribadi")
        return self

    @field_validator("name")
    @classmethod
    def validate_name(cls, v):
        if not v or not v.strip():
            raise ValueError("Vendor name is required")
        return v.strip()


class UpdateVendorRequest(FieldFormPemasok):
    """Request body for updating a vendor (partial update)."""

    code: Optional[str] = Field(None, max_length=50)
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    contact_person: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    province: Optional[str] = None
    postal_code: Optional[str] = None
    tax_id: Optional[str] = None
    payment_terms_days: Optional[int] = Field(None, ge=0, le=365)
    credit_limit: Optional[int] = None
    notes: Optional[str] = None
    is_active: Optional[bool] = None

    # Extended fields
    account_number: Optional[str] = None
    vendor_type: Optional[Literal["BADAN", "ORANG_PRIBADI", "LUAR_NEGERI"]] = None

    # Bank details
    bank_name: Optional[str] = None
    bank_account_number: Optional[str] = None
    bank_account_holder: Optional[str] = None

    # Tax address
    tax_city: Optional[str] = None
    tax_province: Optional[str] = None
    tax_postal_code: Optional[str] = None

    # Opening balance
    opening_balance: Optional[int] = None
    opening_balance_date: Optional[str] = None
    
    # Extended fields (QB/Xero/Zoho aligned)
    company_name: Optional[str] = None
    display_name: Optional[str] = None
    website: Optional[str] = None
    is_pkp: Optional[bool] = None
    currency: Optional[str] = None

    @field_validator("email")
    @classmethod
    def validate_email(cls, v):
        if v and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", v):
            raise ValueError("Invalid email format")
        return v


# =============================================================================
# RESPONSE MODELS - List Item
# =============================================================================


class VendorListItem(BaseModel):
    """Vendor item for list responses."""

    id: str
    code: Optional[str] = None
    name: str
    company_name: Optional[str] = None
    display_name: Optional[str] = None
    contact_person: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    payment_terms_days: int
    is_active: bool
    created_at: str
    ap_balance: Optional[float] = 0


    """Vendor item for list responses."""

    id: str
    code: Optional[str] = None
    name: str
    contact_person: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    payment_terms_days: int
    is_active: bool
    created_at: str


class VendorListResponse(BaseModel):
    """Response for list vendors endpoint."""

    items: List[VendorListItem]
    total: int
    has_more: bool


# =============================================================================
# RESPONSE MODELS - Detail
# =============================================================================


class VendorDetail(BaseModel):
    """Full vendor detail."""

    id: str
    code: Optional[str] = None
    name: str
    contact_person: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    province: Optional[str] = None
    postal_code: Optional[str] = None
    tax_id: Optional[str] = None
    payment_terms_days: int
    credit_limit: Optional[int] = None
    notes: Optional[str] = None

    # Extended fields
    account_number: Optional[str] = None
    vendor_type: Optional[str] = None

    # Bank details
    bank_name: Optional[str] = None
    bank_account_number: Optional[str] = None
    bank_account_holder: Optional[str] = None

    # Tax address
    tax_address: Optional[AlamatTerstruktur] = None

    # Opening balance
    opening_balance: Optional[int] = None
    opening_balance_date: Optional[str] = None
    
    # Extended fields
    company_name: Optional[str] = None
    display_name: Optional[str] = None
    mobile_phone: Optional[str] = None
    mobile: Optional[str] = None  # alias baca untuk FE (useVendorForm membaca v.mobile)
    nik: Optional[str] = None
    nitku: Optional[str] = None
    negara: Optional[str] = None
    default_tax_code: Optional[str] = None
    default_pph_type: Optional[str] = None
    default_pph_rate: Optional[float] = None
    shipping_address: Optional[AlamatTerstruktur] = None
    contacts: List[Dict[str, Any]] = []
    website: Optional[str] = None
    is_pkp: Optional[bool] = None
    currency: Optional[str] = None

    # Transaction check
    has_transactions: Optional[bool] = None

    # Pure Ledger: AP balance from journals
    ap_balance: Optional[float] = 0

    is_active: bool
    created_at: str
    updated_at: str


class VendorDetailResponse(BaseModel):
    """Response for get vendor detail endpoint."""

    success: bool = True
    data: VendorDetail


# =============================================================================
# RESPONSE MODELS - Generic
# =============================================================================


class VendorResponse(BaseModel):
    """Generic vendor operation response (create, update, delete)."""

    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None


# =============================================================================
# AUTOCOMPLETE RESPONSE
# =============================================================================


class VendorAutocompleteItem(BaseModel):
    """Vendor item for autocomplete responses."""

    id: str
    name: str
    code: Optional[str] = None
    phone: Optional[str] = None


class VendorAutocompleteResponse(BaseModel):
    """Response for vendor autocomplete endpoint."""

    items: List[VendorAutocompleteItem]


# =============================================================================
# BALANCE RESPONSE (AP Balance)
# =============================================================================


class VendorBalanceData(BaseModel):
    """Vendor AP balance data."""

    vendor_id: str
    vendor_name: str
    total_balance: int  # Outstanding amount in IDR
    unpaid_bills: int  # Count of unpaid bills
    partial_bills: int  # Count of partially paid bills
    overdue_bills: int  # Count of overdue bills
    overdue_amount: int  # Total overdue amount
    total_billed: int  # Total amount billed (all time)
    total_paid: int  # Total amount paid (all time)


class VendorBalanceResponse(BaseModel):
    """Response for vendor balance endpoint (AP balance)."""

    success: bool = True
    data: VendorBalanceData


# =============================================================================
# DUPLICATE CHECK RESPONSE
# =============================================================================


class VendorDuplicateItem(BaseModel):
    """Vendor match item for duplicate check."""

    id: str
    name: str
    company: Optional[str] = None
    npwp: Optional[str] = None


class VendorDuplicateCheckResponse(BaseModel):
    """Response for vendor duplicate check endpoint."""

    byName: List[VendorDuplicateItem] = Field(default_factory=list)
    byNpwp: List[VendorDuplicateItem] = Field(default_factory=list)


# =============================================================================
# MERGE VENDOR MODELS
# =============================================================================


class MergeVendorRequest(BaseModel):
    """Request body for merging vendors."""
    
    source_vendor_id: str = Field(
        ..., description="ID of the vendor to merge FROM (will be deactivated)"
    )
    target_vendor_id: str = Field(
        ..., description="ID of the vendor to merge INTO (will receive all records)"
    )




class MergeVendorResponse(BaseModel):
    """Response for merge vendor endpoint."""
    
    success: bool = True
    message: str
    data: Optional[Dict[str, Any]] = None


# =============================================================================
# VENDOR ACTIVITY MODELS
# =============================================================================


class VendorActivity(BaseModel):
    id: str
    type: str
    description: str
    actor_name: Optional[str] = None
    timestamp: str
    details: Optional[str] = None
    document_number: Optional[str] = None
    total: Optional[float] = None
    field_name: Optional[str] = None
    old_value: Optional[str] = None
    new_value: Optional[str] = None


class VendorActivityResponse(BaseModel):
    success: bool
    activities: List[VendorActivity]
    total: int
    has_more: bool
