from __future__ import annotations

from pydantic import BaseModel, Field


class ProcessRequest(BaseModel):
    payload: str
    payload_id: str = Field(min_length=1)


class ProcessResponse(BaseModel):
    result: str


class HealthResponse(BaseModel):
    status: str
    redis: str
    storage: dict = {}
    load_state: str = "NORMAL"


class InspectRequest(BaseModel):
    payload: str
    mode: str = "partial"
    consumer_id: str | None = None


class CandidateOut(BaseModel):
    type: str
    value: str
    start: int
    end: int
    detector: str
    confidence: float


class DecisionOut(BaseModel):
    type: str
    value: str
    start: int
    end: int
    decision: str
    confidence: float
    reasons: list[str]


class MappingOut(BaseModel):
    entity_id: str
    type: str
    original: str
    token: str
    synthetic: str
    partial: str
    grammatical_case: str | None = None
    gender: str | None = None


class InspectResponse(BaseModel):
    mode: str
    candidates: list[CandidateOut]
    decisions: list[DecisionOut]
    masked_text: str
    mappings: list[MappingOut]
    demasked_text: str
    round_trip_ok: bool | None
