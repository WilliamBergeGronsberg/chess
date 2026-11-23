For GitHub Copilot Agent (in VS Code), create a file called:

.github/copilot-instructions.md


at the root of your project.

Example structure:

my-project/
│
├─ .github/
│  └─ copilot-instructions.md
│
├─ ascii_chess.py
│
└─ README.md


Then put your project rules inside.

Example:

# ChessCore Terminal Edition

## Project Goal

Build a fully playable chess game in Python.

The game runs entirely in the terminal.

No GUI frameworks.

No external dependencies for offline play (online play uses `websockets`, see Allowed Imports).

The final game should work with:

python ascii_chess.py

and later:

pyinstaller --onefile ascii_chess.py


## Architecture Rules

- Use a single Python file.
- Use functions instead of giant code blocks.
- Keep game logic separate from rendering.
- Keep rendering separate from input handling.
- Use descriptive function names.
- Avoid duplicated code.

## Allowed Imports

```python
import os
import time
import msvcrt
import json
import subprocess
import threading, queue, sys   # online client and cross-platform input
# termios, tty, select          # Linux/macOS input fallback (Docker)

Offline play uses the standard library only.
Online play (the client, and chess_server.py) uses the `websockets` pip package,
which the client imports lazily so offline play never needs it.

Input

Controls:

Arrow Keys = move cursor
Space = select piece / move piece
Enter = confirm menu choice
Esc = cancel
Q = quit

The user must never type moves.

Board Representation

Store board data like:

wp wr wn wb wq wk
bp br bn bb bq bk


Do not use unicode chess characters for game logic.

Unicode is only used while rendering.

Rendering

Render pieces using:

♔ ♕ ♖ ♗ ♘ ♙
♚ ♛ ♜ ♝ ♞ ♟


Render board using box characters:

┌ ┐ └ ┘ ├ ┤ ┬ ┴ ┼ │ ─

Cursor

Current cursor square:

[♙]


Selected piece:

{♙}


Legal move:

 ○


Capture:

 ✕

Startup Screen

Display a centered ASCII logo.

Animate logo character-by-character.

Use green terminal colors.

Do not use fake loading bars.

Do not use fake progress indicators.

Style should resemble Claude Code or GitHub Copilot CLI.

Chess Rules

Implement:

Pawn movement
Knight movement
Bishop movement
Rook movement
Queen movement
King movement
Captures
Check
Checkmate
Stalemate
Castling
En passant
Promotion
Save System

Save current game to:

save.json

Support:

Save
Load
Continue
Future Support

Design architecture so Stockfish can be added later through subprocess.Popen().


For best results, also add a project README:

```text
README.md


describing the game and goals. Copilot reads both the README and .github/copilot-instructions.md and will generate much better code that matches your project style.