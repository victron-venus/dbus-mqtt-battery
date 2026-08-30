"""Shared test fixtures and mocks."""

import sys
import types

# Stub dbus/gi before any dbus_mqtt_battery import
if "dbus" not in sys.modules:
    _dbus = types.ModuleType("dbus")
    _mainloop = types.ModuleType("dbus.mainloop")
    _glib = types.ModuleType("dbus.mainloop.glib")
    _glib.DBusGMainLoop = lambda **kw: None  # type: ignore[attr-defined]
    sys.modules["dbus"] = _dbus
    sys.modules["dbus.mainloop"] = _mainloop
    sys.modules["dbus.mainloop.glib"] = _glib

if "gi" not in sys.modules:
    _gi = types.ModuleType("gi")
    _repository = types.ModuleType("gi.repository")
    _repository.GLib = types.ModuleType("gi.repository.GLib")  # type: ignore[attr-defined]
    _gi.repository = _repository  # type: ignore[attr-defined]
    sys.modules["gi"] = _gi
    sys.modules["gi.repository"] = _repository
