from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional

class ProductItem(BaseModel):
    id: Optional[int] = None
    item_no: Optional[str] = None
    code: Optional[str] = None
    mark: Optional[str] = None
    description: Optional[str] = None
    name: Optional[str] = None
    category: Optional[str] = None
    width: Optional[float] = None
    height: Optional[float] = None
    diameter: Optional[float] = None
    length: Optional[float] = None
    thickness: Optional[float] = None
    material: Optional[str] = None
    pressure_class: Optional[str] = None
    quantity: float
    unit: Optional[str] = None
    remark: Optional[str] = None
    
    # Calculation outputs
    calculated_area: Optional[float] = 0.0
    material_cost: Optional[float] = 0.0
    labor_cost: Optional[float] = 0.0
    accessory_cost: Optional[float] = 0.0
    installation_cost: Optional[float] = 0.0
    painting_cost: Optional[float] = 0.0
    insulation_cost: Optional[float] = 0.0
    waste_cost: Optional[float] = 0.0
    subtotal: Optional[float] = 0.0
    profit: Optional[float] = 0.0
    vat: Optional[float] = 0.0
    grand_total: Optional[float] = 0.0
    
    warnings: List[str] = Field(default_factory=list)

class PricingRequest(BaseModel):
    items: List[ProductItem]
    # Coefficients override
    labor_rate_pct: Optional[float] = None
    installation_rate_pct: Optional[float] = None
    accessory_rate_pct: Optional[float] = None
    painting_rate_m2: Optional[float] = None
    insulation_rate_m2: Optional[float] = None
    waste_rate_pct: Optional[float] = None
    transportation_total: Optional[float] = None
    machinery_total: Optional[float] = None
    management_fee_pct: Optional[float] = None
    risk_fee_pct: Optional[float] = None
    profit_pct: Optional[float] = None
    vat_pct: Optional[float] = None

class PricingResponse(BaseModel):
    items: List[ProductItem]
    summary: Dict[str, float] # totals for subtotal, profit, vat, grand_total, labor, installation, accessory, insulation, painting, etc.
    warnings: List[str] = Field(default_factory=list)

class ChatMessage(BaseModel):
    role: str # 'user' or 'assistant'
    content: str

class ChatRequest(BaseModel):
    messages: List[ChatMessage]
    current_items: List[ProductItem] = Field(default_factory=list)
    current_coefficients: Dict[str, Any] = Field(default_factory=dict)

class ChatResponse(BaseModel):
    message: str
    suggested_coefficients: Optional[Dict[str, float]] = None
    warnings: List[str] = Field(default_factory=list)

class RuleUpdate(BaseModel):
    table: str # 'thickness_rules', 'pricing_rules', 'coefficient_rules', 'mark_rules'
    data: Dict[str, Any]
