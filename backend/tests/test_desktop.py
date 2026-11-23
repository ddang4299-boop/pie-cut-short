"""Desktop mode: /api/... reaches the API (prefix stripped), everything else is the built UI."""

from fastapi.testclient import TestClient

from app import main
from app.desktop import FrontendMiddleware


def test_frontend_middleware_routes_api_and_spa(app_env, tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>PieCut</title>", "utf-8")
    (dist / "assets" / "app-123.js").write_text("console.log(1)", "utf-8")
    (dist / "piecut.svg").write_text("<svg/>", "utf-8")
    (tmp_path / "secret.txt").write_text("no", "utf-8")
    main._cached_system_info.cache_clear()
    with TestClient(FrontendMiddleware(main.app, dist)) as c:
        health = c.get("/api/health")
        assert health.status_code == 200 and health.json()["status"] == "ok"
        assert c.get("/api/music").json() == []
        # SPA routes, including ones that share a name with an API route, get the UI
        for path in ("/", "/library", "/editor?clip=clip_1", "/settings", "/sources"):
            r = c.get(path)
            assert r.status_code == 200 and "PieCut" in r.text and r.headers["cache-control"] == "no-cache", path
        asset = c.get("/assets/app-123.js")
        assert asset.text == "console.log(1)" and "immutable" in asset.headers["cache-control"]
        assert c.get("/piecut.svg").text == "<svg/>"
        # no escaping the dist folder
        assert "no" != c.get("/../secret.txt").text and "PieCut" in c.get("/%2e%2e/secret.txt").text


def test_frontend_middleware_without_a_build(app_env, tmp_path):
    with TestClient(FrontendMiddleware(main.app, tmp_path / "missing")) as c:
        r = c.get("/library")
        assert r.status_code == 503 and "npm run build" in r.text
        assert c.get("/api/health").status_code == 200
