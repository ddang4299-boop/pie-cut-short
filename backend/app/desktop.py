"""Desktop mode (PieCut.pyw): the backend also serves the built UI, so no Vite dev server is needed.

`/api/...` goes to the API with the prefix stripped (exactly what the Vite dev proxy does), and every
other path is the single-page app: a file from frontend/dist when it exists, otherwise index.html.
Enabled by SERVE_FRONTEND=1 (the launcher sets it); tests and `uvicorn --reload` don't use it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Awaitable, Callable

from starlette.responses import FileResponse, PlainTextResponse

Scope = dict[str, Any]
ASGIApp = Callable[[Scope, Any, Any], Awaitable[None]]


class FrontendMiddleware:
    def __init__(self, app: ASGIApp, dist: Path):
        self.app = app
        self.dist = dist.resolve()

    async def __call__(self, scope: Scope, receive: Any, send: Any) -> None:
        if scope["type"] != "http":  # lifespan, websockets
            await self.app(scope, receive, send)
            return
        path: str = scope["path"]
        if path == "/api" or path.startswith("/api/"):
            stripped = path[4:] or "/"
            await self.app({**scope, "path": stripped, "raw_path": stripped.encode()}, receive, send)
            return
        await self._static(path)(scope, receive, send)

    def _static(self, path: str) -> Any:
        index = self.dist / "index.html"
        if not index.exists():
            return PlainTextResponse("PieCut UI is not built yet: run `npm run build` in frontend/.", status_code=503)
        rel = path.lstrip("/")
        if rel:
            target = (self.dist / rel).resolve()
            if target.is_file() and self.dist in target.parents:
                # hashed build assets never change; other files (favicon) are cheap to revalidate
                immutable = target.parent.name == "assets"
                return FileResponse(target, headers={"Cache-Control": "max-age=31536000, immutable" if immutable else "no-cache"})
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
