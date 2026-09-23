from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_consumer_registry, get_demasker, get_pipeline
from app.api.schemas import (
    CandidateOut,
    DecisionOut,
    InspectRequest,
    InspectResponse,
    MappingOut,
)
from app.config.consumers import ConsumerRegistry
from app.core.enums import MaskingMode
from app.core.hashing import sha256_hex
from app.core.interfaces import Demasker
from app.core.pipeline import PiiPipeline
from app.replacement.strategies import get_strategy

router = APIRouter()


@router.post("/inspect", response_model=InspectResponse)
def inspect(
    body: InspectRequest,
    pipeline: PiiPipeline = Depends(get_pipeline),
    demasker: Demasker = Depends(get_demasker),
    consumer_registry: ConsumerRegistry = Depends(get_consumer_registry),
) -> InspectResponse:
    """Debug/demo endpoint for the UI -- NOT the hackathon /process contract.

    Runs mask then immediately demask in one call (no payload_id / Mapping Vault
    state needed) purely so a human can see every pipeline stage at once: what was
    found, why it was decided MASK/KEEP, what it became, and whether restoring it
    gets back the original. Real reversibility across two separate HTTP calls is
    what /process demonstrates.
    """
    try:
        mode = MaskingMode(body.mode)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"invalid mode: {body.mode!r}") from exc

    consumer = consumer_registry.resolve(body.consumer_id)
    if consumer is None:
        raise HTTPException(status_code=404, detail=f"unknown consumer_id: {body.consumer_id!r}")

    candidates, decisions = pipeline.analyze(body.payload, consumer)
    strategy = get_strategy(mode)
    session_seed = sha256_hex(body.payload)
    transform_result = strategy.transform(body.payload, decisions, session_seed)
    demasked_text = demasker.demask(transform_result.text, transform_result.mappings, mode)

    return InspectResponse(
        mode=mode.value,
        candidates=[
            CandidateOut(
                type=c.type.value,
                value=c.value,
                start=c.start,
                end=c.end,
                detector=c.detector,
                confidence=c.detector_confidence,
            )
            for c in candidates
        ],
        decisions=[
            DecisionOut(
                type=d.candidate.type.value,
                value=d.candidate.value,
                start=d.candidate.start,
                end=d.candidate.end,
                decision=d.decision.value,
                confidence=d.confidence,
                reasons=list(d.reasons),
            )
            for d in decisions
        ],
        masked_text=transform_result.text,
        mappings=[
            MappingOut(
                entity_id=m.entity_id,
                type=m.type.value,
                original=m.original,
                token=m.token,
                synthetic=m.synthetic,
                partial=m.partial,
                grammatical_case=m.grammatical_case,
                gender=m.gender,
            )
            for m in transform_result.mappings
        ],
        demasked_text=demasked_text,
        round_trip_ok=None if mode == MaskingMode.PARTIAL else (demasked_text == body.payload),
    )
