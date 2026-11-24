"""ChessCore Terminal Edition - a fully playable chess game for the terminal."""

import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time

try:
    import msvcrt
except ImportError:  # Linux/macOS (and the Docker server, which only needs the rules)
    msvcrt = None
    import select
    import termios
    import tty

# ============================================================
# CONSTANTS
# ============================================================

SAVE_FILE = "save.json"
FILES = "abcdefgh"

GREEN = "\033[92m"
DIM_GREEN = "\033[32m"
WHITE = "\033[97m"
YELLOW = "\033[93m"
RED = "\033[91m"
RESET = "\033[0m"
BLUE = "\033[94m"

LIGHT_SQUARE = "\033[48;5;238m"
DARK_SQUARE = "\033[48;5;234m"
LAST_MOVE_SQUARE = "\033[48;5;58m"
CHECK_SQUARE = "\033[48;5;88m"
TONES = {"info": WHITE, "ok": GREEN, "warn": YELLOW, "error": RED}

# Unicode is used for rendering only; game logic uses "wp", "bk", ...
PIECE_GLYPHS = {
    "wk": "♔", "wq": "♕", "wr": "♖", "wb": "♗", "wn": "♘", "wp": "♙",
    "bk": "♚", "bq": "♛", "br": "♜", "bb": "♝", "bn": "♞", "bp": "♟",
}

KNIGHT_STEPS = [(-2, -1), (-2, 1), (-1, -2), (-1, 2), (1, -2), (1, 2), (2, -1), (2, 1)]
KING_STEPS = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]
BISHOP_DIRS = [(-1, -1), (-1, 1), (1, -1), (1, 1)]
ROOK_DIRS = [(-1, 0), (1, 0), (0, -1), (0, 1)]

PROMOTION_CHOICES = ["q", "r", "b", "n"]
FINAL_STATUSES = ("checkmate", "stalemate", "draw")
COMPUTER_LEVELS = {"easy": (0, 200), "intermediate": (10, 500), "hard": (20, 1000)}

LETTER_ART = {
    "C": [" ██████╗", "██╔════╝", "██║     ", "██║     ", "╚██████╗", " ╚═════╝"],
    "H": ["██╗  ██╗", "██║  ██║", "███████║", "██╔══██║", "██║  ██║", "╚═╝  ╚═╝"],
    "E": ["███████╗", "██╔════╝", "█████╗  ", "██╔══╝  ", "███████╗", "╚══════╝"],
    "S": ["███████╗", "██╔════╝", "███████╗", "╚════██║", "███████║", "╚══════╝"],
    "O": [" ██████╗ ", "██╔═══██╗", "██║   ██║", "██║   ██║", "╚██████╔╝", " ╚═════╝ "],
    "R": ["██████╗ ", "██╔══██╗", "██████╔╝", "██╔══██╗", "██║  ██║", "╚═╝  ╚═╝"],
}


# ============================================================
# GAME LOGIC (no rendering, no input)
# ============================================================

def new_board():
    back_rank = "rnbqkbnr"
    board = [["b" + kind for kind in back_rank], ["bp"] * 8]
    board += [[""] * 8 for _ in range(4)]
    board += [["wp"] * 8, ["w" + kind for kind in back_rank]]
    return board


def new_game():
    return {
        "board": new_board(),
        "turn": "w",
        "castling": {"wK": True, "wQ": True, "bK": True, "bQ": True},
        "en_passant": None,
        "halfmove": 0,
        "fullmove": 1,
        "history": [],
    }


def copy_state(state):
    copied = {
        "board": [row[:] for row in state["board"]],
        "turn": state["turn"],
        "castling": dict(state["castling"]),
        "en_passant": state["en_passant"],
        "halfmove": state["halfmove"],
        "fullmove": state["fullmove"],
        "history": list(state["history"]),
    }
    if "computer" in state:
        copied["computer"] = state["computer"]
    return copied


def in_bounds(row, col):
    return 0 <= row < 8 and 0 <= col < 8


def opponent(color):
    return "b" if color == "w" else "w"


def square_name(row, col):
    return f"{FILES[col]}{8 - row}"


def find_king(board, color):
    for row in range(8):
        for col in range(8):
            if board[row][col] == color + "k":
                return row, col
    return None


def is_square_attacked(board, row, col, by_color):
    pawn_row = row + 1 if by_color == "w" else row - 1
    for d_col in (-1, 1):
        if in_bounds(pawn_row, col + d_col) and board[pawn_row][col + d_col] == by_color + "p":
            return True

    for d_row, d_col in KNIGHT_STEPS:
        r, c = row + d_row, col + d_col
        if in_bounds(r, c) and board[r][c] == by_color + "n":
            return True

    for d_row, d_col in KING_STEPS:
        r, c = row + d_row, col + d_col
        if in_bounds(r, c) and board[r][c] == by_color + "k":
            return True

    for directions, attackers in ((BISHOP_DIRS, "bq"), (ROOK_DIRS, "rq")):
        for d_row, d_col in directions:
            r, c = row + d_row, col + d_col
            while in_bounds(r, c):
                piece = board[r][c]
                if piece:
                    if piece[0] == by_color and piece[1] in attackers:
                        return True
                    break
                r, c = r + d_row, c + d_col
    return False


def is_in_check(board, color):
    king = find_king(board, color)
    return king is not None and is_square_attacked(board, king[0], king[1], opponent(color))


def sliding_targets(board, row, col, directions):
    color = board[row][col][0]
    targets = []
    for d_row, d_col in directions:
        r, c = row + d_row, col + d_col
        while in_bounds(r, c):
            piece = board[r][c]
            if not piece:
                targets.append((r, c))
            else:
                if piece[0] != color:
                    targets.append((r, c))
                break
            r, c = r + d_row, c + d_col
    return targets


def step_targets(board, row, col, steps):
    color = board[row][col][0]
    targets = []
    for d_row, d_col in steps:
        r, c = row + d_row, col + d_col
        if in_bounds(r, c) and (not board[r][c] or board[r][c][0] != color):
            targets.append((r, c))
    return targets


def pawn_targets(state, row, col):
    board = state["board"]
    color = board[row][col][0]
    step = -1 if color == "w" else 1
    start_row = 6 if color == "w" else 1
    next_row = row + step
    targets = []

    if in_bounds(next_row, col) and not board[next_row][col]:
        targets.append((next_row, col))
        if row == start_row and not board[row + 2 * step][col]:
            targets.append((row + 2 * step, col))

    for d_col in (-1, 1):
        r, c = next_row, col + d_col
        if not in_bounds(r, c):
            continue
        if (board[r][c] and board[r][c][0] != color) or (r, c) == state["en_passant"]:
            targets.append((r, c))
    return targets


def castling_targets(state, row, col):
    board = state["board"]
    color = board[row][col][0]
    home_row = 7 if color == "w" else 0
    enemy = opponent(color)
    if (row, col) != (home_row, 4) or is_square_attacked(board, home_row, 4, enemy):
        return []

    targets = []
    safe = lambda c: not is_square_attacked(board, home_row, c, enemy)
    if (state["castling"][color + "K"] and board[home_row][7] == color + "r"
            and not board[home_row][5] and not board[home_row][6] and safe(5) and safe(6)):
        targets.append((home_row, 6))
    if (state["castling"][color + "Q"] and board[home_row][0] == color + "r"
            and not any(board[home_row][c] for c in (1, 2, 3)) and safe(3) and safe(2)):
        targets.append((home_row, 2))
    return targets


def pseudo_legal_targets(state, row, col):
    board = state["board"]
    kind = board[row][col][1]
    if kind == "p":
        return pawn_targets(state, row, col)
    if kind == "n":
        return step_targets(board, row, col, KNIGHT_STEPS)
    if kind == "b":
        return sliding_targets(board, row, col, BISHOP_DIRS)
    if kind == "r":
        return sliding_targets(board, row, col, ROOK_DIRS)
    if kind == "q":
        return sliding_targets(board, row, col, BISHOP_DIRS + ROOK_DIRS)
    return step_targets(board, row, col, KING_STEPS) + castling_targets(state, row, col)


def is_promotion_move(state, frm, to):
    piece = state["board"][frm[0]][frm[1]]
    return piece[1] == "p" and to[0] in (0, 7)


def is_capture_move(state, frm, to):
    board = state["board"]
    if board[to[0]][to[1]]:
        return True
    return board[frm[0]][frm[1]][1] == "p" and frm[1] != to[1]


def move_to_uci(frm, to, promotion=None):
    return square_name(*frm) + square_name(*to) + (promotion or "")


def apply_move(state, frm, to, promotion="q"):
    """Return a new state with the move played. The move must be pseudo-legal."""
    new = copy_state(state)
    board = new["board"]
    from_row, from_col = frm
    to_row, to_col = to
    piece = board[from_row][from_col]
    color, kind = piece[0], piece[1]
    captured = board[to_row][to_col]

    if kind == "p" and from_col != to_col and not captured:
        captured = board[from_row][to_col]
        board[from_row][to_col] = ""

    board[to_row][to_col] = piece
    board[from_row][from_col] = ""

    if kind == "k" and abs(to_col - from_col) == 2:
        if to_col > from_col:
            board[to_row][5], board[to_row][7] = board[to_row][7], ""
        else:
            board[to_row][3], board[to_row][0] = board[to_row][0], ""

    promoted = kind == "p" and to_row in (0, 7)
    if promoted:
        board[to_row][to_col] = color + promotion

    if kind == "k":
        new["castling"][color + "K"] = False
        new["castling"][color + "Q"] = False
    rook_homes = {(7, 0): "wQ", (7, 7): "wK", (0, 0): "bQ", (0, 7): "bK"}
    for square in (frm, to):
        if square in rook_homes:
            new["castling"][rook_homes[square]] = False

    new["en_passant"] = ((from_row + to_row) // 2, from_col) if kind == "p" and abs(to_row - from_row) == 2 else None
    new["halfmove"] = 0 if kind == "p" or captured else state["halfmove"] + 1
    if color == "b":
        new["fullmove"] += 1
    new["turn"] = opponent(color)
    new["history"].append(move_to_uci(frm, to, promotion if promoted else None))
    return new


def legal_moves(state, row, col):
    piece = state["board"][row][col]
    if not piece or piece[0] != state["turn"]:
        return []
    color = piece[0]
    return [
        target for target in pseudo_legal_targets(state, row, col)
        if not is_in_check(apply_move(state, (row, col), target)["board"], color)
    ]


def has_legal_move(state):
    for row in range(8):
        for col in range(8):
            if legal_moves(state, row, col):
                return True
    return False


def has_insufficient_material(board):
    pieces = [p for row in board for p in row if p and p[1] != "k"]
    return not pieces or (len(pieces) == 1 and pieces[0][1] in "bn")


def game_status(state):
    """Return 'playing', 'check', 'checkmate', 'stalemate' or 'draw'."""
    in_check = is_in_check(state["board"], state["turn"])
    if not has_legal_move(state):
        return "checkmate" if in_check else "stalemate"
    if state["halfmove"] >= 100 or has_insufficient_material(state["board"]):
        return "draw"
    return "check" if in_check else "playing"


def draw_reason(state):
    if has_insufficient_material(state["board"]):
        return "insufficient material"
    return "the fifty-move rule"


PIECE_NAMES = {"p": "pawn", "n": "knight", "b": "bishop", "r": "rook", "q": "queen", "k": "king"}
PIECE_VALUES = {"p": 1, "n": 3, "b": 3, "r": 5, "q": 9, "k": 0}
START_COUNTS = {"q": 1, "r": 2, "b": 2, "n": 2, "p": 8}


def material(board, color):
    return sum(PIECE_VALUES[piece[1]] for row in board for piece in row if piece and piece[0] == color)


def captured_pieces(board, by_color):
    """Kinds of the opponent's pieces missing from the board, most valuable first."""
    victim = opponent(by_color)
    on_board = {kind: 0 for kind in START_COUNTS}
    for row in board:
        for piece in row:
            if piece and piece[0] == victim and piece[1] in on_board:
                on_board[piece[1]] += 1
    return [kind for kind in START_COUNTS for _ in range(max(0, START_COUNTS[kind] - on_board[kind]))]


def move_to_san(state, frm, to, promotion=None):
    """Return (standard algebraic notation, state after the move) for a legal move."""
    board = state["board"]
    piece = board[frm[0]][frm[1]]
    kind = piece[1]
    capture = is_capture_move(state, frm, to)

    if kind == "k" and abs(to[1] - frm[1]) == 2:
        text = "O-O" if to[1] > frm[1] else "O-O-O"
    elif kind == "p":
        text = (FILES[frm[1]] + "x" if capture else "") + square_name(*to)
        text += "=" + promotion.upper() if promotion else ""
    else:
        rivals = [
            (r, c) for r in range(8) for c in range(8)
            if (r, c) != frm and board[r][c] == piece and to in legal_moves(state, r, c)
        ]
        detail = ""
        if rivals:
            if all(c != frm[1] for _, c in rivals):
                detail = FILES[frm[1]]
            elif all(r != frm[0] for r, _ in rivals):
                detail = str(8 - frm[0])
            else:
                detail = square_name(*frm)
        text = kind.upper() + detail + ("x" if capture else "") + square_name(*to)

    after = apply_move(state, frm, to, promotion or "q")
    status = game_status(after)
    return text + {"checkmate": "#", "check": "+"}.get(status, ""), after


_SAN_CACHE = {"moves": [], "sans": [], "state": None}


def san_history(history):
    """Notation for every move in `history` (UCI strings), computed incrementally and cached."""
    cache = _SAN_CACHE
    if cache["state"] is None or history[: len(cache["moves"])] != cache["moves"]:
        cache.update(moves=[], sans=[], state=new_game())
    try:
        for uci in history[len(cache["moves"]):]:
            frm, to, promotion = uci_to_move(uci)
            san, cache["state"] = move_to_san(cache["state"], frm, to, promotion)
            cache["moves"].append(uci)
            cache["sans"].append(san)
    except (ValueError, IndexError, TypeError):
        cache.update(moves=[], sans=[], state=None)
        return list(history)
    return cache["sans"]


# ============================================================
# SAVE SYSTEM
# ============================================================

def is_valid_state(data):
    try:
        board = data["board"]
        if len(board) != 8 or any(len(row) != 8 for row in board):
            return False
        flat = [piece for row in board for piece in row]
        if any(piece and piece not in PIECE_GLYPHS for piece in flat):
            return False
        if flat.count("wk") != 1 or flat.count("bk") != 1:
            return False
        if data["turn"] not in ("w", "b"):
            return False
        if "computer" in data and data["computer"] not in COMPUTER_LEVELS:
            return False
        if set(data["castling"]) != {"wK", "wQ", "bK", "bQ"}:
            return False
        ep = data["en_passant"]
        if ep is not None and not (len(ep) == 2 and in_bounds(ep[0], ep[1])):
            return False
        return all(isinstance(data[key], int) for key in ("halfmove", "fullmove")) \
            and all(isinstance(move, str) for move in data["history"])
    except (KeyError, TypeError):
        return False


def save_game(state, path=SAVE_FILE):
    temp_path = path + ".tmp"
    try:
        with open(temp_path, "w", encoding="utf-8") as file:
            json.dump(state, file, indent=2)
        os.replace(temp_path, path)
        return True
    except OSError:
        return False


def state_from_data(data):
    """Build a game state from validated JSON data (JSON turns tuples into lists)."""
    state = copy_state(data)
    ep = data["en_passant"]
    state["en_passant"] = tuple(ep) if ep is not None else None
    return state


def load_game(path=SAVE_FILE):
    try:
        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, ValueError):
        return None
    return state_from_data(data) if is_valid_state(data) else None


# ============================================================
# STOCKFISH ENGINE SUPPORT
# ============================================================

def state_to_fen(state):
    rows = []
    for row in state["board"]:
        text, empty = "", 0
        for piece in row:
            if not piece:
                empty += 1
                continue
            text += str(empty) if empty else ""
            empty = 0
            text += piece[1].upper() if piece[0] == "w" else piece[1]
        rows.append(text + (str(empty) if empty else ""))

    castling = "".join(
        letter for key, letter in (("wK", "K"), ("wQ", "Q"), ("bK", "k"), ("bQ", "q"))
        if state["castling"][key]
    ) or "-"
    ep = square_name(*state["en_passant"]) if state["en_passant"] else "-"
    return f"{'/'.join(rows)} {state['turn']} {castling} {ep} {state['halfmove']} {state['fullmove']}"


def uci_to_move(text):
    """Convert 'e7e8q' to ((row, col), (row, col), 'q' or None)."""
    frm = (8 - int(text[1]), FILES.index(text[0]))
    to = (8 - int(text[3]), FILES.index(text[2]))
    return frm, to, (text[4] if len(text) > 4 else None)


def send_to_engine(engine, command):
    engine.stdin.write(command + "\n")
    engine.stdin.flush()


def read_engine_until(engine, prefix):
    deadline = time.monotonic() + 15
    while True:
        try:
            line = engine.uci_lines.get(timeout=max(0, deadline - time.monotonic()))
        except queue.Empty:
            raise RuntimeError(f"Stockfish timed out waiting for {prefix}.") from None
        if line is None:
            raise RuntimeError(f"Stockfish stopped before sending {prefix}.")
        if line.startswith(prefix):
            return line.strip()


def stockfish_path():
    configured = os.environ.get("CHESSCORE_STOCKFISH")
    if configured:
        return configured
    base = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
    name = "stockfish.exe" if os.name == "nt" else "stockfish"
    for path in (os.path.join(base, name), os.path.join(base, "stockfish", name),
                 os.path.join(base, "stockfish", "src", name),
                 os.path.join(os.path.dirname(os.path.abspath(__file__)), "stockfish", name)):
        if os.path.isfile(path):
            return path
    return shutil.which("stockfish") or name


def start_engine(path):
    engine = subprocess.Popen(
        [path], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, bufsize=1,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    engine.uci_lines = queue.Queue()

    def collect_output():
        try:
            for line in engine.stdout:
                engine.uci_lines.put(line)
        finally:
            engine.uci_lines.put(None)

    threading.Thread(target=collect_output, daemon=True).start()
    try:
        send_to_engine(engine, "uci")
        read_engine_until(engine, "uciok")
        send_to_engine(engine, "isready")
        read_engine_until(engine, "readyok")
    except (OSError, RuntimeError):
        stop_engine(engine)
        raise
    return engine


def configure_engine(engine, level):
    skill, _ = COMPUTER_LEVELS[level]
    send_to_engine(engine, f"setoption name Skill Level value {skill}")
    send_to_engine(engine, "ucinewgame")
    send_to_engine(engine, "isready")
    read_engine_until(engine, "readyok")


def engine_best_move(engine, state, movetime_ms=500):
    send_to_engine(engine, f"position fen {state_to_fen(state)}")
    send_to_engine(engine, f"go movetime {movetime_ms}")
    line = read_engine_until(engine, "bestmove")
    parts = line.split()
    if len(parts) < 2 or not re.fullmatch(r"[a-h][1-8][a-h][1-8][qrbn]?", parts[1]):
        raise RuntimeError(f"Stockfish returned an invalid move: {line}")
    frm, to, promotion = uci_to_move(parts[1])
    if to not in legal_moves(state, *frm):
        raise RuntimeError(f"Stockfish returned an illegal move: {parts[1]}")
    if is_promotion_move(state, frm, to) != (promotion is not None):
        raise RuntimeError(f"Stockfish returned an invalid promotion: {parts[1]}")
    return frm, to, promotion


def stop_engine(engine):
    try:
        send_to_engine(engine, "quit")
    except OSError:
        pass
    try:
        engine.wait(timeout=2)
    except subprocess.TimeoutExpired:
        engine.kill()
        engine.wait()
    try:
        engine.stdin.close()
    except OSError:
        pass
    finally:
        engine.stdout.close()


# ============================================================
# INPUT
# ============================================================

POSIX_ARROWS = {b"A": "UP", b"B": "DOWN", b"C": "RIGHT", b"D": "LEFT"}


def key_ready():
    if msvcrt:
        return msvcrt.kbhit()
    return bool(select.select([sys.stdin.fileno()], [], [], 0)[0])


def enter_raw_mode():
    """Put a POSIX terminal in cbreak mode; returns the saved settings (None on Windows/non-tty)."""
    if msvcrt or not sys.stdin.isatty():
        return None
    saved = termios.tcgetattr(sys.stdin.fileno())
    tty.setcbreak(sys.stdin.fileno())
    return saved


def leave_raw_mode(saved):
    if saved is not None:
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, saved)


def read_windows_token():
    key = msvcrt.getch()
    if key in (b"\xe0", b"\x00"):
        return {b"H": "UP", b"P": "DOWN", b"K": "LEFT", b"M": "RIGHT"}.get(msvcrt.getch())
    return {b"\r": "ENTER", b"\x1b": "ESC", b"\x08": "BACKSPACE", b"\x03": "CTRL_C"}.get(
        key, key.decode("ascii", errors="ignore") or None
    )


def read_posix_token():
    fd = sys.stdin.fileno()
    key = os.read(fd, 1)
    if key == b"\x1b":
        sequence = b""
        while len(sequence) < 8 and select.select([fd], [], [], 0.02)[0]:
            sequence += os.read(fd, 1)
            if sequence[-1:].isalpha() or sequence[-1:] == b"~":
                break
        if not sequence:
            return "ESC"
        return POSIX_ARROWS.get(sequence[-1:]) if sequence[:1] in (b"[", b"O") else None
    return {b"\r": "ENTER", b"\n": "ENTER", b"\x7f": "BACKSPACE", b"\x08": "BACKSPACE"}.get(
        key, key.decode("ascii", errors="ignore") or None
    )


def read_token(idle=None):
    """Return a key name (UP, DOWN, LEFT, RIGHT, ENTER, ESC, BACKSPACE, CTRL_C) or one typed character.

    `idle` is called repeatedly while waiting, so callers can keep other work going.
    """
    while idle and not key_ready():
        idle()
        time.sleep(0.03)
    return read_windows_token() if msvcrt else read_posix_token()


def read_key(idle=None):
    """Return a name: UP, DOWN, LEFT, RIGHT, SPACE, ENTER, ESC, QUIT or an upper-case letter."""
    token = read_token(idle)
    if token == " ":
        return "SPACE"
    if token in ("q", "Q", "CTRL_C"):
        return "QUIT"
    if token and len(token) == 1:
        return token.upper()
    return token


def edit_text(draw, max_len, initial="", upper=False, idle=None):
    """Let the user type a line. `draw(text)` renders it. Returns the text or None if cancelled."""
    text = initial
    while True:
        draw(text)
        token = read_token(idle)
        if token == "ENTER":
            return text.strip()
        if token in ("ESC", "CTRL_C"):
            return None
        if token == "BACKSPACE":
            text = text[:-1]
        elif token and len(token) == 1 and token.isprintable() and len(text) < max_len:
            text += token.upper() if upper else token


# ============================================================
# RENDERING
# ============================================================

def terminal_width():
    try:
        return os.get_terminal_size().columns
    except OSError:
        return 80


def terminal_height():
    try:
        return os.get_terminal_size().lines
    except OSError:
        return 30


def clear_screen():
    print("\033[2J\033[H", end="", flush=True)


def draw_frame(lines):
    """Redraw in place (no full clear) to avoid flicker."""
    body = "".join(line + "\033[K\n" for line in lines)
    print("\033[H" + body + "\033[J", end="", flush=True)


def center(text, visible_width, width):
    return " " * max(0, (width - visible_width) // 2) + text


def build_logo(width):
    letters = [LETTER_ART[letter] for letter in "CHESSCORE"]
    rows = ["".join(letter[i].ljust(len(letter[0])) for letter in letters) for i in range(6)]
    if len(rows[0]) > width:
        return ["C H E S S C O R E"]
    return rows


def last_move_squares(game):
    if not game["history"]:
        return set()
    try:
        frm, to, _ = uci_to_move(game["history"][-1])
    except (ValueError, IndexError):
        return set()
    return {frm, to}


def render_cell(game, ui, row, col, last_move=(), check_square=None, width=3, blank=False):
    piece = game["board"][row][col]
    square = (row, col)
    glyph = PIECE_GLYPHS[piece] if piece else " "
    color = (WHITE if piece[0] == "w" else YELLOW) if piece else ""

    if square in ui["targets"]:
        glyph, color = ("✕", RED) if ui["targets"][square] else ("○", GREEN)

    if square == ui["selected"]:
        left, right = "{", "}"
    elif square == ui["cursor"]:
        left, right = "[", "]"
    else:
        left, right = " ", " "

    if square == check_square:
        background = CHECK_SQUARE
    elif square in last_move:
        background = LAST_MOVE_SQUARE
    else:
        background = LIGHT_SQUARE if (row + col) % 2 == 0 else DARK_SQUARE
    if blank:
        return f"{background}{' ' * width}{RESET}"
    padding = " " * ((width - 3) // 2)
    return f"{background}{padding}{GREEN}{left}{color}{glyph}{GREEN}{right}{padding}{RESET}"


def build_board_lines(game, ui, flip=False, cell_width=3, cell_height=1):
    border = lambda text: f"{DIM_GREEN}{text}{RESET}"
    rows = range(7, -1, -1) if flip else range(8)
    cols = range(7, -1, -1) if flip else range(8)
    files = FILES[::-1] if flip else FILES
    header = border(" " * (4 + cell_width // 2) + (" " * cell_width).join(files))
    last_move = last_move_squares(game)
    check_square = find_king(game["board"], game["turn"]) if is_in_check(game["board"], game["turn"]) else None
    horizontal = "─" * cell_width
    lines = [header, border("   ┌" + (horizontal + "┬") * 7 + horizontal + "┐")]

    for position, row in enumerate(rows):
        for line in range(cell_height):
            cells = "".join(
                render_cell(game, ui, row, col, last_move, check_square, cell_width, blank=line > 0) + border("│")
                for col in cols
            )
            rank = str(8 - row) if line == 0 else " "
            lines.append(border(f" {rank} │") + cells + border(f" {rank}"))
        if position < 7:
            lines.append(border("   ├" + (horizontal + "┼") * 7 + horizontal + "┤"))

    lines.append(border("   └" + (horizontal + "┴") * 7 + horizontal + "┘"))
    lines.append(header)
    return lines


PANEL_ROWS = 17
BOARD_WIDTH, PANEL_GAP, PANEL_WIDTH = 38, 4, 30
MIN_WIDTH = BOARD_WIDTH + PANEL_GAP + PANEL_WIDTH


def color_name(color):
    return "White" if color == "w" else "Black"


def colorize(text, tone="info"):
    return f"{TONES.get(tone, WHITE)}{text}{RESET}" if text else ""


def turn_line(game, status):
    name = color_name(game["turn"])
    glyph = PIECE_GLYPHS[game["turn"] + "k"]
    if status == "checkmate":
        return f"{RED}CHECKMATE{RESET}  {color_name(opponent(game['turn']))} wins"
    if status == "stalemate":
        return f"{YELLOW}STALEMATE{RESET}  it's a draw"
    if status == "draw":
        return f"{YELLOW}DRAW{RESET}  {draw_reason(game)}"
    check = f"  {RED}CHECK!{RESET}" if status == "check" else ""
    return f"{GREEN}{glyph} {name} to move{RESET}{check}"


def captured_line(game, color):
    glyphs = "".join(PIECE_GLYPHS[opponent(color) + kind] for kind in captured_pieces(game["board"], color))
    lead = material(game["board"], color) - material(game["board"], opponent(color))
    bonus = f"  {GREEN}+{lead}{RESET}" if lead > 0 else ""
    return f"{DIM_GREEN}{color_name(color):<6}{RESET}{glyphs}{bonus}"


def move_rows(game, rows):
    sans = san_history(game["history"])
    if not sans:
        return [f"{DIM_GREEN}  No moves yet{RESET}"] + [""] * (rows - 1)
    pairs = [(i // 2 + 1, sans[i], sans[i + 1] if i + 1 < len(sans) else "") for i in range(0, len(sans), 2)]
    lines = [f"{DIM_GREEN}{number:>3}.{RESET} {white:<8}{black}" for number, white, black in pairs[-rows:]]
    return lines + [""] * (rows - len(lines))


def build_panel(game, ui, top):
    """Side panel: `top` lines, the move list, then details about the square under the cursor."""
    rows = max(2, PANEL_ROWS - len(top) - 5)
    row, col = ui["cursor"]
    piece = game["board"][row][col]
    what = f"{color_name(piece[0])} {PIECE_NAMES[piece[1]]}" if piece else "empty square"
    selected = f"Selected: {square_name(*ui['selected'])}" if ui["selected"] else ""
    return (
        top + ["", f"{GREEN}Moves{RESET}"] + move_rows(game, rows)
        + ["", f"{DIM_GREEN}{square_name(row, col)}  {what}{RESET}", f"{DIM_GREEN}{selected}{RESET}"]
    )


def build_info_lines(game, ui, status):
    top = [turn_line(game, status), "", captured_line(game, "w"), captured_line(game, "b")]
    return build_panel(game, ui, top)


def game_hint(ui):
    if ui["selected"]:
        return "Space  move here      Esc  cancel      ○ move   ✕ capture"
    return "↑↓←→ move    Space select    F flip    S save    L load    Q quit"


def draw_notice(lines):
    width = terminal_width()
    top = [""] * max(0, (terminal_height() - len(lines)) // 2)
    draw_frame(top + [center(line, len(re.sub(r"\033\[[0-9;]*m", "", line)), width) for line in lines])


def render_game(game, ui, status, panel=None, flip=None, footer=(), message=None, tone=None, hint=None):
    width, height = terminal_width(), terminal_height()
    flip = ui.get("flip", False) if flip is None else flip
    cell_height = 2 if width >= MIN_WIDTH and height >= 30 + len(footer) else 1
    cell_width = 5 if cell_height == 2 and width >= MIN_WIDTH + 16 else 3
    board_lines = build_board_lines(game, ui, flip, cell_width, cell_height)
    info_lines = panel if panel is not None else build_info_lines(game, ui, status)
    layout_width = MIN_WIDTH + 8 * (cell_width - 3)

    needed = len(board_lines) + 3 + len(footer)
    if width < MIN_WIDTH or height < needed:
        draw_notice([
            f"{YELLOW}Your terminal window is too small.{RESET}",
            f"Please enlarge it to at least {MIN_WIDTH} x {needed}  (now {width} x {height}).",
        ])
        return

    pad = " " * ((width - layout_width) // 2)
    show_title = height >= needed + 2
    lines = [""] * max(0, (height - needed - (2 if show_title else 0)) // 2)
    if show_title:
        title = "♔  C H E S S C O R E  ♚"
        lines += [pad + " " * ((layout_width - len(title)) // 2) + f"{GREEN}{title}{RESET}", ""]
    for index, board_line in enumerate(board_lines):
        info_index = index - 1
        info = " " * PANEL_GAP + info_lines[info_index] if 0 <= info_index < len(info_lines) else ""
        lines.append(pad + board_line + info)
    text = ui["message"] if message is None else message
    lines.append("")
    lines.append(pad + colorize(text, tone or ui.get("tone", "info")))
    shown_hint = "" if ui.get("modal") else (game_hint(ui) if hint is None else hint)
    lines.append(pad + f"{DIM_GREEN}{shown_hint}{RESET}")
    lines += [pad + line for line in footer]
    draw_frame(lines)


MENU_HEIGHT = 18  # logo, subtitle, up to 6 items and the hint; fixed so boot and menu line up
MENU_HINT = "↑↓ move   Enter choose   Q quit"
SELECTED_BAR = "\033[30;42m"  # black text on a green bar


def menu_top_margin():
    return max(0, (terminal_height() - MENU_HEIGHT) // 2)


def build_menu_box(items, index, width):
    """List the options with the selected one highlighted as a bar. All bars share one width."""
    inner = min(max(max(len(label) for label, _ in items) + 8, 26), max(10, width - 4))
    rows = []
    for i, (label, _) in enumerate(items):
        label = label if len(label) <= inner - 6 else label[: inner - 7] + "…"
        if i == index:
            rows.append(f"{SELECTED_BAR}{('  ▶ ' + label).ljust(inner)}{RESET}")
        else:
            rows.append(f"{DIM_GREEN}{('    ' + label).ljust(inner)}{RESET}")
    return [center(row, inner, width) for row in rows]


def build_menu_lines(items, index, width, subtitle, hint=MENU_HINT):
    logo = build_logo(width)
    lines = [""] * menu_top_margin()
    lines += [center(f"{GREEN}{row}{RESET}", len(row), width) for row in logo]
    lines += ["", center(f"{GREEN}{subtitle}{RESET}", len(subtitle), width), ""]
    lines += build_menu_box(items, index, width)
    lines += ["", center(f"{DIM_GREEN}{hint}{RESET}", len(hint), width)]
    return lines


def build_prompt_lines(title, text, hint):
    width = terminal_width()
    lines = [""] * max(0, (terminal_height() - 5) // 2)
    shown = f"> {text}_"
    lines += [center(f"{GREEN}{title}{RESET}", len(title), width), ""]
    lines += [center(f"{GREEN}{shown}{RESET}", len(shown), width), ""]
    lines += [center(f"{DIM_GREEN}{hint}{RESET}", len(hint), width)]
    return lines


# ============================================================
# BOOT SCREEN AND MENU
# ============================================================

SUBTITLE = "T E R M I N A L   E D I T I O N"


def boot():
    clear_screen()
    width = terminal_width()
    skip = False

    def type_out(text, delay):
        nonlocal skip
        for char in text:
            print(f"{GREEN}{char}{RESET}", end="", flush=True)
            if char != " " and not skip:
                time.sleep(delay)
                if key_ready():
                    read_token()
                    skip = True

    print("\n" * menu_top_margin(), end="")
    for row in build_logo(width):
        print(" " * max(0, (width - len(row)) // 2), end="")
        type_out(row, 0.002)
        print()

    print()
    print(" " * ((width - len(SUBTITLE)) // 2), end="")
    type_out(SUBTITLE, 0.03)
    time.sleep(0.4)


def choose_menu(items, cancel_value, cancel_keys=("QUIT",), hint=MENU_HINT, subtitle=SUBTITLE):
    """Show a vertical menu. Returns the chosen item's value, or cancel_value on a cancel key."""
    index = 0
    clear_screen()

    while True:
        draw_frame(build_menu_lines(items, index, terminal_width(), subtitle, hint))
        key = read_key()
        if key == "UP":
            index = (index - 1) % len(items)
        elif key == "DOWN":
            index = (index + 1) % len(items)
        elif key == "ENTER":
            return items[index][1]
        elif key in cancel_keys:
            return cancel_value


def run_menu(has_save):
    items = []
    if has_save:
        items.append(("Continue saved game", "continue"))
    items += [
        ("New game (two players, one screen)", "new"),
        ("Play against computer", "computer"),
        ("Play online", "online"), ("Quit", "quit"),
    ]
    return choose_menu(items, "quit")


# ============================================================
# GAME LOOP
# ============================================================

def new_ui(color="w"):
    return {
        "cursor": (6, 4) if color == "w" else (1, 4), "selected": None, "targets": {},
        "message": "", "tone": "info", "flip": False, "modal": False,
    }


def say(ui, text, tone="info"):
    ui["message"] = text
    ui["tone"] = tone


CURSOR_STEPS = {"UP": (-1, 0), "DOWN": (1, 0), "LEFT": (0, -1), "RIGHT": (0, 1)}


def move_cursor(cursor, key, flip=False):
    """Move the cursor one square in the direction it appears on screen."""
    d_row, d_col = CURSOR_STEPS[key]
    if flip:
        d_row, d_col = -d_row, -d_col
    return (min(7, max(0, cursor[0] + d_row)), min(7, max(0, cursor[1] + d_col)))


def clear_selection(ui):
    ui["selected"] = None
    ui["targets"] = {}


def select_square(game, ui, square):
    moves = legal_moves(game, square[0], square[1])
    if not moves:
        say(ui, "That piece has no legal moves right now.", "warn")
        return
    ui["selected"] = square
    ui["targets"] = {target: is_capture_move(game, square, target) for target in moves}


def choose_promotion(game, ui, redraw=None, idle=None):
    """Let the player pick a piece with the arrow keys. Returns 'q'/'r'/'b'/'n' or None if cancelled."""
    index = 0
    color = game["turn"]
    redraw = redraw or (lambda: render_game(game, ui, game_status(game)))
    ui["modal"] = True
    try:
        while True:
            options = "  ".join(
                f"[{PIECE_GLYPHS[color + kind]}]" if i == index else f" {PIECE_GLYPHS[color + kind]} "
                for i, kind in enumerate(PROMOTION_CHOICES)
            )
            say(ui, f"Promote your pawn to:  {options}      ←→ choose   Enter confirm   Esc cancel", "ok")
            redraw()
            key = read_key(idle)
            if key == "LEFT":
                index = (index - 1) % len(PROMOTION_CHOICES)
            elif key == "RIGHT":
                index = (index + 1) % len(PROMOTION_CHOICES)
            elif key in ("ENTER", "SPACE"):
                return PROMOTION_CHOICES[index]
            elif key == "ESC":
                return None
    finally:
        ui["modal"] = False
        say(ui, "")


def handle_space(game, ui, submit=None, redraw=None, idle=None):
    """Select a piece or play a move. With `submit` (online), the move is handed over instead of applied."""
    cursor = ui["cursor"]
    piece = game["board"][cursor[0]][cursor[1]]
    own_piece = bool(piece) and piece[0] == game["turn"]

    if ui["selected"] is None:
        if own_piece:
            select_square(game, ui, cursor)
        else:
            say(ui, f"Pick one of your own pieces. It's {color_name(game['turn'])}'s turn.", "warn")
        return game

    if cursor == ui["selected"]:
        clear_selection(ui)
    elif cursor in ui["targets"]:
        frm = ui["selected"]
        promotion = None
        if is_promotion_move(game, frm, cursor):
            promotion = choose_promotion(game, ui, redraw, idle)
            if promotion is None:
                return game
        clear_selection(ui)
        if submit:
            submit(frm, cursor, promotion)
            return game
        game = apply_move(game, frm, cursor, promotion or "q")
    elif own_piece:
        clear_selection(ui)
        select_square(game, ui, cursor)
    else:
        say(ui, "That move isn't legal. Choose a ○ or ✕ square, or press Esc to cancel.", "error")
    return game


def confirm_quit(game, ui):
    """Return 'save', 'discard' or None (cancel)."""
    say(ui, "Save the game before quitting?     Y save and quit   N quit without saving   Esc keep playing", "warn")
    ui["modal"] = True
    try:
        render_game(game, ui, game_status(game))
        while True:
            key = read_key()
            if key == "Y":
                return "save"
            if key == "N":
                return "discard"
            if key == "ESC":
                return None
    finally:
        ui["modal"] = False


def end_message(game, status):
    if status == "checkmate":
        return f"Checkmate! {color_name(opponent(game['turn']))} wins.", "ok"
    if status == "stalemate":
        return "Stalemate. Neither side can move, so it's a draw.", "warn"
    return f"Draw by {draw_reason(game)}.", "warn"


def play_game(game):
    """Run one game. Returns 'menu' after the game ends or 'quit' to exit the program."""
    ui = new_ui()
    clear_screen()
    engine = None
    engine_level = None
    try:
        while True:
            status = game_status(game)

            if status in FINAL_STATUSES:
                clear_selection(ui)
                text, tone = end_message(game, status)
                say(ui, f"{text}  Press any key to return to the menu.", tone)
                ui["modal"] = True
                render_game(game, ui, status)
                read_key()
                return "menu"

            level = game.get("computer")
            try:
                if level != engine_level:
                    if engine is not None:
                        stop_engine(engine)
                        engine = None
                    engine_level = level
                    if level:
                        engine = start_engine(stockfish_path())
                        configure_engine(engine, level)
                if level and game["turn"] == "b":
                    say(ui, f"Stockfish ({level}) is thinking...", "info")
                    render_game(game, ui, status)
                    frm, to, promotion = engine_best_move(engine, game, COMPUTER_LEVELS[level][1])
                    game = apply_move(game, frm, to, promotion or "q")
                    clear_selection(ui)
                    say(ui, f"Stockfish played {move_to_uci(frm, to, promotion)}.", "ok")
                    continue
            except (OSError, RuntimeError) as exc:
                clear_selection(ui)
                ui["modal"] = True
                say(ui, f"Stockfish error: {exc}  Y save game; any other key returns to menu.", "error")
                render_game(game, ui, status)
                if read_key() == "Y" and not save_game(game):
                    say(ui, "Couldn't save the game. Check that the folder is writable. Press any key.", "error")
                    render_game(game, ui, status)
                    read_key()
                return "menu"

            render_game(game, ui, status)
            key = read_key()
            say(ui, "")

            if key in CURSOR_STEPS:
                ui["cursor"] = move_cursor(ui["cursor"], key, ui["flip"])
            elif key == "SPACE":
                game = handle_space(game, ui)
            elif key == "ESC":
                clear_selection(ui)
            elif key == "F":
                ui["flip"] = not ui["flip"]
            elif key == "S":
                if save_game(game):
                    say(ui, f"Game saved to {SAVE_FILE}.", "ok")
                else:
                    say(ui, "Couldn't save the game. Check that the folder is writable.", "error")
            elif key == "L":
                loaded = load_game()
                if loaded is None:
                    say(ui, f"No saved game found ({SAVE_FILE} is missing or damaged).", "error")
                else:
                    game = loaded
                    clear_selection(ui)
                    say(ui, "Saved game loaded.", "ok")
            elif key == "QUIT":
                choice = confirm_quit(game, ui)
                say(ui, "")
                if choice == "save" and not save_game(game):
                    say(ui, "Couldn't save the game. Check that the folder is writable.", "error")
                    continue
                if choice:
                    return "quit"
    finally:
        if engine is not None:
            stop_engine(engine)


# ============================================================
# ONLINE PLAY (client)
# ============================================================

ONLINE_FILE = "online.json"
DEFAULT_SERVER = os.environ.get("CHESSCORE_SERVER", "ws://localhost:8765")
RECONNECT_WINDOW = 50  # seconds; the server forfeits a player who stays away for 60
ONLINE_HINT = "↑↓←→ move    Space select    T chat    R resign    D draw    Q leave"
TIME_CONTROLS = [
    ("No clock", (0, 0)), ("3 minutes each", (3, 0)), ("5 minutes each", (5, 0)),
    ("10 minutes each", (10, 0)), ("15 minutes + 10 seconds per move", (15, 10)),
]


def load_server_url():
    try:
        with open(ONLINE_FILE, "r", encoding="utf-8") as file:
            url = json.load(file).get("server")
    except (OSError, ValueError, AttributeError):
        url = None
    return url if isinstance(url, str) and url else DEFAULT_SERVER


def save_server_url(url):
    try:
        with open(ONLINE_FILE, "w", encoding="utf-8") as file:
            json.dump({"server": url}, file, indent=2)
    except OSError:
        pass


class OnlineClient:
    """Background WebSocket connection. Server messages arrive on `events`; drops reconnect with the token."""

    def __init__(self, url, hello):
        from websockets.exceptions import WebSocketException  # imported here so offline play needs no packages
        from websockets.sync.client import connect

        self._connect = connect
        self._errors = (OSError, WebSocketException)
        self.url = url
        self.hello = hello
        self.token = None
        self.events = queue.Queue()
        self.closing = False
        self.ws = None
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()

    def send(self, payload):
        ws = self.ws
        if ws is None:
            return False
        try:
            ws.send(json.dumps(payload))
            return True
        except self._errors:
            return False

    def close(self):
        self.closing = True
        ws = self.ws
        if ws is not None:
            try:
                ws.close()
            except self._errors:
                pass

    def _run(self):
        lost_at = None
        while not self.closing:
            try:
                with self._connect(self.url, open_timeout=5, max_size=65536) as ws:
                    self.ws = ws
                    ws.send(json.dumps({"type": "rejoin", "token": self.token} if self.token else self.hello))
                    for raw in ws:
                        try:
                            message = json.loads(raw)
                        except ValueError:
                            continue
                        if lost_at is not None:
                            lost_at = None
                            self.events.put({"type": "connection", "connected": True})
                        if message.get("type") == "room":
                            self.token = message.get("token", self.token)
                        self.events.put(message)
            except self._errors:
                pass
            self.ws = None
            if self.closing:
                return
            if self.token is None:
                self.events.put({
                    "type": "gave_up",
                    "reason": f"Couldn't connect to {self.url}. Check the address and that the server is running.",
                })
                return
            now = time.monotonic()
            if lost_at is None:
                lost_at = now
                self.events.put({"type": "connection", "connected": False})
            elif now - lost_at > RECONNECT_WINDOW:
                self.events.put({"type": "gave_up", "reason": "Connection lost and couldn't be restored."})
                return
            for _ in range(15):
                if not self.closing:
                    time.sleep(0.1)


def format_clock(seconds):
    seconds = int(seconds + 0.999)
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def result_message(result, color):
    """Return (text, tone) describing how the game ended for the player on `color`."""
    reason = result.get("reason")
    winner = result.get("winner")
    if winner is None:
        text = {
            "stalemate": "Stalemate. Neither side can move, so it's a draw.",
            "agreement": "Draw agreed.",
        }.get(reason, "The game is a draw by rule.")
        return text, "warn"
    if winner == color:
        text = {
            "checkmate": "Checkmate! You win.",
            "resignation": "Your opponent resigned. You win!",
            "timeout": "Your opponent ran out of time. You win!",
            "abandonment": "Your opponent left the game. You win!",
        }.get(reason, "You win!")
        return text, "ok"
    text = {
        "checkmate": "Checkmate. You lose.",
        "resignation": "You resigned.",
        "timeout": "You ran out of time. You lose.",
        "abandonment": "You left the game. You lose.",
    }.get(reason, "You lose.")
    return text, "error"


class OnlineGame:
    """Client-side mirror of a game that the server owns."""

    def __init__(self, client):
        self.client = client
        self.game = new_game()
        self.status = "playing"
        self.ui = new_ui()
        self.color = None
        self.code = None
        self.started = False
        self.result = None
        self.clocks = None
        self.clock_running = None
        self.clock_stamp = time.monotonic()
        self.opponent_connected = False
        self.connected = True
        self.draw_offer = None
        self.chat = []
        self.notice = ""
        self.failed = False
        self.last_draw = 0.0
        self.final_shown = False

    @property
    def flip(self):
        return self.color == "b"

    @property
    def finished(self):
        return self.failed or self.result is not None

    def handle(self, message):
        kind = message.get("type")
        if kind == "room":
            self.code = message.get("code")
            self.color = message.get("color")
            self.ui = new_ui(self.color)
        elif kind == "state":
            self.apply_state(message)
        elif kind == "chat" and isinstance(message.get("text"), str):
            self.chat = (self.chat + [(message.get("from"), message["text"])])[-50:]
        elif kind == "notice":
            self.notice = str(message.get("text", ""))
        elif kind == "error":
            self.notice = str(message.get("message", "Error"))
            self.failed = self.failed or bool(message.get("fatal"))
        elif kind == "connection":
            self.connected = bool(message.get("connected"))
        elif kind == "gave_up":
            self.notice = str(message.get("reason", "Connection lost."))
            self.failed = True

    def apply_state(self, message):
        data = message.get("state")
        if not is_valid_state(data):
            return
        game = state_from_data(data)
        if len(game["history"]) != len(self.game["history"]):
            clear_selection(self.ui)
        self.game = game
        self.status = message.get("status", "playing")
        self.started = bool(message.get("started"))
        self.result = message.get("result")
        self.clocks = message.get("clocks")
        self.clock_running = message.get("running")
        self.clock_stamp = time.monotonic()
        self.opponent_connected = bool(message.get("opponent_connected"))
        self.draw_offer = message.get("draw_offer")
        self.color = message.get("color", self.color)

    def drain(self):
        changed = False
        while True:
            try:
                self.handle(self.client.events.get_nowait())
            except queue.Empty:
                return changed
            changed = True

    def idle(self):
        changed = self.drain()
        ticking = self.clocks and self.clock_running
        if changed or (ticking and time.monotonic() - self.last_draw >= 0.5):
            self.render()

    def clock_for(self, color):
        if not self.clocks:
            return None
        value = self.clocks[color]
        if self.clock_running == color:
            value -= time.monotonic() - self.clock_stamp
        return max(0, value)

    def status_line(self):
        """Return (text, tone) for the message line."""
        if self.ui["message"]:
            return self.ui["message"], self.ui["tone"]
        if self.failed:
            return f"{self.notice}  Press any key to go back.", "error"
        if self.result:
            text, tone = result_message(self.result, self.color)
            return f"{text}  Press any key to go back.", tone
        if not self.connected:
            return "Connection lost. Trying to reconnect...", "warn"
        if self.notice:
            return self.notice, "warn"
        if self.code is None:
            return "Connecting to the server...", "info"
        if not self.started:
            return f"Waiting for an opponent. Share this room code: {self.code}", "ok"
        if self.draw_offer and self.draw_offer != self.color:
            return "Your opponent offers a draw.     D accept   X decline", "warn"
        if self.draw_offer:
            return "Draw offer sent. Waiting for a reply...", "info"
        if self.game["turn"] == self.color:
            return "Your move.", "ok"
        return "Waiting for your opponent's move...", "info"

    def panel(self):
        you = color_name(self.color) if self.color else "-"
        if not self.started:
            opponent = f"{YELLOW}waiting to join{RESET}"
        elif self.opponent_connected:
            opponent = f"{GREEN}online{RESET}"
        else:
            opponent = f"{YELLOW}disconnected{RESET}"
        top = [
            turn_line(self.game, self.status),
            f"{DIM_GREEN}Room{RESET} {self.code or '-'}   {DIM_GREEN}You play{RESET} {you}",
            f"{DIM_GREEN}Opponent{RESET} {opponent}",
        ]
        if self.clocks:
            for color in ("b", "w"):
                mark = f" {GREEN}◀{RESET}" if self.clock_running == color else ""
                top.append(f"{PIECE_GLYPHS[color + 'k']} {color_name(color):<6}{format_clock(self.clock_for(color))}{mark}")
        top += [captured_line(self.game, "w"), captured_line(self.game, "b")]
        return build_panel(self.game, self.ui, top)

    def chat_footer(self):
        width = max(20, terminal_width() - 12)
        lines = []
        for sender, text in self.chat[-3:]:
            mine = sender == self.color
            label = "You" if mine else "Opponent"
            lines.append(f"{GREEN if mine else WHITE}{label}{RESET}{DIM_GREEN}: {text[: width - len(label)]}{RESET}")
        if not lines:
            lines.append(f"{DIM_GREEN}No chat messages yet. Press T to write one.{RESET}")
        return lines + [""] * (3 - len(lines))

    def render(self):
        self.last_draw = time.monotonic()
        self.final_shown = self.finished
        text, tone = self.status_line()
        hint = game_hint(self.ui) if self.ui["selected"] else ONLINE_HINT
        render_game(
            self.game, self.ui, self.status, panel=self.panel(), flip=self.flip,
            footer=self.chat_footer(), message=text, tone=tone, hint=hint,
        )

    def submit_move(self, frm, to, promotion):
        if not self.client.send({"type": "move", "uci": move_to_uci(frm, to, promotion)}):
            self.notice = "Not connected. Your move wasn't sent."

    def confirm(self, prompt):
        say(self.ui, f"{prompt}     Y yes   N no", "warn")
        self.ui["modal"] = True
        try:
            while True:
                self.render()
                key = read_key(self.idle)
                if key in ("Y", "N", "ESC"):
                    return key == "Y"
        finally:
            self.ui["modal"] = False
            say(self.ui, "")

    def type_chat(self):
        def draw(text):
            say(self.ui, f"Message: {text}_     Enter send   Esc cancel", "ok")
            self.render()

        self.ui["modal"] = True
        try:
            text = edit_text(draw, 200, idle=self.idle)
        finally:
            self.ui["modal"] = False
            say(self.ui, "")
        if text:
            self.client.send({"type": "chat", "text": text})


def play_online(client):
    """Run an online game until it ends or the player leaves."""
    session = OnlineGame(client)
    clear_screen()

    while True:
        session.drain()
        session.render()
        was_final = session.final_shown
        key = read_key(session.idle)
        if was_final:
            return
        session.notice = ""
        game, ui = session.game, session.ui
        say(ui, "")

        if key in CURSOR_STEPS:
            ui["cursor"] = move_cursor(ui["cursor"], key, session.flip)
        elif key == "SPACE":
            if not session.started:
                session.notice = "Wait for an opponent to join before moving."
            elif game["turn"] != session.color or session.result:
                session.notice = "It's your opponent's turn."
            else:
                handle_space(game, ui, session.submit_move, session.render, session.idle)
        elif key == "ESC":
            clear_selection(ui)
        elif key == "T":
            session.type_chat()
        elif key == "R" and session.started:
            if session.confirm("Resign this game?"):
                client.send({"type": "resign"})
        elif key == "D" and session.started:
            client.send({"type": "draw_accept" if session.draw_offer not in (None, session.color) else "draw_offer"})
        elif key == "X" and session.started:
            client.send({"type": "draw_decline"})
        elif key == "QUIT":
            if not session.started:
                return
            if session.confirm("Leave the game? This counts as a resignation."):
                client.send({"type": "resign"})
                return


def show_message(text, hint="Press any key"):
    width = terminal_width()
    lines = [""] * max(0, (terminal_height() - 3) // 2)
    lines += [center(f"{GREEN}{text}{RESET}", len(text), width), ""]
    lines += [center(f"{DIM_GREEN}{hint}{RESET}", len(hint), width)]
    draw_frame(lines)
    read_key()


def start_online(hello, url):
    try:
        client = OnlineClient(url, hello)
    except ImportError:
        show_message("Online play needs the 'websockets' package. Install it with:  pip install websockets")
        return
    client.start()
    try:
        play_online(client)
    finally:
        client.close()


ONLINE_SUBTITLE = "P L A Y   O N L I N E"
MENU_BACK_HINT = "↑↓ move   Enter choose   Esc back"


def run_online_menu():
    while True:
        url = load_server_url()
        items = [
            ("Create a room", "create"), ("Join a room", "join"),
            (f"Server: {url}", "server"), ("Back", "back"),
        ]
        choice = choose_menu(items, "back", ("QUIT", "ESC"), MENU_BACK_HINT, ONLINE_SUBTITLE)

        if choice == "create":
            control = choose_menu(
                [(label, label) for label, _ in TIME_CONTROLS] + [("Back", "back")],
                "back", ("QUIT", "ESC"), MENU_BACK_HINT, "T I M E   C O N T R O L",
            )
            if control != "back":
                minutes, increment = dict(TIME_CONTROLS)[control]
                start_online({"type": "create", "minutes": minutes, "increment": increment, "color": "random"}, url)
        elif choice == "join":
            draw = lambda text: draw_frame(build_prompt_lines(
                "Enter the room code your opponent gave you", text, "Enter join   Esc back"))
            code = edit_text(draw, 8, upper=True)
            if code:
                start_online({"type": "join", "code": code}, url)
        elif choice == "server":
            draw = lambda text: draw_frame(build_prompt_lines(
                "Server address (starts with ws:// or wss://)", text, "Enter save   Esc back"))
            new_url = edit_text(draw, 120, initial=url)
            if new_url and new_url.startswith(("ws://", "wss://")):
                save_server_url(new_url)
            elif new_url:
                show_message("That address has to start with ws:// or wss://")
        else:
            return


# ============================================================
# MAIN
# ============================================================

def crashing_outro():
    for frame in ("CHESSCORE", "CH3SSC0RE", "CHESSC0RE", "CH3SSC0RE"):
        print(f"\r{RED}  {frame}{RESET}", end="", flush=True)
        time.sleep(0.03)
    print()

    messages = [
        (RED, "[  12.481] chesscore[1]: ERROR: server heartbeat timeout"),
        (YELLOW, "[  12.512] netmon[342]: packet loss exceeded threshold"),
        (RED, "[  12.482] systemd[1]: chesscore.service: Main process exited, status=1/FAILURE"),
        (RED, "[  12.517] postgres[812]: FATAL: connection reset by peer"),
        (BLUE, "[  12.520] kernel: eth0 link up at 1000Mbps"),
        (YELLOW, "[FAILED] Failed to start ChessCore game service."),
        (RED, "[  12.483] systemd[1]: chesscore.service: Failed with result 'exit-code'."),
        (YELLOW, "[  12.531] scheduler[119]: delayed task execution detected"),
        (RED, "[  12.545] authd[241]: token validation failure"),
        (RED, "[  12.601] chesscore[1]: matchmaking backend unreachable"),
        (BLUE, "[  12.614] cachemgr[88]: cache refresh complete"),
        (YELLOW, "[  12.622] monitor[19]: CPU usage exceeded 85%"),
        (RED, "[  12.628] redis[401]: failed to write snapshot"),
        (BLUE, "[  12.635] dns-resolver[72]: updated nameserver cache"),
        (RED, "[  12.643] chesscore[1]: player session synchronization failed"),
        (YELLOW, "[  12.650] kernel: high latency interrupt response"),
        (RED, "[  12.661] systemd[1]: dependency failed for chesscore.service"),
        (BLUE, "[  12.674] updater[220]: checking for updates"),
        (YELLOW, "[  12.688] watchdog[9]: process restart attempt 2"),
        (RED, "[  12.702] postgres[812]: unexpected shutdown detected"),
        (BLUE, "[  12.719] inventory[33]: assets synchronized"),
        (RED, "[  12.734] chesscore[1]: game state replication timeout"),
        (YELLOW, "[  12.748] storage[155]: disk queue depth warning"),
        (BLUE, "[  12.761] containerd[201]: image pull complete"),
        (RED, "[  12.775] broker[54]: message delivery failed"),
        (YELLOW, "[  12.791] monitor[19]: memory pressure detected"),
        (RED, "[  12.804] chesscore[1]: opening-book service unavailable"),
        (BLUE, "[  12.812] telemetry[16]: telemetry batch sent"),
        (RED, "[  12.826] nginx[91]: upstream timed out"),
        (YELLOW, "[  12.839] firewall[77]: excessive connection attempts"),
        (RED, "[  12.851] authd[241]: user credential lookup failed"),
        (BLUE, "[  12.867] kernel: scheduler tick restored"),
        (RED, "[  12.880] chesscore[1]: ELO ranking service timeout"),
        (YELLOW, "[  12.896] backupd[41]: backup window exceeded"),
        (RED, "[  12.911] systemd[1]: chesscore.service: Start request repeated too quickly"),
        (BLUE, "[  12.927] api-gateway[128]: route cache rebuilt"),
        (RED, "[  12.943] dbproxy[63]: failed to acquire lock"),
        (YELLOW, "[  12.958] monitor[19]: network jitter warning"),
        (RED, "[  12.973] chesscore[1]: failed to initialize board engine"),
        (BLUE, "[  12.988] metrics[202]: export successful"),
        (RED, "[  13.004] redis[401]: replication link down"),
        (YELLOW, "[  13.018] scheduler[119]: clock drift detected"),
        (RED, "[  13.035] chesscore[1]: tournament coordinator unavailable"),
        (BLUE, "[  13.049] netmon[342]: route convergence complete"),
        (RED, "[  13.067] systemd[1]: Unit chesscore.service entered failed state"),
        (YELLOW, "[  13.085] kernel: soft lockup warning"),
        (RED, "[  13.098] postgres[812]: checkpoint write failure"),
        (BLUE, "[  13.111] orchestrator[87]: scaling event completed"),
        (RED, "[  13.127] chesscore[1]: websocket initialization failed"),
        (YELLOW, "[  13.143] monitor[19]: thread starvation suspected"),
        (RED, "[  13.157] broker[54]: queue overflow"),
        (BLUE, "[  13.171] cachemgr[88]: cache warmup complete"),
        (RED, "[  13.185] chesscore[1]: failed to load opening database"),
        (YELLOW, "[  13.202] conntrack[61]: nearing table capacity"),
        (RED, "[  13.216] authd[241]: session key mismatch"),
        (BLUE, "[  13.229] telemetry[16]: diagnostics uploaded"),
        (RED, "[  13.247] nginx[91]: upstream connection refused"),
        (YELLOW, "[  13.262] kernel: packet reassembly timeout"),
        (RED, "[  13.278] chesscore[1]: move validation service crashed"),
        (BLUE, "[  13.293] updater[220]: repository metadata refreshed"),
        (RED, "[  13.309] redis[401]: cluster slot unavailable"),
        (YELLOW, "[  13.325] watchdog[9]: restart frequency threshold exceeded"),
        (RED, "[  13.341] postgres[812]: relation corruption detected"),
        (BLUE, "[  13.358] inventory[33]: state verification complete"),
        (RED, "[  13.375] chesscore[1]: clock synchronization failure"),
        (YELLOW, "[  13.389] monitor[19]: paging activity elevated"),
        (RED, "[  13.407] dbproxy[63]: transaction rollback failure"),
        (BLUE, "[  13.422] api-gateway[128]: endpoint cache refreshed"),
        (RED, "[  13.437] chesscore[1]: analysis engine timeout"),
        (YELLOW, "[  13.453] storage[155]: filesystem latency warning"),
        (RED, "[  13.469] systemd[1]: Failed to restart chesscore.service"),
        (BLUE, "[  13.485] kernel: cpu frequency scaling restored"),
        (RED, "[  13.501] broker[54]: broker cluster partition detected"),
        (YELLOW, "[  13.516] netmon[342]: asymmetric routing observed"),
        (RED, "[  13.532] chesscore[1]: matchmaking queue corruption detected"),
        (BLUE, "[  13.548] metrics[202]: collector synchronized"),
        (RED, "[  13.564] authd[241]: kerberos negotiation failure"),
        (YELLOW, "[  13.578] scheduler[119]: timing variance alert"),
        (RED, "[  13.593] postgres[812]: replication lag critical"),
        (BLUE, "[  13.611] containerd[201]: cleanup completed"),
        (RED, "[  13.625] chesscore[1]: backend registry unavailable"),
        (YELLOW, "[  13.641] monitor[19]: load average threshold exceeded"),
        (RED, "[  13.657] nginx[91]: worker process crashed"),
        (BLUE, "[  13.673] telemetry[16]: service heartbeat restored"),
        (RED, "[  13.688] chesscore[1]: failed to bind control socket"),
        (YELLOW, "[  13.704] firewall[77]: rule synchronization delayed"),
        (RED, "[  13.719] systemd[1]: chesscore.service: Failed with result 'exit-code'."),
        (BLUE, "[  13.737] kernel: network stack recovered"),
        (RED, "[  13.751] redis[401]: master election failed"),
        (YELLOW, "[  13.768] monitor[19]: process eviction imminent"),
        (RED, "[  13.784] chesscore[1]: persistent storage unavailable"),
        (BLUE, "[  13.799] updater[220]: update scan complete"),
        (RED, "[  13.815] dbproxy[63]: query execution timeout"),
        (YELLOW, "[  13.831] watchdog[9]: service instability detected"),
        (RED, "[  13.846] chesscore[1]: cluster registration failed"),
        (BLUE, "[  13.862] api-gateway[128]: health check passed"),
        (RED, "[  13.879] postgres[812]: WAL archive failure"),
        (YELLOW, "[  13.894] kernel: buffer exhaustion warning"),
        (RED, "[  13.911] chesscore[1]: ERROR: server heartbeat timeout"),
        (RED, "[  13.927] systemd[1]: chesscore.service: Main process exited, status=1/FAILURE"),
        (YELLOW, "[FAILED] Failed to start ChessCore game service."),
        (RED, "[  13.943] systemd[1]: chesscore.service: Failed with result 'exit-code'.")
    ]
    for color, message in messages:
        print(f"{color}{message}{RESET}", flush=True)
        time.sleep(0.02)


def main():
    if msvcrt:
        os.system("")  # enables ANSI escape sequences in the Windows console
    saved_terminal = enter_raw_mode()
    print("\033[?25l", end="")
    normal_exit = False
    try:
        boot()
        while True:
            saved = load_game()
            choice = run_menu(saved is not None)
            if choice == "quit":
                normal_exit = True
                break
            if choice == "online":
                run_online_menu()
                continue
            game = saved if choice == "continue" else new_game()
            if choice == "computer":
                level = choose_menu(
                    [("Easy", "easy"), ("Intermediate", "intermediate"), ("Hard", "hard"), ("Back", None)],
                    None, cancel_keys=("QUIT", "ESC"),
                    hint="↑↓ move   Enter choose   Esc back", subtitle="CHOOSE DIFFICULTY",
                )
                if level is None:
                    continue
                game["computer"] = level
            if play_game(game) == "quit":
                normal_exit = True
                break
    finally:
        leave_raw_mode(saved_terminal)
        print("\033[?25h" + RESET, end="")
        clear_screen()
        if normal_exit:
            crashing_outro()
        else:
            print("Session terminated.")


if __name__ == "__main__":
    main()
