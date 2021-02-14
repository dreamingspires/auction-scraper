from concurrent.futures import Future
from contextlib import contextmanager
import functools
from itertools import count
import os
from selectors import DefaultSelector, EVENT_READ
import socket
from queue import Queue, Full as QueueFull
from threading import Lock, Thread
import time
from typing import Optional

from jeepney import HeaderFields, Message, MessageType, Parser
from jeepney.auth import AuthenticationError, BEGIN, make_auth_external, SASLParser
from jeepney.bus import get_bus
from jeepney.bus_messages import message_bus
from jeepney.wrappers import ProxyBase, unwrap_msg
from .blocking import unwrap_read
from .common import (
    MessageFilters, FilterHandle, ReplyMatcher, RouterClosed, check_replyable,
)


class ReceiveStopped(Exception):
    pass


class DBusConnection:
    def __init__(self, sock):
        self.sock = sock
        self.parser = Parser()
        self.outgoing_serial = count(start=1)
        self.selector = DefaultSelector()
        self.select_key = self.selector.register(sock, EVENT_READ)
        self._stop_r, self._stop_w = os.pipe()
        self.stop_key = self.selector.register(self._stop_r, EVENT_READ)
        self.send_lock = Lock()
        self.rcv_lock = Lock()
        self.unique_name = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    def send(self, message: Message, serial=None):
        """Serialise and send a :class:`~.Message` object"""
        if serial is None:
            serial = next(self.outgoing_serial)
        data = message.serialise(serial=serial)
        with self.send_lock:
            self.sock.sendall(data)

    def receive(self, *, timeout=None) -> Message:
        """Return the next available message from the connection

        If the data is ready, this will return immediately, even if timeout<=0.
        Otherwise, it will wait for up to timeout seconds, or indefinitely if
        timeout is None. If no message comes in time, it raises TimeoutError.

        If the connection is closed from another thread, this will raise
        ReceiveStopped.
        """
        if timeout is not None:
            deadline = time.monotonic() + timeout
        else:
            deadline = None

        with self.rcv_lock:
            while True:
                msg = self.parser.get_next_message()
                if msg is not None:
                    return msg

                if deadline is not None:
                    timeout = deadline - time.monotonic()

                b = self._read_some_data(timeout)
                self.parser.add_data(b)

    def _read_some_data(self, timeout=None):
        # Wait for data or a signal on the stop pipe
        for key, ev in self.selector.select(timeout):
            if key == self.select_key:
                return unwrap_read(self.sock.recv(4096))
            elif key == self.stop_key:
                raise ReceiveStopped("DBus receive stopped from another thread")

        raise TimeoutError

    def interrupt(self):
        """Make any threads waiting for a message raise ReceiveStopped"""
        os.write(self._stop_w, b'a')

    def reset_interrupt(self):
        """Allow calls to .receive() again after .interrupt()

        To avoid race conditions, you should typically wait for threads to
        respond (e.g. by joining them) between interrupting and resetting.
        """
        # Clear any data on the stop pipe
        while (self.stop_key, EVENT_READ) in self.selector.select(timeout=0):
            os.read(self._stop_r, 1024)

    def close(self):
        """Close the connection"""
        self.interrupt()
        self.selector.close()
        self.sock.close()


def open_dbus_connection(bus='SESSION'):
    """Open a plain D-Bus connection

    :return: :class:`DBusConnection`
    """
    bus_addr = get_bus(bus)
    sock = socket.socket(family=socket.AF_UNIX)
    sock.connect(bus_addr)
    sock.sendall(b'\0' + make_auth_external())
    auth_parser = SASLParser()
    while not auth_parser.authenticated:
        auth_parser.feed(unwrap_read(sock.recv(1024)))
        if auth_parser.error:
            raise AuthenticationError(auth_parser.error)

    sock.sendall(BEGIN)

    conn = DBusConnection(sock)
    conn.parser.add_data(auth_parser.buffer)

    with DBusRouter(conn) as router:
        reply_body = Proxy(message_bus, router, timeout=10).Hello()
        conn.unique_name = reply_body[0]

    return conn


class DBusRouter:
    """A client D-Bus connection which can wait for replies.

    This runs a separate receiver thread and dispatches received messages.

    It's possible to wrap a :class:`DBusConnection` in a router temporarily.
    Using the connection directly while it is wrapped is not supported,
    but you can use it again after the router is closed.
    """
    def __init__(self, conn: DBusConnection):
        self.conn = conn
        self._replies = ReplyMatcher()
        self._filters = MessageFilters()
        self._rcv_thread = Thread(target=self._receiver, daemon=True)
        self._rcv_thread.start()

    @property
    def unique_name(self):
        return self.conn.unique_name

    def send(self, message, *, serial=None):
        """Serialise and send a :class:`~.Message` object"""
        self.conn.send(message, serial=serial)

    def send_and_get_reply(self, msg: Message, *, timeout=None) -> Message:
        """Send a method call message, wait for and return a reply"""
        check_replyable(msg)
        if not self._rcv_thread.is_alive():
            raise RouterClosed("This D-Bus router has stopped")

        serial = next(self.conn.outgoing_serial)

        with self._replies.catch(serial, Future()) as reply_fut:
            self.conn.send(msg, serial=serial)
            return reply_fut.result(timeout=timeout)

    def close(self):
        """Close this router

        This does not close the underlying connection.
        """
        self.conn.interrupt()
        self._rcv_thread.join(timeout=10)
        self.conn.reset_interrupt()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    def filter(self, rule, *, queue: Optional[Queue] =None, bufsize=1):
        """Create a filter for incoming messages

        Usage::

            with router.filter(rule) as queue:
                matching_msg = queue.get()

        :param jeepney.MatchRule rule: Catch messages matching this rule
        :param queue.Queue queue: Matched messages will be added to this
        :param int bufsize: If no queue is passed in, create one with this size
        """
        return FilterHandle(self._filters, rule, queue or Queue(maxsize=bufsize))

    # Code to run in receiver thread ------------------------------------

    def _dispatch(self, msg: Message):
        if self._replies.dispatch(msg):
            return

        for filter in self._filters.matches(msg):
            try:
                filter.queue.put_nowait(msg)
            except QueueFull:
                pass

    def _receiver(self):
        try:
            while True:
                msg = self.conn.receive()
                self._dispatch(msg)
        except ReceiveStopped:
            pass
        finally:
            # Send errors to any tasks still waiting for a message.
            self._replies.drop_all()

class Proxy(ProxyBase):
    """A blocking proxy for calling D-Bus methods via a :class:`DBusRouter`.

    You can call methods on the proxy object, such as ``bus_proxy.Hello()``
    to make a method call over D-Bus and wait for a reply. It will either
    return a tuple of returned data, or raise :exc:`.DBusErrorResponse`.
    The methods available are defined by the message generator you wrap.

    You can set a time limit on a call by passing ``_timeout=`` in the method
    call, or set a default when creating the proxy. The ``_timeout`` argument
    is not passed to the message generator.
    All timeouts are in seconds, and :exc:`TimeoutErrror` is raised if it
    expires before a reply arrives.

    :param msggen: A message generator object
    :param ~threading.DBusRouter router: Router to send and receive messages
    :param float timeout: Default seconds to wait for a reply, or None for no limit
    """
    def __init__(self, msggen, router, *, timeout=None):
        super().__init__(msggen)
        self._router = router
        self._timeout = timeout

    def __repr__(self):
        extra = '' if (self._timeout is None) else f', timeout={self._timeout}'
        return f"Proxy({self._msggen}, {self._router}{extra})"

    def _method_call(self, make_msg):
        @functools.wraps(make_msg)
        def inner(*args, **kwargs):
            timeout = kwargs.pop('_timeout', self._timeout)
            msg = make_msg(*args, **kwargs)
            assert msg.header.message_type is MessageType.method_call
            reply = self._router.send_and_get_reply(msg, timeout=timeout)
            return unwrap_msg(reply)

        return inner

@contextmanager
def open_dbus_router(bus='SESSION'):
    """Open a D-Bus 'router' to send and receive messages.

    Use as a context manager::

        with open_dbus_router() as router:
            ...

    On leaving the ``with`` block, the connection will be closed.

    :param str bus: 'SESSION' or 'SYSTEM' or a supported address.
    :return: :class:`DBusRouter`
    """
    with open_dbus_connection(bus=bus) as conn:
        with DBusRouter(conn) as router:
            yield router
