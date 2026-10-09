"""Prepare the public demo: a demo tenant with the sample college documents (eval/corpus)
and a public key for the widget demo site. deploy/setup.sh runs it after each start:

    python3 deploy/seed_demo.py --api https://ragforge.<ip>.sslip.io \\
        --demo-site https://demo.<ip>.sslip.io

The demo account comes from DEMO_EMAIL and DEMO_PASSWORD (setup.sh keeps them in .env).
It uses only Python's standard library, as the server has none of the project's packages.
Safe to run again: the tenant is reused, the same files are duplicates (nothing new), and
the old demo key is revoked before a new one is made. The new key goes into
deploy/demo-config.js, which the demo site loads, so its link never changes.
"""

import argparse
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

CORPUS = Path(__file__).resolve().parents[1] / "eval" / "corpus"
# Served by Caddy as the demo site's demo-config.js (docker-compose.prod.yml mounts it).
DEMO_CONFIG = Path(__file__).resolve().parent / "demo-config.js"
KEY_NAME = "Widget demo"
TENANT_NAME = "Northfield College (demo)"
WAIT_SECONDS = 300


class Api:
    """A tiny client for the RAGForge API (urllib, JSON)."""

    def __init__(self, base_url: str, *, insecure: bool = False) -> None:
        self.base_url = base_url.rstrip("/")
        self.token: str | None = None
        # --insecure: a local test with Caddy's own certificates (not Let's Encrypt).
        self.context = ssl._create_unverified_context() if insecure else None  # noqa: S323

    def call(
        self, method: str, path: str, body: Any = None, *, files: dict[str, bytes] | None = None
    ) -> tuple[int, Any]:
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        data: bytes | None = None
        if files is not None:
            data, content_type = multipart(files)
            headers["Content-Type"] = content_type
        elif body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(  # noqa: S310 (our own https address)
            self.base_url + path, data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=60, context=self.context) as response:  # noqa: S310
                return response.status, _json(response.read())
        except urllib.error.HTTPError as error:
            return error.code, _json(error.read())


def multipart(files: dict[str, bytes]) -> tuple[bytes, str]:
    """A multipart/form-data body with one `file` field per file: (body, content type)."""
    boundary = uuid.uuid4().hex
    parts = [
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode()
        + data
        + b"\r\n"
        for name, data in files.items()
    ]
    return b"".join(parts) + f"--{boundary}--\r\n".encode(), (
        f"multipart/form-data; boundary={boundary}"
    )


def _json(raw: bytes) -> Any:
    try:
        return json.loads(raw) if raw else None
    except json.JSONDecodeError:
        return raw.decode(errors="replace")[:300]


def wait_for_api(api: Api) -> None:
    """Caddy needs a few seconds for its first certificates after a start."""
    deadline = time.monotonic() + 120
    while True:
        try:
            if api.call("GET", "/health")[0] == 200:
                return
        except OSError:
            pass
        if time.monotonic() > deadline:
            sys.exit(f"The API at {api.base_url} does not answer.")
        time.sleep(3)


def log_in(api: Api, email: str, password: str) -> None:
    """Log in as the demo owner; sign the demo tenant up the first time."""
    status, body = api.call("POST", "/v1/auth/login", {"email": email, "password": password})
    if status == 401:
        status, body = api.call(
            "POST",
            "/v1/auth/signup",
            {"tenant_name": TENANT_NAME, "email": email, "password": password},
        )
        _expect(status, body, 201, "sign up")
        status, body = api.call("POST", "/v1/auth/login", {"email": email, "password": password})
    _expect(status, body, 200, "log in")
    api.token = body["access_token"]


def upload_corpus(api: Api) -> int:
    paths = sorted(path for path in CORPUS.iterdir() if path.is_file())
    for path in paths:
        status, body = api.call("POST", "/v1/documents", files={path.name: path.read_bytes()})
        _expect(status, body, (200, 202), f"upload {path.name}")
    return len(paths)


def new_demo_key(api: Api, demo_site: str) -> str:
    """Revoke the old demo key (its value cannot be read again), then make a new one."""
    status, listing = api.call("GET", "/v1/api-keys")
    _expect(status, listing, 200, "list the keys")
    for old in listing["items"]:
        if old["name"] == KEY_NAME and old["revoked_at"] is None:
            status, body = api.call("DELETE", f"/v1/api-keys/{old['id']}")
            _expect(status, body, (200, 204), "revoke the old demo key")
    status, created = api.call(
        "POST",
        "/v1/api-keys",
        {"name": KEY_NAME, "kind": "public", "allowed_origins": [demo_site]},
    )
    _expect(status, created, 201, "create the demo key")
    return str(created["key"])


def write_demo_config(key: str, api_url: str) -> None:
    """The demo site's key and API address. Written in place: the file is bind-mounted, and a
    new file (a new inode) would not reach the container."""
    config = json.dumps({"key": key, "api": api_url})
    with DEMO_CONFIG.open("w", encoding="utf-8", newline="\n") as file:
        file.write(f"window.RAGFORGE_DEMO = {config};\n")


def wait_until_ready(api: Api, count: int) -> None:
    deadline = time.monotonic() + WAIT_SECONDS
    while True:
        status, body = api.call("GET", "/v1/documents?status=ready&limit=100")
        _expect(status, body, 200, "list the documents")
        if len(body["items"]) >= count:
            return
        if time.monotonic() > deadline:
            sys.exit(f"Only {len(body['items'])} of {count} documents are ready.")
        time.sleep(3)


def _expect(status: int, body: Any, expected: int | tuple[int, ...], what: str) -> None:
    if status not in (expected if isinstance(expected, tuple) else (expected,)):
        sys.exit(f"Could not {what}: HTTP {status} {body}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--api", required=True, help="The API's address (https://...)")
    parser.add_argument("--demo-site", required=True, help="The widget demo site's address")
    parser.add_argument("--insecure", action="store_true", help="Do not check certificates")
    args = parser.parse_args()
    email, password = os.environ.get("DEMO_EMAIL"), os.environ.get("DEMO_PASSWORD")
    if not email or not password:
        sys.exit("Set DEMO_EMAIL and DEMO_PASSWORD (deploy/setup.sh keeps them in .env).")

    api = Api(args.api, insecure=args.insecure)
    wait_for_api(api)
    log_in(api, email, password)
    count = upload_corpus(api)
    key = new_demo_key(api, args.demo_site.rstrip("/"))
    write_demo_config(key, api.base_url)
    wait_until_ready(api, count)
    print(f"Demo ready: {count} documents.")
    print(f"Widget demo: {args.demo_site.rstrip('/')}/")
    print(f"Dashboard:   {api.base_url}/")


if __name__ == "__main__":
    main()
