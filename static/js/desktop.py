
"""ARES HEALTH - Lanzador de escritorio."""

import threading
import time
import urllib.request

import webview
from werkzeug.serving import make_server

from app import app

HOST = "127.0.0.1"
PORT = 5000
URL = f"http://{HOST}:{PORT}"


def start_server():
    server = make_server(HOST, PORT, app)
    server.serve_forever()


def wait_for_server():
    for _ in range(50):
        try:
            with urllib.request.urlopen(
                f"{URL}/api/health", timeout=1
            ) as response:
                if response.status == 200:
                    return True
        except Exception:
            time.sleep(0.1)

    return False


def main():
    server_thread = threading.Thread(
        target=start_server,
        daemon=True
    )
    server_thread.start()

    if not wait_for_server():
        raise RuntimeError(
            "No se pudo iniciar el servidor local."
        )

    webview.create_window(
        title="ARES HEALTH 2.0 | Mission Control",
        url=URL,
        width=1280,
        height=800,
        min_size=(950, 650)
    )

    webview.start()


if __name__ == "__main__":
    main()
