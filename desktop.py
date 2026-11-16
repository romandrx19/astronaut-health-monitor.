"""Ventana de escritorio sobre la misma aplicación y base SQLite de la web."""
from threading import Thread
import logging
from logging.handlers import RotatingFileHandler

from werkzeug.serving import make_server
from runtime_paths import AlreadyRunning, ProcessLock, configure_runtime


def run_window(app, webview, server_factory=make_server):
    """Comparte Flask y garantiza cierre aun si falla la creación de la ventana."""
    server = server_factory("127.0.0.1", 0, app, threaded=True)
    thread = Thread(target=server.serve_forever, name="ares-local-http", daemon=True)
    try:
        thread.start()
        screens = getattr(webview, "screens", [])
        width = max(480, min(1280, screens[0].width - 40)) if screens else 1280
        height = max(400, min(900, screens[0].height - 80)) if screens else 900
        webview.create_window("ARES HEALTH 2.0 · Prototipo educativo",
                              f"http://127.0.0.1:{server.server_port}",
                              width=width, height=height, min_size=(480, min(640, height)))
        webview.start(debug=False)
    finally:
        if thread.is_alive():
            server.shutdown()
        server.server_close()
        if thread.ident is not None:
            thread.join(timeout=5)
        if thread.is_alive():
            raise RuntimeError("El servidor local no terminó correctamente")


def main():
    target = configure_runtime()
    log_directory = target.parent / "logs"
    log_directory.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, handlers=[RotatingFileHandler(
        log_directory / "ares-health.log", maxBytes=2_000_000, backupCount=2, encoding="utf-8")])
    try:
        import webview
    except ImportError:
        raise SystemExit("Instala el modo escritorio: python -m pip install -r requirements-desktop.txt")
    try:
        with ProcessLock(target.with_suffix(".desktop.lock")):
            from app import app
            app.debug = False
            run_window(app, webview)
    except AlreadyRunning as error:
        logging.info(str(error))
        raise SystemExit(str(error))
    except Exception:
        logging.exception("No se pudo iniciar ARES HEALTH")
        raise SystemExit(f"No se pudo iniciar ARES HEALTH. Consulta {log_directory / 'ares-health.log'}") from None


if __name__ == "__main__":
    main()
