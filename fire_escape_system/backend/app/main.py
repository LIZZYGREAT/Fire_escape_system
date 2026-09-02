from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional, Union

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.health import router as health_router
from app.api.hazards import router as hazards_router
from app.api.maps import router as maps_router
from app.api.websocket import router as websocket_router
from app.services.connections import ConnectionManager
from app.services.hazard_inputs import HazardObservationBuffer
from app.services.map_compiler import MapCompiler
from app.services.map_repository import MapRepository
from app.services.simulation import SimulationRuntime


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("SystemConductor")
BACKEND_ROOT = Path(__file__).resolve().parents[1]


async def _logic_tick_loop(app: FastAPI) -> None:
    logger.info("local simulation tick loop started")
    while True:
        runtime = app.state.simulation_runtime
        interval = runtime.project.simulation.tick_interval_seconds
        await asyncio.sleep(interval / max(0.25, app.state.simulation_speed))
        runtime = app.state.simulation_runtime
        if app.state.is_paused:
            continue
        async with app.state.simulation_lock:
            update = await asyncio.to_thread(runtime.tick_once)
        if update["fire_diff"] or update["environment_diff"] or update["topology_tree"]:
            await app.state.connection_manager.broadcast(update)


def create_app(
    *,
    maps_root: Optional[Union[str, Path]] = None,
    start_simulation: bool = True,
) -> FastAPI:
    repository = MapRepository(maps_root or BACKEND_ROOT / "maps")
    compiler = MapCompiler(repository)
    available_maps = repository.list_map_ids()
    runtime_map_id = "map_1" if "map_1" in available_maps else repository.default_map_id()
    runtime = SimulationRuntime(repository, compiler, map_id=runtime_map_id)
    connection_manager = ConnectionManager()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        task = None
        if start_simulation:
            task = asyncio.create_task(_logic_tick_loop(application))
        try:
            yield
        finally:
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    application = FastAPI(
        title="Smart Fire Escape and Map Editor API",
        version="1.0.0",
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.state.map_repository = repository
    application.state.map_compiler = compiler
    application.state.simulation_runtime = runtime
    application.state.connection_manager = connection_manager
    application.state.hazard_observations = HazardObservationBuffer()
    application.state.simulation_lock = asyncio.Lock()
    application.state.is_paused = False
    application.state.simulation_speed = 2.0
    application.include_router(health_router)
    application.include_router(hazards_router)
    application.include_router(maps_router)
    application.include_router(websocket_router)
    # Register the catch-all static site last so API, docs and WebSocket routes
    # keep precedence.  This also gives the monitor and editor a same-origin
    # deployment, allowing them to derive HTTP/WS endpoints from location.host.
    frontend_root = BACKEND_ROOT.parent / "frontend"
    if frontend_root.exists():
        application.mount(
            "/",
            StaticFiles(directory=str(frontend_root), html=True),
            name="frontend",
        )
    return application


app = create_app()
