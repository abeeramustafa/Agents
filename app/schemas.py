"""JSON shapes for the API and for model output.

Pydantic checks the shape. The catalog gate checks that every category
the model names actually exists in the CSVs.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ProductFilters(BaseModel):
    categories: list[str] = Field(default_factory=list)
    subcategories: list[str] = Field(default_factory=list)
    min_retail_price: float | None = None
    max_retail_price: float | None = None
    min_net_margin_pct: float | None = None
    max_return_rate_pct: float | None = None
    trends: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    sort_priority: str | None = None


class IntakeOutput(BaseModel):
    """Maya's reply. Filters are hints. Code decides what is really in the catalog."""

    in_catalog: bool
    clarifying_question: str | None = None
    filters: ProductFilters = Field(default_factory=ProductFilters)
    requested_item_text: str
    message: str


class ClosestMatch(BaseModel):
    kind: Literal["category", "subcategory"]
    label: str
    score: float


class GateResult(BaseModel):
    status: Literal["ready", "clarify", "out_of_catalog"]
    in_catalog: bool
    message: str
    clarifying_question: str | None = None
    filters: ProductFilters
    product_ids: list[str] = Field(default_factory=list)
    closest_matches: list[ClosestMatch] = Field(default_factory=list)
    note: str | None = None
    matched_label: str | None = None


class LlmPing(BaseModel):
    """Tiny schema for /api/llm-check. It proves the key, not the catalog."""

    ok: bool
    reply: str


class RankedProduct(BaseModel):
    """One row of Omar's ranking. Unknown product ids are dropped after this."""

    model_config = ConfigDict(extra="ignore")

    product_id: str
    rank: int = 1
    rationale: str = ""
    watch_out: str = ""


class ScoutOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ranked: list[RankedProduct] = Field(default_factory=list)
    message: str = ""


class SupplierRiskNote(BaseModel):
    model_config = ConfigDict(extra="ignore")

    supplier_id: str = ""
    risk: str = ""


class SupplierOutput(BaseModel):
    """Lina's comparison. Supplier ids that are not in the offers are dropped later."""

    model_config = ConfigDict(extra="ignore")

    product_id: str = ""
    recommended_supplier_ids: list[str] = Field(default_factory=list)
    comparison_notes: str = ""
    risks: list[SupplierRiskNote] = Field(default_factory=list)
    cheapest_supplier_warning: str = ""
    message: str = ""


class AuditIssue(BaseModel):
    model_config = ConfigDict(extra="ignore")

    where: str = ""
    problem: str = ""


class AuditOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    passed: bool = False
    issues: list[AuditIssue] = Field(default_factory=list)


class PlanItem(BaseModel):
    """One approved product. Supplier ids outside the human's picks are dropped later."""

    model_config = ConfigDict(extra="ignore")

    product_id: str = ""
    supplier_ids: list[str] = Field(default_factory=list)
    why: str = ""


class PlanOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    headline: str = ""
    plan: list[PlanItem] = Field(default_factory=list)
    risks_to_check: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)
    message: str = ""


class MessageIn(BaseModel):
    session_id: str
    text: str


class DecisionIn(BaseModel):
    session_id: str
    product_id: str
    decision: Literal["approved", "rejected", "undo"]


class StageIn(BaseModel):
    session_id: str
    stage: Literal["BRIEF", "PRODUCT_REVIEW", "SUPPLIER_REVIEW", "FINAL_REVIEW"] = "BRIEF"


class SessionIdIn(BaseModel):
    session_id: str


class SupplierSelectIn(BaseModel):
    session_id: str
    product_id: str
    supplier_ids: list[str] = Field(default_factory=list)


class SupplierRetryIn(BaseModel):
    session_id: str
    product_id: str
