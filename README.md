# ChessCore Terminal Edition

A fully playable chess game for the terminal. No mouse, no typed moves. Offline play needs only the Python standard library. Online play uses a small relay server and the `websockets` package.

## Run (offline)

```
py ascii_chess.py
```

Build a standalone `ChessCore.exe` (one file, no Python needed on the other computer). Install `pyinstaller` and `websockets` first so online play is bundled:

```
py -m PyInstaller --onefile --name ChessCore --hidden-import websockets.sync.client --hidden-import websockets.exceptions ascii_chess.py
```

The result is `dist\ChessCore.exe`. Copy it to the other computer and run it in a terminal (Windows Terminal or cmd), then set **Play Online → Server** to the host's address. Saves and `online.json` are written to the folder you run it from.

## Controls

| Key | Action |
| --- | --- |
| Arrow keys | Move cursor / menu selection |
| Space | Select a piece, then move it |
| Enter | Confirm menu choice / promotion piece |
| Esc | Cancel selection |
| F | Flip the board |
| S / L | Save to / load from `save.json` (offline) |
| Q | Quit (asks whether to save) |

Cursor `[♙]`, selected piece `{♙}`, legal move ` ○ `, capture ` ✕ `. The last move is highlighted, and a king in check is shown on a red square. The side panel shows whose turn it is, captured pieces with the material lead, and the move list in standard notation.

## Online play

One player picks **Play Online → Create room**, chooses a time control and shares the 5-letter room code. The other picks **Join room** and types the code. The server validates every move, runs the clocks and relays chat.

| Key (online) | Action |
| --- | --- |
| T | Chat (Enter sends, Esc cancels) |
| R | Resign |
| D / X | Offer or accept a draw / decline an offer |
| Q | Leave (resigns an active game) |

If the connection drops, the client reconnects automatically. A player who stays away more than 60 seconds forfeits.

The server address is set under **Play Online → Server** (saved in `online.json`), or with the `CHESSCORE_SERVER` environment variable. The default is `ws://localhost:8765`.

## Docker

Run the server:

```
docker compose up -d server
```

It listens on port 8765 and has a health check at `http://localhost:8765/health`. Stop it with `docker compose stop server`.

Run the terminal client in a container (needs an interactive terminal; it connects to the server container):

```
docker compose run --rm client
```

Saves from the container client live in the `chess-data` volume.

To play over the internet, host the server on a machine with a public address and put a TLS reverse proxy in front of port 8765 (for example Caddy or nginx), then set the client's server address to `wss://your.domain`. Plain `ws://` is fine on a local network.

Without Docker: `pip install -r requirements.txt`, then `py chess_server.py` (`HOST` and `PORT` environment variables are supported).

## Tests

```
py -m unittest test_notation test_online -v
```

## Rules implemented

All piece movement, captures, check, checkmate, stalemate, castling, en passant, promotion, plus draws by insufficient material and the 50-move rule.

## Files

- `ascii_chess.py`: the game and online client. Game logic is pure functions on a state dict (pieces stored as `wp`, `bk`, ...), followed by the save system, engine support (`state_to_fen`, `uci_to_move`, `start_engine` for a future Stockfish opponent), input, rendering, menus, the game loops and the online client.
- `chess_server.py`: the online relay server. It imports the rules from `ascii_chess.py`.
- `Dockerfile`, `docker-compose.yml`, `requirements.txt`: container setup.
- `test_online.py`: server tests.
"# chess" 
