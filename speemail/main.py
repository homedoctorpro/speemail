import logging
import webbrowser
from threading import Timer

import uvicorn

from speemail.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


def run() -> None:
    port = settings.port
    url = f"http://localhost:{port}"

    print(f"\n  Speemail starting at {url}\n")

    # Open browser after a short delay (gives the server time to start)
    Timer(1.5, lambda: webbrowser.open(url)).start()

    uvicorn.run(
        "speemail.api.app:app",
        host="127.0.0.1",
        port=port,
        reload=False,
        log_level="warning",  # suppress uvicorn's noisy access logs; we have our own
    )


if __name__ == "__main__":
    run()
