import importlib.util
import sys
from unittest.mock import MagicMock
from dbus_mqtt_battery import DvccController

# Mock the modules and submodules
sys.modules['dbus'] = MagicMock()
sys.modules['dbus.mainloop.glib'] = MagicMock()
sys.modules['vedbus'] = MagicMock()
sys.modules['paho.mqtt'] = MagicMock()
sys.modules['paho.mqtt.client'] = MagicMock()
sys.modules['paho.mqtt.enums'] = MagicMock()
sys.modules['gi'] = MagicMock()
sys.modules['gi.repository'] = MagicMock()
sys.modules['gi.repository.GLib'] = MagicMock()

spec = importlib.util.spec_from_file_location("dbus_mqtt_battery", "./dbus-mqtt-battery.py")
dbus_mqtt_battery = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = dbus_mqtt_battery
spec.loader.exec_module(dbus_mqtt_battery)

controller = DvccController(cell_count=4, bms_count=1)

data = {
    'max_cell': 3.4,
    'min_cell': 3.1,
    'max_cell_id': 1,
    'min_cell_id': 1,
    'max_temp': 25.0,
    'min_temp': 20.0,
    'soc': 50.0,
    'allow_charge': False,  # BUS says don't charge
    'allow_discharge': True
}

print("Data:", data)
result = controller.calculate(data)
print("Result:", result)
