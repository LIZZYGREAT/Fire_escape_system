from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request
from pydantic import ValidationError

from app.models import EditorProject
from app.services import MapCompiler, MapRepository


def get_repository(request: Request) -> MapRepository:
    return request.app.state.map_repository


def get_compiler(request: Request) -> MapCompiler:
    return request.app.state.map_compiler


def project_from_payload(payload: Any) -> EditorProject:
    if isinstance(payload, EditorProject):
        return payload
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="EditorProject JSON object is required")
    candidate = payload
    if isinstance(payload.get("project"), dict):
        candidate = payload["project"]
    elif isinstance(payload.get("data"), dict):
        candidate = payload["data"]
    try:
        return EditorProject.model_validate(candidate)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc

