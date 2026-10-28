"""Typed contracts between agents. Every LLM output is validated against one of these."""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

# --------------------------------------------------------------------------- triage


class SenderType(str, Enum):
    TAX_OFFICE = "tax_office"
    HEALTH_INSURANCE = "health_insurance"
    PENSION_INSURANCE = "pension_insurance"
    EMPLOYMENT_AGENCY = "job_center_or_employment_agency"
    BROADCAST_FEE = "broadcast_fee"
    CITY_OFFICE = "city_or_registration_office"
    IMMIGRATION_OFFICE = "immigration_office"
    FAMILY_BENEFITS = "family_benefits_office"
    LANDLORD = "landlord_or_property_manager"
    UTILITY = "utility_or_telecom"
    BANK_OR_INSURANCE = "bank_or_insurance"
    COURT_OR_BAILIFF = "court_or_bailiff"
    DEBT_COLLECTION = "debt_collection"
    EMPLOYER = "employer"
    OTHER = "other"


class DocType(str, Enum):
    DECISION = "decision"  # Bescheid: an official decision with legal effect
    PAYMENT_REQUEST = "payment_request"
    REMINDER = "reminder"  # Mahnung / Zahlungserinnerung
    REQUEST_FOR_INFORMATION = "request_for_information"
    INVOICE_OR_STATEMENT = "invoice_or_statement"
    CONTRACT_CHANGE = "contract_change_or_termination"
    APPOINTMENT = "appointment_or_invitation"
    INFORMATION = "information_only"
    OTHER = "other"


class Triage(BaseModel):
    sender_type: SenderType
    sender_name: str | None = Field(None, description="Organisation name as printed on the letter")
    doc_type: DocType
    language: str = Field("de", description="ISO code of the letter's language")
    is_official_decision: bool = Field(
        description="True for an administrative act (Bescheid/Verwaltungsakt), usually with a Rechtsbehelfsbelehrung"
    )
    needs_response: bool = Field(
        description="True if the recipient must send something back (form, documents, statement). Paying alone is not a response."
    )
    confidence: float = Field(ge=0, le=1)
    reasoning: str = Field(description="One or two sentences")


# --------------------------------------------------------------------------- explanation


class GlossaryTerm(BaseModel):
    german: str
    english: str
    note: str | None = None


class Explanation(BaseModel):
    summary_en: str = Field(description="3-5 plain-English sentences: who wrote, why, and what they want")
    key_points: list[str] = Field(description="Short bullet points with the facts that matter")
    if_ignored: str | None = Field(None, description="What happens if the recipient does nothing, if the letter says so")
    glossary: list[GlossaryTerm] = Field(default_factory=list, description="German terms worth learning, max 6")


# --------------------------------------------------------------------------- extraction


class DeadlineRule(BaseModel):
    kind: Literal["fixed_date", "after_notification", "after_receipt"] = Field(
        description=(
            "fixed_date: an explicit calendar date in the letter. "
            "after_notification: a period that starts when an official decision counts as delivered (Bekanntgabe). "
            "after_receipt: a period that starts when the recipient received a private letter (Erhalt/Zugang)."
        )
    )
    fixed_date: date | None = None
    months: int = 0
    weeks: int = 0
    days: int = 0
    delivery: Literal["post", "electronic", "hand", "unknown"] = "post"


class ExtractedDeadline(BaseModel):
    id: str = Field(description="Short slug, e.g. 'payment' or 'objection'")
    purpose: str = Field(description="What must happen by then, in English")
    kind: Literal["action_required", "payment", "right_to_object", "appointment"]
    rule: DeadlineRule
    quote: str = Field(description="Verbatim sentence from the letter that states this deadline")


class Amount(BaseModel):
    value: float
    currency: str = "EUR"
    purpose: str
    direction: Literal["you_pay", "you_receive", "info"]
    quote: str


class Reference(BaseModel):
    label: str = Field(description="e.g. Steuernummer, Aktenzeichen, Versichertennummer")
    value: str
    quote: str


class Action(BaseModel):
    description: str
    priority: Literal["high", "medium", "low"]
    quote: str | None = None


class Extraction(BaseModel):
    letter_date: date | None = None
    letter_date_quote: str | None = None
    deadlines: list[ExtractedDeadline] = Field(default_factory=list)
    amounts: list[Amount] = Field(default_factory=list)
    references: list[Reference] = Field(default_factory=list)
    actions: list[Action] = Field(default_factory=list)
    has_legal_remedy_notice: bool = Field(
        False, description="True if the letter contains a Rechtsbehelfsbelehrung / Rechtsmittelbelehrung"
    )


# --------------------------------------------------------------------------- deterministic results


class ResolvedDeadline(BaseModel):
    id: str
    purpose: str
    kind: str
    due_date: date | None
    days_left: int | None
    status: Literal["overdue", "due_today", "open", "unknown"]
    basis: Literal["stated", "computed", "estimated", "unknown"]
    steps: list[str]
    quote: str


class Issue(BaseModel):
    severity: Literal["error", "warning"]
    source: Literal["grounding", "critic", "consistency"]
    field: str
    message: str


class CriticReport(BaseModel):
    unsupported_claims: list[str] = Field(
        default_factory=list, description="Statements in the explanation that the letter does not support"
    )
    missing_important_points: list[str] = Field(
        default_factory=list, description="Important content of the letter the explanation leaves out"
    )


class Verification(BaseModel):
    passed: bool
    issues: list[Issue] = Field(default_factory=list)
    quotes_checked: int = 0
    extraction_rounds: int = 1
    repaired: list[str] = Field(default_factory=list, description="Errors found in earlier rounds and sent back to the extractor")


# --------------------------------------------------------------------------- drafting

REPLY_INTENTS: dict[str, str] = {
    "file_objection": "Formally object to an official decision (Einspruch / Widerspruch)",
    "request_extension": "Ask for more time (Fristverlängerung)",
    "request_installments": "Ask to pay in instalments (Ratenzahlung / Stundung)",
    "submit_documents": "Send requested forms, documents or information",
    "ask_for_clarification": "Ask the sender to explain something",
    "dispute_claim": "Dispute a bill or claim from a private sender",
    "confirm_or_acknowledge": "Confirm receipt or acknowledge",
    "cancel_contract": "Cancel a contract (Kündigung)",
}


class DraftOutput(BaseModel):
    intent: str = Field(description="One of the allowed intents")
    subject_de: str = Field(description="Betreff line in German, including reference numbers")
    body_de: str = Field(
        description="German letter body from the salutation up to (not including) the closing 'Mit freundlichen Grüßen'"
    )
    body_en: str = Field(description="Faithful English translation of body_de")
    notes_en: str = Field(description="One or two sentences for the user: what to check or attach before sending")
    alternatives: list[str] = Field(default_factory=list, description="Other intents that could fit")


class ReplyDraft(DraftOutput):
    status: Literal["pending_approval", "approved", "rejected"] = "pending_approval"
    edited_by_user: bool = False
    decided_at: datetime | None = None
    rejection_reason: str | None = None


# --------------------------------------------------------------------------- run state


class TraceEvent(BaseModel):
    agent: str
    model: str
    attempt: int = 1
    duration_ms: int
    input_tokens: int = 0
    output_tokens: int = 0
    tool_calls: list[str] = Field(default_factory=list, description="Tools the model chose to call in this step")
    thought: str | None = Field(None, description="The model's reasoning text for this step, if any")
    note: str | None = None


class RunState(BaseModel):
    run_id: str
    created_at: datetime
    source_name: str
    bundesland: str
    today: date
    received_on: date | None = None
    reply_intent: str | None = None
    status: Literal["running", "awaiting_approval", "done", "failed"] = "running"
    error: str | None = None

    text_redacted: str
    redactions: dict[str, str] = Field(default_factory=dict, description="placeholder -> original; stays on local disk")

    triage: Triage | None = None
    explanation: Explanation | None = None
    extraction: Extraction | None = None
    deadlines: list[ResolvedDeadline] = Field(default_factory=list)
    verification: Verification | None = None
    urgency: Literal["overdue", "urgent", "soon", "relaxed", "none"] = "none"
    draft: ReplyDraft | None = None
    supervisor_notes: str | None = None
    trace: list[TraceEvent] = Field(default_factory=list)

    def public_view(self) -> dict:
        """Everything except the placeholder map, for APIs and UIs."""
        data = self.model_dump(mode="json", exclude={"redactions"})
        data["redaction_count"] = len(self.redactions)
        return data
