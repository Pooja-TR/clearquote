from typing import List, Optional
from pydantic import BaseModel, Field


class Line(BaseModel):
    rfx_item_id: Optional[int] = Field(None, description="ID of the matching RFx line item, or null if no confident match")
    vendor_description: str = Field(description="Item description exactly as the vendor wrote it")
    price: Optional[float] = Field(None, description="Price exactly as written, as a number. Null if not readable or not stated")
    currency: Optional[str] = Field(None, description="Currency exactly as written (INR, Rs, USD, $). Null if not stated")
    price_unit: Optional[str] = Field(None, description="Unit the price refers to, exactly as written (per box, per 100 pcs, /kg, Nos, Bundle/25). Null if not stated")
    source_ref: str = Field(description="Where it came from: Excel 'Sheet!Cell', docx 'T1R5' or 'P3', email 'L7', PDF 'page N', photo 'row N'")
    source_snippet: str = Field(description="Verbatim text of the row or sentence this price came from")
    confidence: float = Field(description="0 to 1. Use below 0.8 if any digit, unit or match is uncertain")
    applicability_uncertain: bool = Field(False, description="True if the vendor's statement could apply to more than one RFx line")
    box_2d: Optional[List[int]] = Field(None, description="Photos only: bounding box of the price cell as [ymin, xmin, ymax, xmax] on a 0-1000 scale")
    notes: Optional[str] = Field(None, description="Anything the buyer should know about this value")


class Questionnaire(BaseModel):
    iso_9001: str = Field("Not stated", description="Yes, No, or Not stated")
    burst_report: str = Field("Not stated", description="Yes only if a burst test report is provided or attached now. No if promised later or refused. Not stated otherwise")
    lead_time_days: Optional[int] = None
    fsc: str = Field("Not stated", description="Yes, No, or Not stated")
    accepts_60_day_payment: str = Field("Not stated", description="Yes, No, or Not stated")
    evidence: str = Field("", description="Short verbatim quotes supporting these answers")


class Terms(BaseModel):
    freight: Optional[str] = Field(None, description="Freight wording verbatim")
    freight_included: Optional[bool] = None
    freight_amount_stated: bool = False
    payment_terms: Optional[str] = None
    validity: Optional[str] = None
    gst: Optional[str] = None


class Discount(BaseModel):
    percent: float
    applies_to: str = Field(description="What the discount applies to, verbatim")
    source_ref: str
    source_snippet: str


class Doc(BaseModel):
    vendor_name: str
    lines: List[Line]
    questionnaire: Questionnaire
    terms: Terms
    discounts: List[Discount] = []
    baseline_reference: bool = Field(False, description="True if the vendor says some rates are the same as last year / previous / existing")
    baseline_text: Optional[str] = None
    open_questions: List[str] = Field([], description="Anything unclear that the buyer should ask the vendor")


class DraftItem(BaseModel):
    description: str
    unit: str
    quantity: int


class Draft(BaseModel):
    items: List[DraftItem]
    questionnaire: List[str]
    terms: List[str]
