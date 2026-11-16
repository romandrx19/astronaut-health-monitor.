"""Controles de origen y host para la demostración HTTP local."""
from ipaddress import ip_address

from flask import abort, jsonify, request
from werkzeug.exceptions import SecurityError


def register_local_security(app):
    @app.errorhandler(SecurityError)
    def untrusted_host(error):
        return jsonify(error="Host no permitido para la demostración local"), 403

    @app.before_request
    def check_local_write():
        if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
            return
        try:
            local = ip_address(request.remote_addr).is_loopback
        except (ValueError, TypeError):
            local = False
        if not local:
            abort(403, description="Las escrituras de esta demostración requieren acceso local")
        origin = request.headers.get("Origin")
        if origin and origin.rstrip("/") != request.host_url.rstrip("/"):
            abort(403, description="Origen de solicitud no permitido")
        if request.headers.get("Sec-Fetch-Site") == "cross-site":
            abort(403, description="Abre la aplicación local para guardar datos")

    @app.errorhandler(403)
    def forbidden(error):
        return jsonify(error=error.description), 403

    @app.errorhandler(500)
    def internal_error(error):
        return jsonify(error="No se pudo completar la operación. Consulta el registro local de la aplicación"), 500

    @app.after_request
    def local_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        if request.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response
