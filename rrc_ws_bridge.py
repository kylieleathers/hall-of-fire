#!/Users/kylieleathers/Documents/nomadnet/venv/bin/python3
"""
WebSocket bridge for RRC chat.

Connects to the rrcd hub via Reticulum, then opens a WebSocket server
so any local client (phone app, browser, etc.) can chat through it.

Messages from WebSocket clients → rrcd hub
Messages from rrcd hub → all connected WebSocket clients

Run:
    python3 rrc_ws_bridge.py

Then connect a WebSocket client to:
    ws://YOUR_MAC_IP:8765

Configuration is via environment variables (see README for details):
    RRC_HUB_HASH, RRC_ROOM, RRC_NICK, RRC_WS_PORT, RRC_HTTP_PORT

Message format (JSON):
    Incoming (client → bridge): {"type": "message", "room": "hall-of-fire", "body": "hello"}
    Incoming (client → bridge): {"type": "join",    "room": "hall-of-fire"}
    Outgoing (bridge → client): {"type": "message", "room": "hall-of-fire", "nick": "someone", "body": "hello"}
    Outgoing (bridge → client): {"type": "notice",  "room": "hall-of-fire", "body": "someone joined"}
    Outgoing (bridge → client): {"type": "status",  "body": "connected to Imladris"}
"""

import asyncio
import json
import socket
import threading
import time
import os
from http.server import HTTPServer, SimpleHTTPRequestHandler
import RNS

import websockets
from rrc_client import RRCClient

# ── config ────────────────────────────────────────────────────────────────────
HUB_HASH     = os.environ.get("RRC_HUB_HASH", "7a101b8c8050f1069397eeb232a4511f")
DEFAULT_ROOM = os.environ.get("RRC_ROOM", "hall-of-fire")
NICK         = os.environ.get("RRC_NICK", "traveller")
WS_HOST      = "0.0.0.0"   # accept connections from any device on the network
WS_PORT      = int(os.environ.get("RRC_WS_PORT", "8765"))
HTTP_PORT    = int(os.environ.get("RRC_HTTP_PORT", "8888"))
WEB_DIR      = os.path.join(os.path.dirname(__file__), "web")


def local_ip():
    """Best-effort guess at this machine's LAN IP, for display purposes only."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()

# ── shared state ──────────────────────────────────────────────────────────────
connected_clients: set = set()
loop: asyncio.AbstractEventLoop = None


def broadcast(msg: dict):
    """Send a JSON message to all connected WebSocket clients (thread-safe)."""
    if not connected_clients or loop is None:
        return
    data = json.dumps(msg)
    asyncio.run_coroutine_threadsafe(_broadcast(data), loop)


async def _broadcast(data: str):
    for ws in list(connected_clients):
        try:
            await ws.send(data)
        except Exception:
            pass


# ── RRC callbacks ─────────────────────────────────────────────────────────────

def on_message(room, nick, body):
    broadcast({"type": "message", "room": room, "nick": nick, "body": body})


def on_notice(room, body):
    broadcast({"type": "notice", "room": room, "body": body})


def on_connected(hub_name):
    print(f"[rrc] connected to hub: {hub_name}")
    broadcast({"type": "status", "body": f"connected to {hub_name}"})


def on_disconnected():
    print("[rrc] disconnected from hub")
    broadcast({"type": "status", "body": "disconnected from hub"})


# ── WebSocket handler ─────────────────────────────────────────────────────────

async def handle_client(websocket, client, default_room):
    connected_clients.add(websocket)
    remote = websocket.remote_address
    print(f"[ws] client connected: {remote}")

    # tell the new client what room they're in
    await websocket.send(json.dumps({
        "type": "status",
        "body": f"joined #{default_room} as '{client.nick}'"
    }))

    try:
        async for raw in websocket:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send(json.dumps({"type": "error", "body": "invalid JSON"}))
                continue

            msg_type = msg.get("type")
            room = msg.get("room", default_room)

            if msg_type == "message":
                body = msg.get("body", "").strip()
                nick = msg.get("nick", "").strip() or None
                if body:
                    client.send(room, body, nick=nick)

            elif msg_type == "join":
                try:
                    client.join(room, timeout=10)
                    await websocket.send(json.dumps({"type": "status", "body": f"joined #{room}"}))
                except TimeoutError:
                    await websocket.send(json.dumps({"type": "error", "body": f"could not join #{room}"}))

            else:
                await websocket.send(json.dumps({"type": "error", "body": f"unknown type: {msg_type}"}))

    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        connected_clients.discard(websocket)
        print(f"[ws] client disconnected: {remote}")


# ── startup ───────────────────────────────────────────────────────────────────

def start_rrc(client, room):
    """Connect to RRC hub in a background thread, retrying indefinitely."""
    def _run():
        while True:
            try:
                print(f"[rrc] connecting to hub {HUB_HASH}...")
                client.connect()
                print(f"[rrc] identity: {client.identity_hash}")
                print(f"[rrc] joining #{room}...")
                client.join(room)
                print(f"[rrc] ready — bridge is live")
                return  # success — exit the retry loop
            except Exception as e:
                print(f"[rrc] connection failed: {e} — retrying in 5s...")
                time.sleep(5)

    t = threading.Thread(target=_run, daemon=True)
    t.start()


def start_http():
    """Serve the web/ directory over HTTP in a background thread."""
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=WEB_DIR, **kwargs)
        def log_message(self, fmt, *args):
            pass

    class ReusableHTTPServer(HTTPServer):
        allow_reuse_address = True
        allow_reuse_port = True

    for attempt in range(10):
        try:
            server = ReusableHTTPServer(("0.0.0.0", HTTP_PORT), Handler)
            break
        except OSError:
            if attempt == 9:
                raise
            time.sleep(1)

    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()


async def main():
    global loop
    loop = asyncio.get_running_loop()

    print("[rrc_ws_bridge] starting reticulum...")
    RNS.Reticulum(loglevel=RNS.LOG_WARNING)

    client = RRCClient(
        hub_hash=HUB_HASH,
        nick=NICK,
        identity_path=os.path.expanduser("~/.rrc_bridge_identity"),
        on_message=on_message,
        on_notice=on_notice,
        on_connected=on_connected,
        on_disconnected=on_disconnected,
    )

    start_rrc(client, DEFAULT_ROOM)
    start_http()

    handler = lambda ws: handle_client(ws, client, DEFAULT_ROOM)

    ip = local_ip()
    print(f"[ws] websocket on ws://0.0.0.0:{WS_PORT}")
    print(f"\n  Open on your phone: http://{ip}:{HTTP_PORT}\n")
    print(f"  WebSocket: ws://{ip}:{WS_PORT}\n")

    async with websockets.serve(handler, WS_HOST, WS_PORT):
        await asyncio.Future()  # run forever


if __name__ == "__main__":
    asyncio.run(main())
