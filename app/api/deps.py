from __future__ import annotations

from fastapi import Request

from app.config.consumers import ConsumerRegistry
from app.core.interfaces import Demasker, MappingStore
from app.core.pipeline import PiiPipeline
from app.core.process_service import ProcessService


def get_process_service(request: Request) -> ProcessService:
    return request.app.state.process_service


def get_mapping_store(request: Request) -> MappingStore:
    return request.app.state.mapping_store


def get_pipeline(request: Request) -> PiiPipeline:
    return request.app.state.pipeline


def get_demasker(request: Request) -> Demasker:
    return request.app.state.demasker


def get_consumer_registry(request: Request) -> ConsumerRegistry:
    return request.app.state.consumer_registry
