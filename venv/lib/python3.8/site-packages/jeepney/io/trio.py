from itertools import count
import logging
from math import inf
from typing import Optional

from outcome import Value, Error
import trio
from trio.abc import Channel

from jeepney.auth import SASLParser, make_auth_external, BEGIN, AuthenticationError
from jeepney.bus import get_bus
from jeepney.low_level import Parser, MessageType, Message
from jeepney.wrappers import ProxyBase, unwrap_msg
from jeepney.bus_messages import message_bus
from .common import (
    MessageFilters, FilterHandle, ReplyMatcher, RouterClosed, check_replyable,
)

log = logging.getLogger(__name__)

__all__ = [
    'open_dbus_connection',
    'open_dbus_router',
    'Proxy',
]


class DBusConnection(Channel):
    """A plain D-Bus connection with no matching of replies.

    This doesn't run any separate tasks: sending and receiving are done in
    the task that calls those methods. It's suitable for implementing servers:
    several worker tasks can receive requests and send replies.
    For a typical client pattern, see :class:`DBusRouter`.

    Implements trio's channel interface for Message objects.
    """
    def __init__(self, socket: trio.SocketStream):
        self.socket = socket
        self.parser = Parser()
        self.outgoing_serial = count(start=1)
        self.unique_name = None
        self.send_lock = trio.Lock()

    async def send(self, message: Message, *, serial=None):
        """Serialise and send a :class:`~.Message` object"""
        async with self.send_lock:
            if serial is None:
                serial = next(self.outgoing_serial)
            await self.socket.send_all(message.serialise(serial))

    async def receive(self) -> Message:
        """Return the next available message from the connection"""
        while True:
            msg = self.parser.get_next_message()
            if msg is not None:
                return msg

            b = await self.socket.receive_some()
            if not b:
                raise trio.EndOfChannel("Socket closed at the other end")
            self.parser.add_data(b)

    async def aclose(self):
        """Close the D-Bus connection"""
        await self.socket.aclose()

    def router(self):
        """Temporarily wrap this connection as a :class:`DBusRouter`

        To be used like::

            async with conn.router() as req:
                reply = await req.send_and_get_reply(msg)

        While the router is running, you shouldn't use :meth:`receive`.
        Once the router is closed, you can use the plain connection again.
        """
        return DBusRouter(self)


async def open_dbus_connection(bus='SESSION') -> DBusConnection:
    """Open a plain D-Bus connection

    :return: :class:`DBusConnection`
    """
    bus_addr = get_bus(bus)
    sock : trio.SocketStream = await trio.open_unix_socket(bus_addr)

    # Authentication flow
    await sock.send_all(b'\0' + make_auth_external())
    auth_parser = SASLParser()
    while not auth_parser.authenticated:
        b = await sock.receive_some()
        auth_parser.feed(b)
        if auth_parser.error:
            raise AuthenticationError(auth_parser.error)

    await sock.send_all(BEGIN)
    # Authentication finished

    conn = DBusConnection(sock)
    conn.parser.add_data(auth_parser.buffer)

    # Say *Hello* to the message bus - this must be the first message, and the
    # reply gives us our unique name.
    async with conn.router() as router:
        reply = await router.send_and_get_reply(message_bus.Hello())
        conn.unique_name = reply.body[0]

    return conn


class TrioFilterHandle(FilterHandle):
    def __init__(self, filters: MessageFilters, rule, send_chn, recv_chn):
        super().__init__(filters, rule, recv_chn)
        self.send_channel = send_chn

    @property
    def receive_channel(self):
        return self.queue

    async def aclose(self):
        self.close()
        await self.send_channel.aclose()

    async def __aenter__(self):
        return self.queue

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.aclose()


class Future:
    """A very simple Future for trio based on `trio.Event`."""
    def __init__(self):
        self._outcome = None
        self._event = trio.Event()

    def set_result(self, result):
        self._outcome = Value(result)
        self._event.set()

    def set_exception(self, exc):
        self._outcome = Error(exc)
        self._event.set()

    async def get(self):
        await self._event.wait()
        return self._outcome.unwrap()


class DBusRouter:
    """A client D-Bus connection which can wait for replies.

    This runs a separate receiver task and dispatches received messages.
    """
    _nursery_mgr = None
    _send_cancel_scope = None
    _rcv_cancel_scope = None
    is_running = False

    def __init__(self, conn: DBusConnection):
        self._conn = conn
        self._to_send, self._to_be_sent = trio.open_memory_channel(0)
        self._replies = ReplyMatcher()
        self._filters = MessageFilters()

    @property
    def unique_name(self):
        return self._conn.unique_name

    async def send(self, message, *, serial=None):
        """Send a message, don't wait for a reply
        """
        if serial is None:
            serial = next(self._conn.outgoing_serial)
        b = message.serialise(serial)
        # Hand off the actual sending to a separate task. This ensures that
        # cancelling the task that makes a D-Bus message can't break the
        # connection by sending an incomplete message.
        await self._to_send.send(b)

    async def send_and_get_reply(self, message) -> Message:
        """Send a method call message and wait for the reply

        Returns the reply message (method return or error message type).
        """
        check_replyable(message)
        if not self.is_running:
            raise RouterClosed("This DBusRouter has stopped")

        serial = next(self._conn.outgoing_serial)

        with self._replies.catch(serial, Future()) as reply_fut:
            await self.send(message, serial=serial)
            return (await reply_fut.get())

    def filter(self, rule, *, channel: Optional[trio.MemorySendChannel]=None, bufsize=1):
        """Create a filter for incoming messages

        Usage::

            async with router.filter(rule) as receive_channel:
                matching_msg = await receive_channel.receive()

            # OR:
            send_chan, recv_chan = trio.open_memory_channel(1)
            async with router.filter(rule, channel=send_chan):
                matching_msg = await recv_chan.receive()

        If the channel fills up,
        The sending end of the channel is closed when leaving the ``async with``
        block, whether or not it was passed in.

        :param jeepney.MatchRule rule: Catch messages matching this rule
        :param trio.MemorySendChannel channel: Send matching messages here
        :param int bufsize: If no channel is passed in, create one with this size
        """
        if channel is None:
            channel, recv_channel = trio.open_memory_channel(bufsize)
        else:
            recv_channel = None
        return TrioFilterHandle(self._filters, rule, channel, recv_channel)

    # Task management -------------------------------------------

    async def start(self, nursery: trio.Nursery):
        if self.is_running:
            raise RuntimeError("DBusRequester tasks are already running")
        self._send_cancel_scope = await nursery.start(self._sender)
        self._rcv_cancel_scope = await nursery.start(self._receiver)

    async def aclose(self):
        """Stop the sender & receiver tasks"""
        # Close the channel to the sender task. Normally the task will be
        # waiting for messages to send, and this is enough to stop it.
        # This can't block, but we shield it so the code below can run if we're
        # in cleanup after cancellation.
        with trio.move_on_after(1) as cleanup_scope:
            cleanup_scope.shield = True
            await self._to_send.aclose()

        # Allow a short grace period for send operations to complete.
        # This should ensure that in normal conditions, we don't send an
        # incomplete message (which breaks the connection), but avoids
        # hanging if sending has somehow got stuck.
        if self._send_cancel_scope is not None:
            self._send_cancel_scope.deadline = trio.current_time() + 1
            self._send_cancel_scope = None

        # It doesn't matter if we receive a partial message - the connection
        # should ensure that whatever is received is fed to the parser.
        if self._rcv_cancel_scope is not None:
            self._rcv_cancel_scope.cancel()
            self._rcv_cancel_scope = None

        # Ensure trio checkpoint
        await trio.sleep(0)

    async def __aenter__(self):
        self._nursery_mgr = trio.open_nursery()
        nursery = await self._nursery_mgr.__aenter__()
        await self.start(nursery)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.aclose()
        await self._nursery_mgr.__aexit__(exc_type, exc_val, exc_tb)
        self._nursery_mgr = None

    # Code to run in sender task --------------------------------------

    async def _sender(self, task_status=trio.TASK_STATUS_IGNORED):
        with trio.CancelScope() as cscope:
            task_status.started(cscope)
            async with self._to_be_sent:
                async for bmsg in self._to_be_sent:
                    async with self._conn.send_lock:
                        await self._conn.socket.send_all(bmsg)

    # Code to run in receiver task ------------------------------------

    def _dispatch(self, msg: Message):
        """Handle one received message"""
        if self._replies.dispatch(msg):
            return

        for filter in self._filters.matches(msg):
            try:
                filter.send_channel.send_nowait(msg)
            except trio.WouldBlock:
                pass

    async def _receiver(self, task_status=trio.TASK_STATUS_IGNORED):
        """Receiver loop - runs in a separate task"""
        with trio.CancelScope() as cscope:
            self.is_running = True
            task_status.started(cscope)
            try:
                while cscope.deadline == inf:
                    msg = await self._conn.receive()
                    self._dispatch(msg)
            finally:
                self.is_running = False
                # Send errors to any tasks still waiting for a message.
                self._replies.drop_all()

                # Closing a memory channel can't block, but it only has an
                # async close method, so we need to shield it from cancellation.
                with trio.move_on_after(3) as cleanup_scope:
                    for filter in self._filters.filters.values():
                        cleanup_scope.shield = True
                        await filter.send_channel.aclose()


class Proxy(ProxyBase):
    """A trio proxy for calling D-Bus methods

    You can call methods on the proxy object, such as ``await bus_proxy.Hello()``
    to make a method call over D-Bus and wait for a reply. It will either
    return a tuple of returned data, or raise :exc:`.DBusErrorResponse`.
    The methods available are defined by the message generator you wrap.

    :param msggen: A message generator object.
    :param ~trio.DBusRouter router: Router to send and receive messages.
    """
    def __init__(self, msggen, router):
        super().__init__(msggen)
        if not isinstance(router, DBusRouter):
            raise TypeError("Proxy can only be used with DBusRequester")
        self._router = router

    def _method_call(self, make_msg):
        async def inner(*args, **kwargs):
            msg = make_msg(*args, **kwargs)
            assert msg.header.message_type is MessageType.method_call
            reply = await self._router.send_and_get_reply(msg)
            return unwrap_msg(reply)

        return inner


class _RouterContext:
    conn = None
    req_ctx = None

    def __init__(self, bus='SESSION'):
        self.bus = bus

    async def __aenter__(self):
        self.conn = await open_dbus_connection(self.bus)
        self.req_ctx = self.conn.router()
        return await self.req_ctx.__aenter__()

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.req_ctx.__aexit__(exc_type, exc_val, exc_tb)
        await self.conn.aclose()


def open_dbus_router(bus='SESSION'):
    """Open a D-Bus 'router' to send and receive messages.

    Use as an async context manager::

        async with open_dbus_router() as req:
            ...

    :param str bus: 'SESSION' or 'SYSTEM' or a supported address.
    :return: :class:`DBusRouter`

    This is a shortcut for::

        conn = await open_dbus_connection()
        async with conn:
            async with conn.router() as req:
                ...
    """
    return _RouterContext(bus)
