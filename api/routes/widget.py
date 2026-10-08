"""The chat widget's JavaScript, for websites:
`<script src="https://<api>/widget.js" data-api-key="rf_pub_..." async></script>`.
"""

import asyncio

from fastapi import APIRouter
from fastapi.responses import FileResponse

from api.dependencies import SettingsDep
from api.errors import not_found

router = APIRouter(tags=["widget"])

# Browsers keep it for 5 minutes, so a new version reaches websites quickly.
CACHE_SECONDS = 5 * 60


@router.get("/widget.js", include_in_schema=False)
async def widget_script(settings: SettingsDep) -> FileResponse:
    if not await asyncio.to_thread(settings.widget_file.is_file):
        raise not_found("The widget file is missing on the server.")
    return FileResponse(
        settings.widget_file,
        media_type="text/javascript",
        headers={
            "Cache-Control": f"public, max-age={CACHE_SECONDS}",
            # Pages with strict isolation (COEP) may load it from our domain.
            "Cross-Origin-Resource-Policy": "cross-origin",
            "X-Content-Type-Options": "nosniff",
        },
    )
