"""ChessCore relay server: authoritative online chess rooms over WebSockets.

The server validates every move with the rules in ascii_chess.py, keeps the clocks,
and relays chat. Clients are never trusted.
"""

import asyncio
import json
import logging
import os
import secrets
import signal
import time
from http import HTTPStatus

from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

import ascii_chess as ac

GRACE_SECONDS = 60      # how long a disconnected player may stay away
WAIT_TTL = 600          # how long a room may wait for its second player
END_TTL = 300           # how long a finished room stays around
TICK = 0.5              # watcher interval
MAX_ROOMS = 500
MAX_CHAT_LENGTH = 200
CHAT_HISTORY = 50
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 5

log = logging.getLogger("chesscore")
ROOMS = {}
TOKENS = {}


def other(color):
    return ac.opponent(color)


class Seat:
    def __init__(self):
        self.token = secrets.token_urlsafe(16)
        self.ws = None
        self.left_at = None
        self.last_chat = 0.0


class Room:
    def __init__(self, code, minutes, increment):
        self.code = code
        self.game = ac.new_game()
        self.seats = {}
        self.timed = minutes > 0
        self.increment = increment
        self.minutes = minutes
        self.clock = {"w": minutes * 60.0, "b": minutes * 60.0}
        self.turn_started = None
        self.started = False
        self.result = None
        self.ended_at = None
        self.draw_offer = None
        self.chat = []
        self.created = time.monotonic()
        self.task = None

    def running_color(self):
        if self.timed and self.started and self.result is None:
            return self.game["turn"]
        return None

    def remaining(self, color):
        value = self.clock[color]
        if self.running_color() == color:
            value -= time.monotonic() - self.turn_started
        return max(0.0, value)

    def settle_clock(self):
        running = self.running_color()
        if running:
            now = time.monotonic()
            self.clock[running] -= now - self.turn_started
            self.turn_started = now

    def finish(self, winner, reason):
        self.settle_clock()
        self.result = {"winner": winner, "reason": reason}
        self.ended_at = time.monotonic()
        self.draw_offer = None

    def state_message(self, color):
        opponent_seat = self.seats.get(other(color))
        return {
            "type": "state",
            "color": color,
            "state": self.game,
            "status": ac.game_status(self.game),
            "started": self.started,
            "clocks": {c: self.remaining(c) for c in "wb"} if self.timed else None,
            "running": self.running_color(),
            "opponent_connected": bool(opponent_seat and opponent_seat.ws),
            "draw_offer": self.draw_offer,
            "result": self.result,
        }


async def send(ws, payload):
    try:
        await ws.send(json.dumps(payload))
    except ConnectionClosed:
        pass


async def broadcast_state(room):
    for color, seat in list(room.seats.items()):
        if seat.ws:
            await send(seat.ws, room.state_message(color))


async def send_to_color(room, color, payload):
    seat = room.seats.get(color)
    if seat and seat.ws:
        await send(seat.ws, payload)


def room_message(room, color):
    return {
        "type": "room", "code": room.code, "color": color, "token": room.seats[color].token,
        "minutes": room.minutes, "increment": room.increment,
    }


def fatal(text):
    return {"type": "error", "message": text, "fatal": True}


def new_code():
    while True:
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
        if code not in ROOMS:
            return code


def clamp_int(value, low, high):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        return None
    return value


async def create_room(ws, msg):
    minutes = clamp_int(msg.get("minutes", 0), 0, 180)
    increment = clamp_int(msg.get("increment", 0), 0, 60)
    if minutes is None or increment is None:
        await send(ws, fatal("Invalid time control."))
        return None
    if len(ROOMS) >= MAX_ROOMS:
        await send(ws, fatal("The server is full. Try again later."))
        return None

    requested = msg.get("color")
    color = requested if requested in ("w", "b") else secrets.choice("wb")
    room = Room(new_code(), minutes, increment)
    seat = room.seats[color] = Seat()
    seat.ws = ws
    ROOMS[room.code] = room
    TOKENS[seat.token] = (room, color)
    room.task = asyncio.create_task(watch(room))
    log.info("room %s created (%s min + %s s)", room.code, minutes, increment)
    await send(ws, room_message(room, color))
    await send(ws, room.state_message(color))
    return room, color


async def join_room(ws, msg):
    code = str(msg.get("code", "")).strip().upper()
    room = ROOMS.get(code)
    if room is None or room.result is not None:
        await send(ws, fatal("We couldn't find a room with that code."))
        return None
    if len(room.seats) >= 2:
        await send(ws, fatal("That room already has two players."))
        return None

    color = other(next(iter(room.seats)))
    seat = room.seats[color] = Seat()
    seat.ws = ws
    TOKENS[seat.token] = (room, color)
    room.started = True
    room.turn_started = time.monotonic()
    log.info("room %s started", room.code)
    await send(ws, room_message(room, color))
    await broadcast_state(room)
    return room, color


async def rejoin_room(ws, msg):
    entry = TOKENS.get(msg.get("token"))
    if entry is None:
        await send(ws, fatal("That game has ended or no longer exists."))
        return None
    room, color = entry
    seat = room.seats[color]
    old = seat.ws
    seat.ws = ws
    seat.left_at = None
    if old is not None:
        asyncio.create_task(old.close())
    await send(ws, room_message(room, color))
    for chat_entry in room.chat:
        await send(ws, chat_entry)
    await broadcast_state(room)
    return room, color


async def end_game(room, winner, reason):
    room.finish(winner, reason)
    log.info("room %s over: %s", room.code, room.result)
    await broadcast_state(room)


async def handle_move(room, color, msg):
    if room.result is not None:
        return await send_to_color(room, color, error("The game is already over."))
    if not room.started:
        return await send_to_color(room, color, error("Wait for an opponent to join first."))
    if room.game["turn"] != color:
        return await send_to_color(room, color, error("It's your opponent's turn."))

    move = parse_move(room.game, color, msg.get("uci"))
    if move is None:
        await send_to_color(room, color, error("That move isn't legal."))
        return await send_to_color(room, color, room.state_message(color))
    frm, to, promotion = move

    room.settle_clock()
    if room.timed and room.clock[color] <= 0:
        return await end_game(room, other(color), "timeout")

    room.game = ac.apply_move(room.game, frm, to, promotion or "q")
    if room.timed:
        room.clock[color] += room.increment
        room.turn_started = time.monotonic()
    room.draw_offer = None

    status = ac.game_status(room.game)
    if status == "checkmate":
        await end_game(room, color, "checkmate")
    elif status in ("stalemate", "draw"):
        await end_game(room, None, status)
    else:
        await broadcast_state(room)


def error(text):
    return {"type": "error", "message": text}


def parse_move(game, color, uci):
    """Return (from, to, promotion) if `uci` is a legal move for `color`, else None."""
    if not isinstance(uci, str) or len(uci) not in (4, 5):
        return None
    try:
        frm, to, promotion = ac.uci_to_move(uci)
    except (ValueError, IndexError):
        return None
    if not (ac.in_bounds(*frm) and ac.in_bounds(*to)):
        return None
    piece = game["board"][frm[0]][frm[1]]
    if not piece or piece[0] != color or to not in ac.legal_moves(game, frm[0], frm[1]):
        return None
    if ac.is_promotion_move(game, frm, to):
        return (frm, to, promotion) if promotion in ac.PROMOTION_CHOICES else None
    return (frm, to, None) if promotion is None else None


async def handle_chat(room, color, msg):
    seat = room.seats[color]
    now = time.monotonic()
    text = "".join(ch for ch in str(msg.get("text", "")) if ch.isprintable())[:MAX_CHAT_LENGTH].strip()
    if not text or now - seat.last_chat < 0.5:
        return
    seat.last_chat = now
    entry = {"type": "chat", "from": color, "text": text}
    room.chat = (room.chat + [entry])[-CHAT_HISTORY:]
    for c in room.seats:
        await send_to_color(room, c, entry)


async def handle_in_room(room, color, msg):
    kind = msg.get("type")
    if kind == "move":
        await handle_move(room, color, msg)
    elif kind == "chat":
        await handle_chat(room, color, msg)
    elif kind == "resign" and room.started and room.result is None:
        await end_game(room, other(color), "resignation")
    elif kind == "draw_offer" and room.started and room.result is None:
        if room.draw_offer == other(color):
            await end_game(room, None, "agreement")
        elif room.draw_offer is None:
            room.draw_offer = color
            await broadcast_state(room)
    elif kind == "draw_accept" and room.started and room.result is None and room.draw_offer == other(color):
        await end_game(room, None, "agreement")
    elif kind == "draw_decline" and room.result is None and room.draw_offer == other(color):
        room.draw_offer = None
        await broadcast_state(room)
        await send_to_color(room, other(color), {"type": "notice", "text": "Draw offer declined."})


async def handler(ws):
    attached = None
    try:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(msg, dict):
                continue
            if attached is None:
                entry = {"create": create_room, "join": join_room, "rejoin": rejoin_room}.get(msg.get("type"))
                if entry:
                    attached = await entry(ws, msg)
            else:
                await handle_in_room(*attached, msg)
    except ConnectionClosed:
        pass
    finally:
        if attached:
            await detach(*attached, ws)


async def detach(room, color, ws):
    seat = room.seats[color]
    if seat.ws is not ws:
        return
    seat.ws = None
    seat.left_at = time.monotonic()
    if room.result is None:
        await broadcast_state(room)


async def close_room(room):
    ROOMS.pop(room.code, None)
    for seat in room.seats.values():
        TOKENS.pop(seat.token, None)
        if seat.ws:
            await seat.ws.close()
    log.info("room %s closed", room.code)


async def watch(room):
    """Enforce clocks, disconnect grace periods and room lifetimes."""
    try:
        while True:
            await asyncio.sleep(TICK)
            now = time.monotonic()
            if room.result is not None:
                if now - room.ended_at > END_TTL or all(s.ws is None for s in room.seats.values()):
                    return
                continue

            running = room.running_color()
            if running and room.remaining(running) <= 0:
                await end_game(room, other(running), "timeout")
                continue

            if not room.started and now - room.created > WAIT_TTL:
                return
            for color, seat in room.seats.items():
                if seat.ws is None and now - seat.left_at > GRACE_SECONDS:
                    if not room.started:
                        return
                    await end_game(room, other(color), "abandonment")
                    break
    finally:
        await close_room(room)


async def process_request(connection, request):
    if request.path == "/health":
        return connection.respond(HTTPStatus.OK, "OK\n")
    return None


def make_server(host, port):
    return serve(handler, host, port, process_request=process_request, max_size=4096)


async def run(host, port):
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # Windows event loops
            pass
    async with make_server(host, port):
        log.info("ChessCore server listening on %s:%s", host, port)
        await stop.wait()
    log.info("shutting down")


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8765"))
    try:
        asyncio.run(run(host, port))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
