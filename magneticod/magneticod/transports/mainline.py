# magneticod - Autonomous BitTorrent DHT crawler and metadata fetcher.
# Copyright (C) 2017  Mert Bora ALPER <bora@boramalper.org>
# Dedicated to Cemile Binay, in whose hands I thrived.
#
# This program is free software: you can redistribute it and/or modify it under the terms of the GNU Affero General
# Public License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any
# later version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied
# warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU Affero General Public License for more
# details.
#
# You should have received a copy of the GNU Affero General Public License along with this program.  If not, see
# <http://www.gnu.org/licenses/>.
import asyncio
import collections
import logging
import sys
import time
import typing

from . import codec

Address = typing.Tuple[str, int]

MessageQueueEntry = typing.NamedTuple("MessageQueueEntry", [
    ("queued_on", int),
    ("message", codec.Message),
    ("address", Address)
])


class Transport(asyncio.DatagramProtocol):
    """
    Mainline DHT Transport

    The signature `class Transport(asyncio.DatagramProtocol)` seems almost oxymoron, but it's indeed more sensible than
    it first seems. `Transport` handles ALL that is related to transporting messages, which includes receiving them
    (`asyncio.DatagramProtocol.datagram_received`), sending them (`asyncio.DatagramTransport.send_to`), pausing and
    resuming writing as requested by the asyncio, and also handling operational errors.
    """

    def __init__(self):
        super().__init__()
        self._datagram_transport = asyncio.DatagramTransport()
        self._write_allowed = asyncio.Event()
        self._queue_nonempty = asyncio.Event()
        self._message_queue = collections.deque()  # type: typing.Deque[MessageQueueEntry]
        self._messenger_task = asyncio.Task(self._send_messages())

    # Offered Functionality
    # =====================
    def send_message(self, message: codec.Message, address: Address) -> None:
        self._message_queue.append(MessageQueueEntry(int(time.monotonic()), message, address))
        if not self._queue_nonempty.is_set():
            self._queue_nonempty.set()

    @staticmethod
    def on_message(message: codec.Message, address: Address):
        pass

    # Private Functionality
    # =====================
    def connection_made(self, transport: asyncio.DatagramTransport) -> None:
        self._datagram_transport = transport
        self._write_allowed.set()

    def datagram_received(self, data: bytes, address: Address) -> None:
        # Ignore nodes that "uses" port 0, as we cannot communicate with them reliably across the different systems.
        # See https://tools.cisco.com/security/center/viewAlert.x?alertId=19935 for slightly more details
        if address[1] == 0:
            return

        try:
            message = codec.decode(data)
        except codec.DecodeError:
            return

        if not isinstance(message, dict):
            return

        self.on_message(message, address)

    def error_received(self, exc: OSError):
        logging.debug("Mainline DHT received error!", exc_info=exc)

    def pause_writing(self):
        self._write_allowed.clear()

    def resume_writing(self):
        self._write_allowed.set()

    def connection_lost(self, exc: Exception):
        if exc:
            logging.fatal("Mainline DHT lost connection! (See the following log entry for the exception.)",
                          exc_info=exc
                          )
        else:
            logging.fatal("Mainline DHT lost connection!")
        sys.exit(1)

    async def _send_messages(self) -> None:
        while True:
            await asyncio.wait([self._write_allowed.wait(), self._queue_nonempty.wait()])
            try:
                queued_on, message, address = self._message_queue.pop()
            except IndexError:
                self._queue_nonempty.clear()
                continue

            if time.monotonic() - queued_on > 60:
                return

            self._datagram_transport.sendto(codec.encode(message), address)
