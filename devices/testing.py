"""Adaptador exclusivamente sintético para pruebas locales, nunca un reloj real."""
from devices.base import DeviceAdapter, DeviceStatus
from devices.normalizer import normalize_reading


class DemoAdapter(DeviceAdapter):
    def __init__(self, payload):
        self.payload = payload

    def status(self):
        return DeviceStatus("Adaptador de prueba", "not_configured",
                            "Prueba local: datos de simulación, sin conexión física")

    def read(self, astronaut_id):
        return normalize_reading(self.payload, astronaut_id, source="simulation",
                                 device_id="test:demo-adapter", provenance="adapter_test")
