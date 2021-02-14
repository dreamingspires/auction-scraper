"""Deprecated: use jeepney.io.blocking instead"""
from jeepney.io.blocking import *


def connect_and_authenticate(bus='SESSION') -> DBusConnection:
    conn = open_dbus_connection(bus)
    conn._unwrap_reply = True  # Backward compatible behaviour
    return conn


if __name__ == '__main__':
    conn = connect_and_authenticate()
    print("Unique name:", conn.unique_name)
