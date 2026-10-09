"""The demo seeding script builds its upload request by hand (it may use only Python's
standard library on the server): check that FastAPI reads it like any upload."""

import json
from pathlib import Path

import pytest
from fastapi import FastAPI, UploadFile
from httpx import ASGITransport, AsyncClient

from deploy import seed_demo
from deploy.seed_demo import multipart


async def test_the_hand_made_upload_is_read_like_any_other() -> None:
    app = FastAPI()

    @app.post("/upload")
    async def upload(file: UploadFile) -> dict[str, str]:
        return {"name": file.filename or "", "text": (await file.read()).decode()}

    body, content_type = multipart({"rules.txt": b"Fees are due by 15 July.\r\n"})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/upload", content=body, headers={"Content-Type": content_type}
        )

    assert response.json() == {"name": "rules.txt", "text": "Fees are due by 15 July.\r\n"}


def test_the_demo_config_is_javascript_with_the_key_and_the_address(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "demo-config.js"
    config.write_text("old content, much longer than the new one\n", encoding="utf-8")
    monkeypatch.setattr(seed_demo, "DEMO_CONFIG", config)

    seed_demo.write_demo_config("rf_pub_abc", "https://ragforge.example.com")

    text = config.read_text(encoding="utf-8")
    assert text.startswith("window.RAGFORGE_DEMO = ")
    assert text.endswith(";\n")
    assert json.loads(text.removeprefix("window.RAGFORGE_DEMO = ").removesuffix(";\n")) == {
        "key": "rf_pub_abc",
        "api": "https://ragforge.example.com",
    }
