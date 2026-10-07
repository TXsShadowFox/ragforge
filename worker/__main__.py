"""Run the ingestion worker: `python -m worker` (or `make worker`)."""

import asyncio

from shared.config import get_settings
from shared.logging import configure_logging
from worker.runner import run_worker


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    asyncio.run(run_worker(settings, handle_signals=True))


if __name__ == "__main__":
    main()
