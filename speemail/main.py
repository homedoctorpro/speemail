import logging
import socket
import webbrowser
from threading import Timer

import uvicorn

# On Fly.io (and some other cloud hosts), IPv6 connections to external services
# can hang indefinitely. Force IPv4 to avoid this.
_orig_getaddrinfo = socket.getaddrinfo

def _ipv4_preferred(host, port, family=0, type=0, proto=0, flags=0):
    results = _orig_getaddrinfo(host, port, family, type, proto, flags)
    ipv4 = [r for r in results if r[0] == socket.AF_INET]
    return ipv4 if ipv4 else results

socket.getaddrinfo = _ipv4_preferred

from speemail.config import settings  # noqa: E402

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
        log_level="info",
    )


if __name__ == "__main__":
    run()
