"""The demo seeding script builds its upload request by hand (it may use only Python's
standard library on the server): check that FastAPI reads it like any upload."""

from fastapi import FastAPI, UploadFile
from httpx import ASGITransport, AsyncClient

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
