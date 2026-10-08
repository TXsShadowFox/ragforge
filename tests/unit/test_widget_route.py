"""GET /widget.js serves the chat widget, and CORS lets browsers on other websites call us."""

from pathlib import Path

from httpx import ASGITransport, AsyncClient, Response

from api.main import create_app
from shared.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[2]
WIDGET_FILE = REPO_ROOT / "widget" / "widget.js"
CUSTOMER_SITE = "https://college.example"


async def _get_widget(settings: Settings) -> Response:
    app = create_app(settings)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.get("/widget.js")


def test_the_default_path_points_at_the_widget_from_the_repo_root() -> None:
    default: Path = Settings.model_fields["widget_file"].default
    assert (REPO_ROOT / default).resolve() == WIDGET_FILE.resolve()
    assert WIDGET_FILE.is_file()


async def test_the_widget_is_served_as_javascript(settings: Settings) -> None:
    response = await _get_widget(settings.model_copy(update={"widget_file": WIDGET_FILE}))

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/javascript")
    assert response.headers["cache-control"] == "public, max-age=300"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "RAGForgeWidget" in response.text


async def test_a_missing_widget_file_gives_404(settings: Settings, tmp_path: Path) -> None:
    response = await _get_widget(settings.model_copy(update={"widget_file": tmp_path / "no.js"}))

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_browsers_on_other_websites_may_send_chat_requests(client: AsyncClient) -> None:
    preflight = await client.options(
        "/v1/chat",
        headers={
            "Origin": CUSTOMER_SITE,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization, content-type",
        },
    )

    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "*"
    assert "POST" in preflight.headers["access-control-allow-methods"]
    assert "authorization" in preflight.headers["access-control-allow-headers"].lower()


async def test_browsers_may_read_the_rate_limit_and_request_id_headers(
    client: AsyncClient,
) -> None:
    response = await client.get("/health", headers={"Origin": CUSTOMER_SITE})

    assert response.headers["access-control-allow-origin"] == "*"
    exposed = response.headers["access-control-expose-headers"].lower()
    for header in ["retry-after", "x-ratelimit-limit", "x-ratelimit-remaining", "x-request-id"]:
        assert header in exposed
    assert "access-control-allow-credentials" not in response.headers  # we use no cookies
