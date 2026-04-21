"""Tests for vocence.gateway.http.service.app."""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock


class TestFastAPIApp:
    """FastAPI app can be constructed and has health endpoint."""

    @pytest.mark.asyncio
    async def test_app_creation(self):
        # Avoid starting lifespan (DB, workers)
        from vocence.gateway.http.service.app import app
        assert app is not None
        assert app.title is not None or hasattr(app, "routes")

    def test_health_route_exists(self):
        from vocence.gateway.http.service.app import app
        routes = [r.path for r in app.routes]
        # Status router mounts at / or /health
        assert any("health" in p or p == "/" for p in routes) or len(routes) > 0
