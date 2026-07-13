from __future__ import annotations

import io
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import StreamingResponse

from app.models import EditorProject
from app.services import MapCompiler, MapRepository
from app.services.map_compiler import InvalidMapError
from app.services.map_rasterizer import RasterizationError
from app.services.map_repository import MapRepositoryError
from .dependencies import get_compiler, get_repository, project_from_payload


router = APIRouter(tags=["maps"])


def _project(payload: Any) -> EditorProject:
    return project_from_payload(payload)


def _service_error(exc: Exception) -> HTTPException:
    if isinstance(exc, InvalidMapError):
        return HTTPException(
            status_code=422,
            detail={
                "message": str(exc),
                "issues": [
                    value.model_dump(mode="json", by_alias=True)
                    for value in exc.validation.issues
                ],
            },
        )
    if isinstance(exc, MapRepositoryError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, RasterizationError):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/api/maps")
def list_maps(repository: MapRepository = Depends(get_repository)) -> dict[str, Any]:
    return {
        "maps": repository.list_map_ids(),
        "default": repository.default_map_id(),
    }


@router.get("/api/maps/default")
def get_default_map(repository: MapRepository = Depends(get_repository)) -> dict[str, Any]:
    try:
        project = repository.load_project(repository.default_map_id())
    except MapRepositoryError as exc:
        raise _service_error(exc) from exc
    return project.model_dump(mode="json", by_alias=True)


@router.post("/api/maps/import")
def import_map(
    payload: Any = Body(...),
    repository: MapRepository = Depends(get_repository),
) -> dict[str, Any]:
    project = _project(payload)
    try:
        saved = repository.save_project(project)
    except MapRepositoryError as exc:
        raise _service_error(exc) from exc
    return saved.model_dump(mode="json", by_alias=True)


@router.post("/api/maps/compile")
def compile_map(
    payload: Any = Body(...),
    compiler: MapCompiler = Depends(get_compiler),
) -> dict[str, Any]:
    try:
        return compiler.compile(_project(payload))
    except (MapRepositoryError, RasterizationError, InvalidMapError, ValueError) as exc:
        raise _service_error(exc) from exc


@router.post("/api/maps/validate")
def validate_map(
    payload: Any = Body(...),
    compiler: MapCompiler = Depends(get_compiler),
) -> dict[str, Any]:
    try:
        result = compiler.validate(_project(payload))
    except (MapRepositoryError, RasterizationError, InvalidMapError, ValueError) as exc:
        raise _service_error(exc) from exc
    return result.model_dump(mode="json", by_alias=True)


@router.post("/api/placement/candidates")
def placement_candidates(
    payload: Any = Body(...),
    compiler: MapCompiler = Depends(get_compiler),
) -> dict[str, Any]:
    try:
        return compiler.candidates(_project(payload))
    except (MapRepositoryError, RasterizationError, ValueError) as exc:
        raise _service_error(exc) from exc


@router.post("/api/placement/optimize")
def optimize_placement(
    payload: Any = Body(...),
    compiler: MapCompiler = Depends(get_compiler),
) -> dict[str, Any]:
    try:
        return compiler.optimize(_project(payload))
    except (MapRepositoryError, RasterizationError, ValueError) as exc:
        raise _service_error(exc) from exc


@router.post("/api/placement/validate")
def validate_placement(
    payload: Any = Body(...),
    compiler: MapCompiler = Depends(get_compiler),
) -> dict[str, Any]:
    try:
        result = compiler.validate(_project(payload))
    except (MapRepositoryError, RasterizationError, ValueError) as exc:
        raise _service_error(exc) from exc
    return result.model_dump(mode="json", by_alias=True)


@router.get("/api/maps/{map_id}")
def get_map(
    map_id: str,
    repository: MapRepository = Depends(get_repository),
) -> dict[str, Any]:
    try:
        project = repository.load_project(map_id)
    except MapRepositoryError as exc:
        raise _service_error(exc) from exc
    return project.model_dump(mode="json", by_alias=True)


@router.put("/api/maps/{map_id}")
def save_map(
    map_id: str,
    payload: Any = Body(...),
    repository: MapRepository = Depends(get_repository),
) -> dict[str, Any]:
    project = _project(payload)
    if project.map.id != map_id:
        raise HTTPException(
            status_code=409,
            detail="path map_id must match project.map.id",
        )
    try:
        saved = repository.save_project(project)
    except MapRepositoryError as exc:
        raise _service_error(exc) from exc
    return saved.model_dump(mode="json", by_alias=True)


@router.get("/api/maps/{map_id}/report")
def get_map_report(
    map_id: str,
    repository: MapRepository = Depends(get_repository),
    compiler: MapCompiler = Depends(get_compiler),
) -> dict[str, Any]:
    try:
        project = repository.load_project(map_id)
        result = compiler.validate(project)
    except (MapRepositoryError, RasterizationError, ValueError) as exc:
        raise _service_error(exc) from exc
    return result.model_dump(mode="json", by_alias=True)


@router.post("/api/maps/{map_id}/export")
def export_map(
    map_id: str,
    payload: Any = Body(default=None),
    repository: MapRepository = Depends(get_repository),
    compiler: MapCompiler = Depends(get_compiler),
) -> StreamingResponse:
    try:
        project = repository.load_project(map_id) if payload is None else _project(payload)
        if project.map.id != map_id:
            raise HTTPException(
                status_code=409,
                detail="path map_id must match project.map.id",
            )
        archive = compiler.export_zip(project)
    except HTTPException:
        raise
    except (MapRepositoryError, RasterizationError, InvalidMapError, ValueError) as exc:
        raise _service_error(exc) from exc
    filename = f"{map_id}-{project.map.version}.zip"
    return StreamingResponse(
        io.BytesIO(archive),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
