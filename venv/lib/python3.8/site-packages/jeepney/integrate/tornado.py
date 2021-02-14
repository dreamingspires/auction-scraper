"""Deprecated: use jeepney.io.tornado instead"""
from jeepney.io.tornado import *

async def connect_and_authenticate(bus='SESSION'):
    conn = await open_dbus_connection(bus)
    return DBusRouter(conn)


if __name__ == '__main__':
    rtr = IOLoop.current().run_sync(connect_and_authenticate)
    print("Unique name is:", rtr.unique_name)
