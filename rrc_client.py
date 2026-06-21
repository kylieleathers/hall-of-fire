#!/Users/kylieleathers/Documents/nomadnet/venv/bin/python3
"""RRC client library — wraps Reticulum + rrcd protocol into a simple class."""

import os
import time
import threading
import RNS
import cbor2

# ── RRC protocol constants ────────────────────────────────────────────────────
RRC_VERSION = 1

K_V, K_T, K_ID, K_TS, K_SRC, K_ROOM, K_BODY, K_NICK = 0, 1, 2, 3, 4, 5, 6, 7

T_HELLO, T_WELCOME          = 1, 2
T_JOIN,  T_JOINED, T_PART   = 10, 11, 12
T_MSG,   T_NOTICE, T_ACTION = 20, 21, 22
T_PING,  T_PONG             = 30, 31

B_HELLO_NAME, B_HELLO_VER, B_HELLO_CAPS = 0, 1, 2

DEST_NAME = "rrc.hub"


class RRCClient:
    """
    Connects to an rrcd hub, joins a room, and sends/receives messages.

    Usage:
        client = RRCClient(
            hub_hash="7a101b8c8050f1069397eeb232a4511f",
            nick="traveller",
            on_message=lambda room, nick, body: print(f"[#{room}] {nick}: {body}"),
        )
        client.connect()
        client.join("hall-of-fire")
        client.send("hall-of-fire", "hello world")
        client.disconnect()
    """

    def __init__(self, hub_hash, nick="traveller", identity_path=None,
                 on_message=None, on_notice=None, on_connected=None, on_disconnected=None):
        self.hub_hash = hub_hash
        self.nick = nick
        self.identity_path = identity_path or os.path.expanduser("~/.rrc_client_identity")

        # callbacks — set these before calling connect()
        self.on_message = on_message
        self.on_notice = on_notice
        self.on_connected = on_connected
        self.on_disconnected = on_disconnected

        self._identity = None
        self._link = None
        self._welcomed = threading.Event()
        self._joined_rooms = set()
        self._join_events = {}
        self._lock = threading.Lock()

    # ── public API ────────────────────────────────────────────────────────────

    def connect(self, timeout=15):
        """Start Reticulum, load identity, open a link to the hub. Blocks until welcomed."""
        self._init_identity()
        hub_dest = self._resolve_hub(self.hub_hash)
        RNS.Link(hub_dest,
                 established_callback=self._on_established,
                 closed_callback=self._on_closed)
        if not self._welcomed.wait(timeout=timeout):
            raise TimeoutError("timed out waiting for WELCOME from hub")

    def join(self, room, timeout=10):
        """Join a room. Blocks until the server confirms JOINED."""
        evt = threading.Event()
        with self._lock:
            self._join_events[room] = evt
        self._send(self._envelope(T_JOIN, room=room))
        if not evt.wait(timeout=timeout):
            raise TimeoutError(f"timed out waiting for JOINED #{room}")

    def send(self, room, text, nick=None):
        """Send a chat message to a room. nick overrides the client's default nick."""
        if not self.connected:
            return
        self._send(self._envelope(T_MSG, room=room, body=text, nick_override=nick))

    def disconnect(self):
        """Close the link gracefully."""
        if self._link:
            self._link.teardown()

    @property
    def connected(self):
        return self._link is not None and self._welcomed.is_set()

    @property
    def identity_hash(self):
        return self._identity.hash.hex() if self._identity else None

    # ── internals ─────────────────────────────────────────────────────────────

    def _init_identity(self):
        if os.path.isfile(self.identity_path):
            self._identity = RNS.Identity.from_file(self.identity_path)
        else:
            self._identity = RNS.Identity()
            self._identity.to_file(self.identity_path)

    def _resolve_hub(self, hub_hash_hex, timeout=10):
        hub_hash = bytes.fromhex(hub_hash_hex)
        if not RNS.Transport.has_path(hub_hash):
            RNS.Transport.request_path(hub_hash)
            deadline = time.time() + timeout
            while time.time() < deadline:
                if RNS.Transport.has_path(hub_hash):
                    break
                time.sleep(0.2)

        hub_identity = None
        deadline = time.time() + timeout
        while time.time() < deadline:
            hub_identity = RNS.Identity.recall(hub_hash)
            if hub_identity:
                break
            time.sleep(0.2)

        if hub_identity is None:
            raise ConnectionError(f"could not resolve hub identity: {hub_hash_hex}")

        app_name, aspects = RNS.Destination.app_and_aspects_from_name(DEST_NAME)
        return RNS.Destination(hub_identity, RNS.Destination.OUT, RNS.Destination.SINGLE,
                               app_name, *aspects)

    def _envelope(self, msg_type, room=None, body=None, nick_override=None):
        env = {
            K_V:    RRC_VERSION,
            K_T:    int(msg_type),
            K_ID:   os.urandom(8),
            K_TS:   int(time.time() * 1000),
            K_SRC:  self._identity.hash,
            K_NICK: nick_override if nick_override else self.nick,
        }
        if room is not None:
            env[K_ROOM] = room
        if body is not None:
            env[K_BODY] = body
        return env

    def _send(self, env):
        if self._link is None:
            return
        try:
            RNS.Packet(self._link, cbor2.dumps(env)).send()
        except Exception:
            pass

    def _on_established(self, lnk):
        self._link = lnk
        self._link.set_packet_callback(self._on_packet)
        self._link.identify(self._identity)
        hello_body = {B_HELLO_NAME: "rrc_client", B_HELLO_VER: "0.1", B_HELLO_CAPS: {}}
        self._send(self._envelope(T_HELLO, body=hello_body))

    def _on_closed(self, lnk):
        self._link = None
        self._welcomed.clear()
        with self._lock:
            self._joined_rooms.clear()
        if self.on_disconnected:
            self.on_disconnected()

    def _on_packet(self, data, packet):
        try:
            msg = cbor2.loads(data)
        except Exception:
            return

        t    = msg.get(K_T)
        room = msg.get(K_ROOM, "")
        body = msg.get(K_BODY, "")
        nick = msg.get(K_NICK, "<unknown>")

        if t == T_WELCOME:
            hub_name = body.get(0, "hub") if isinstance(body, dict) else "hub"
            self._welcomed.set()
            if self.on_connected:
                self.on_connected(hub_name)

        elif t == T_JOINED:
            with self._lock:
                self._joined_rooms.add(room)
                evt = self._join_events.pop(room, None)
            if evt:
                evt.set()

        elif t == T_MSG:
            src = msg.get(K_SRC, b"")
            src_hex = src.hex() if isinstance(src, bytes) else str(src)
            if src_hex != self._identity.hash.hex() and self.on_message:
                self.on_message(room, nick, body)

        elif t == T_NOTICE:
            if self.on_notice:
                self.on_notice(room, body)

        elif t == T_PONG:
            pass


# ── CLI entrypoint ────────────────────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(description="RRC chat client")
    parser.add_argument("--hub",  default=os.environ.get("RRC_HUB_HASH", "7a101b8c8050f1069397eeb232a4511f"))
    parser.add_argument("--room", default=os.environ.get("RRC_ROOM", "hall-of-fire"))
    parser.add_argument("--nick", default=os.environ.get("RRC_NICK", "traveller"))
    args = parser.parse_args()

    print("[rrc_client] starting reticulum...")
    RNS.Reticulum(loglevel=RNS.LOG_WARNING)

    client = RRCClient(
        hub_hash=args.hub,
        nick=args.nick,
        on_message=lambda room, nick, body: print(f"[#{room}] {nick}: {body}"),
        on_notice=lambda room, body: print(f"[notice #{room}] {body}"),
        on_connected=lambda hub_name: print(f"[connected] welcomed by {hub_name}"),
        on_disconnected=lambda: print("[disconnected]"),
    )

    print(f"[hub] connecting to {args.hub}...")
    client.connect()
    print(f"[identity] {client.identity_hash}")

    print(f"[room] joining #{args.room}...")
    client.join(args.room)

    print(f"\nConnected to #{args.room} as '{args.nick}'. Type a message and press enter. Ctrl+C to quit.\n")
    try:
        while True:
            text = input()
            if text.strip():
                client.send(args.room, text)
    except KeyboardInterrupt:
        print("\n[bye]")
        client.disconnect()


if __name__ == "__main__":
    main()
