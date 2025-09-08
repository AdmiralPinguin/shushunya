"""ZeroMQ publish/subscribe helper.

This module provides a thin wrapper around ZeroMQ PUB/SUB sockets.  It
encapsulates the typical pattern of publishing JSON events under a topic
prefix and subscribing to a set of topics.  It is deliberately simple: no
message ordering guarantees are provided beyond what ZeroMQ inherently
offers, and there is no persistent queue.  Consumers should process
messages quickly and avoid blocking the receive loop.

The `Bus` class exposes two main methods:
  * `recv` – poll the subscribed socket for a single message and return
    a tuple of (topic: str, payload: dict).  If no message arrives within
    the timeout, it returns (None, None).
  * `send` – publish a dictionary payload under a given topic.

To configure endpoints and topics, the `VeilOfSilence` runner reads from
``config.yaml``.  Bind and connect addresses should be valid ZeroMQ
endpoints (e.g. ``tcp://127.0.0.1:5582``).  See the docstring in
``run_veil_of_silence.py`` for more details on configuration.
"""

from __future__ import annotations

import json
from typing import Iterable, List, Optional, Tuple

import zmq


class Bus:
    """Simple PUB/SUB bus using ZeroMQ.

    Parameters
    ----------
    bind_pub: str
        The endpoint on which to bind the PUB socket.  Only one process
        should bind to a given endpoint.  Clients subscribe via
        ``connect_sub`` below.
    connect_sub: Iterable[str]
        A collection of endpoints to which the SUB socket should connect.
        Each endpoint may be a TCP, IPC or inproc address.  If multiple
        publishers are used, they should bind on unique ports.
    topics_in: Iterable[str]
        A collection of topic prefixes that should be subscribed to on the
        SUB socket.  Each topic is matched exactly as a prefix; wildcards
        are not supported.
    """

    def __init__(self, bind_pub: str, connect_sub: Iterable[str], topics_in: Iterable[str]):
        ctx = zmq.Context.instance()
        # Publisher socket: binds on a local endpoint
        self.pub = ctx.socket(zmq.PUB)
        self.pub.bind(bind_pub)

        # Subscriber socket: connects to one or more endpoints
        self.sub = ctx.socket(zmq.SUB)
        for ep in connect_sub:
            self.sub.connect(ep)
        for topic in topics_in:
            # Subscribe to each prefix; ensure topics are encoded as UTF‑8
            self.sub.setsockopt_string(zmq.SUBSCRIBE, topic)

    def recv(self, timeout_ms: int = 100) -> Tuple[Optional[str], Optional[dict]]:
        """Receive a single message from the subscribed topics.

        Parameters
        ----------
        timeout_ms: int
            Timeout in milliseconds to wait for a message.  If no message
            arrives before the timeout, returns (None, None).

        Returns
        -------
        tuple
            A tuple (topic, payload) where ``topic`` is the message's topic
            as a string and ``payload`` is the decoded JSON object.  If no
            message arrives, both values are ``None``.
        """
        poller = zmq.Poller()
        poller.register(self.sub, zmq.POLLIN)
        socks = dict(poller.poll(timeout_ms))
        if self.sub in socks:
            try:
                topic_bytes, payload_bytes = self.sub.recv_multipart()
            except ValueError:
                # Unexpected message format; skip it
                return None, None
            try:
                topic = topic_bytes.decode("utf-8")
                payload = json.loads(payload_bytes.decode("utf-8"))
            except Exception:
                # Decoding failed; skip message
                return None, None
            return topic, payload
        return None, None

    def send(self, topic: str, obj: dict) -> None:
        """Publish a JSON-serialisable object under a given topic.

        Parameters
        ----------
        topic: str
            The topic prefix under which to publish the message.  Consumers
            must subscribe to this prefix to receive the event.
        obj: dict
            The message payload to be serialized as JSON.  Non‑serialisable
            values will raise an exception.
        """
        try:
            payload = json.dumps(obj, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Unserialisable message for topic {topic}: {exc}")
        self.pub.send_multipart([topic.encode("utf-8"), payload.encode("utf-8")])