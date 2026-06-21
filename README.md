# Hall of Fire — RRC Chat over Reticulum

A mobile-friendly chat client for [RRC](https://github.com/kc1awv/rrcd) (a simple
chat room protocol) running on [Reticulum](https://reticulum.network/), the
encrypted, infrastructure-independent mesh networking stack. Includes a
NomadNet node, a Python RRC client/library, a WebSocket bridge, and a
LOTR-themed PWA so you can chat from any phone on your local network.

```
┌──────────┐     local socket     ┌────────┐     RNS link     ┌──────┐
│  rnsd     │◄────────────────────┤ rrcd   │◄─────────────────┤ bridge│◄── WebSocket ──► phone / browser
│ (RNS host)│                     │ (hub)  │                  │       │
└──────────┘                      └────────┘                  └──────┘
                                                                  │
                                                            HTTP (web/)
```

## Components

- **`rrc_client.py`** — `RRCClient` class wrapping the RRC protocol (CBOR
  envelopes over a Reticulum `Link`), plus a terminal CLI chat client.
- **`rrc_ws_bridge.py`** — Bridges an RRC hub to a WebSocket, and serves the
  web app over HTTP, so any browser on your network can join the chat without
  needing Reticulum installed locally.
- **`web/`** — A small PWA (works added-to-homescreen on iOS/Android) that
  connects to the bridge's WebSocket.

## Requirements

- Python 3.10+
- A running [Reticulum](https://reticulum.network/) network (locally, this
  means `rnsd`)
- An [`rrcd`](https://github.com/kc1awv/rrcd) hub to connect to — either your
  own or someone else's

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install git+https://github.com/kc1awv/rrcd.git
```

## Configuration

Everything is configured via environment variables (defaults shown are this
repo's example hub — point these at your own hub):

| Variable        | Default                            | Used by              |
|-----------------|-------------------------------------|-----------------------|
| `RRC_HUB_HASH`  | `7a101b8c8050f1069397eeb232a4511f`  | client, bridge        |
| `RRC_ROOM`      | `hall-of-fire`                      | client, bridge        |
| `RRC_NICK`      | `traveller`                         | client, bridge        |
| `RRC_WS_PORT`   | `8765`                              | bridge                |
| `RRC_HTTP_PORT` | `8888`                              | bridge                |

The web app can also override the room/port per-session via query string,
e.g. `http://<ip>:8888/?room=hall-of-fire&ws_port=8765`.

## Running it

Reticulum uses a "one host, many clients" model per machine — the first RNS
process to start becomes the shared host that everything else connects
through. **Always start `rnsd` first**, or other processes may become the
host and take the network down with them when they exit.

```bash
# 1. Reticulum daemon — start first, wait for it to come up
venv/bin/rnsd

# 2. RRC hub (if you're running your own; skip if joining someone else's)
venv/bin/rrcd

# 3. WebSocket bridge — connects to the hub, serves the web app
RRC_HUB_HASH=<your hub hash> venv/bin/python3 rrc_ws_bridge.py

# 4. (optional) NomadNet, for browsing/serving Reticulum pages
venv/bin/nomadnet
```

Then open `http://<your-machine's-LAN-IP>:8888` on your phone (same WiFi).

### Terminal client

```bash
venv/bin/python3 rrc_client.py --hub <hub_hash> --room hall-of-fire --nick yourname
```

### Identities

The bridge and CLI client use separate identity files (`~/.rrc_bridge_identity`
and `~/.rrc_client_identity`) so the hub doesn't treat them as the same peer.
These are generated on first run and are **not** committed to git — they're
your private Reticulum keys.

## Ideas for future features
- Multi-room support in the web UI (room switcher instead of one fixed room)
- Persistent scrollback (store recent messages so a reload doesn't lose history)
- Typing indicators / read receipts via lightweight RRC notices
- Desktop/mobile push notifications when the tab/app is backgrounded
- `launchd`/`systemd` unit files for running `rnsd` + `rrcd` + bridge as services
- Docker Compose setup for one-command self-hosting
- A hub directory/discovery page (`rrc://` links to known public hubs)
- File or image sharing over RRC (chunked binary bodies)
- Local-first message history (SQLite) searchable from the web UI

