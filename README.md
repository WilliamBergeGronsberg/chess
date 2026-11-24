# ChessCore Terminal Edition

A fully playable chess game for the terminal. No mouse, no typed moves. Offline play needs only the Python standard library. Online play uses a small relay server and the `websockets` package.

## Run (offline)

```
py ascii_chess.py
```

Build a standalone `ChessCore.exe` (one file, no Python or separate Stockfish executable needed on the other computer). Install `pyinstaller` and `websockets` first, and place the Windows Stockfish executable at `stockfish\stockfish.exe`:

```
py -m PyInstaller ChessCore.spec
```

The result is `dist\ChessCore.exe`. Copy it to the other computer and run it in a terminal (Windows Terminal or cmd), then set **Play Online → Server** to the host's address. Saves and `online.json` are written to the folder you run it from.

## Play against the computer

Select **Play against computer**, then **Easy**, **Intermediate**, or **Hard**. You play White; Stockfish plays Black. Saves retain the selected difficulty, including when a game is continued or loaded.

When running from Python, this mode needs a compiled Stockfish executable; the `stockfish` source folder alone is not enough. Place `stockfish.exe` next to `ascii_chess.py`, in `stockfish\`, or in `stockfish\src\` (on Linux/macOS, use `stockfish`), or add it to your PATH. Alternatively, set `CHESSCORE_STOCKFISH` to the full executable path. The Windows build includes Stockfish; the Docker client needs it installed separately.

Stockfish is licensed under GPLv3; its license is included in the build. The official Stockfish 19 source is available at https://github.com/official-stockfish/Stockfish/releases/tag/sf_19. Check its license obligations before distributing the executable.

Easy, Intermediate, and Hard use Stockfish skill levels 0, 10, and 20, with up to 200 ms, 500 ms, and 1 second of thinking time respectively. If the engine cannot start or returns an invalid move, the game displays an error and offers to save before returning to the menu.

## Download page

Open [index.html](./index.html) to preview the terminal-themed download page. To publish it, upload only `index.html` and `dist\ChessCore.exe` to your static host, keeping the EXE in a `dist` folder next to the page.

## Controls

| Key | Action |
| --- | --- |
| Arrow keys | Move cursor / menu selection |
| Space | Select a piece, then move it |
| Enter | Confirm menu choice / promotion piece |
| Esc | Cancel selection |
| F | Flip the board |
| S / L | Save to / load from `save.json` (offline) |
| Q | Quit (asks whether to save; then shows a simulated Ubuntu-style server failure screen before exiting normally) |

Cursor `[♙]`, selected piece `{♙}`, legal move ` ○ `, capture ` ✕ `. The last move is highlighted, and a king in check is shown on a red square. The side panel shows whose turn it is, captured pieces with the material lead, and the move list in standard notation.

The board grows automatically when the terminal is at least 30 rows tall (33 for online play), and gets wider at 88 columns. Smaller windows use the compact board. To enlarge the piece characters too, increase your terminal's font size.

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
py -m unittest test_notation test_online test_shutdown test_computer -v
```

## Rules implemented

All piece movement, captures, check, checkmate, stalemate, castling, en passant, promotion, plus draws by insufficient material and the 50-move rule.

## Files

- `ascii_chess.py`: the game, Stockfish opponent and online client. Game logic is pure functions on a state dict (pieces stored as `wp`, `bk`, ...), followed by the save system, engine support, input, rendering, menus, the game loops and the online client.
- `chess_server.py`: the online relay server. It imports the rules from `ascii_chess.py`.
- `Dockerfile`, `docker-compose.yml`, `requirements.txt`: container setup.
- `test_online.py`: server tests.
"# chess" 
