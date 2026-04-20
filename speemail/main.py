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
    host = "0.0.0.0" if settings.server_mode else "127.0.0.1"

    if settings.server_mode:
        print(f"\n  Speemail (server mode) listening on {host}:{port}\n")
    else:
        url = f"http://localhost:{port}"
        print(f"\n  Speemail starting at {url}\n")
        Timer(1.5, lambda: webbrowser.open(url)).start()

    uvicorn.run(
        "speemail.api.app:app",
        host=host,
        port=port,
        reload=False,
        log_level="warning",
    )


if __name__ == "__main__":
    run()
