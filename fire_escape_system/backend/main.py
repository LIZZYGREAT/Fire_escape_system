"""Backward-compatible ASGI entry point.

Run with ``uvicorn main:app --reload`` from the backend directory.
"""

from app.main import app, create_app

__all__ = ["app", "create_app"]


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
