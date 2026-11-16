"""No hay UUID ni protocolo de datos confirmado para Roadtrip Watch 4 GPS."""
from devices.base import DeviceAdapter, DeviceStatus, IntegrationUnavailable


class RoadtripAdapter(DeviceAdapter):
    def status(self):
        return DeviceStatus("Roadtrip Watch 4 GPS", "integration_unavailable",
                            "Dispositivo no integrado: protocolo de datos pendiente de verificar")

    def read(self, astronaut_id):
        raise IntegrationUnavailable(self.status().message)
