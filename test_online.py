"""Tests for the online server. Run with:  py -m unittest test_online -v"""

import asyncio
import json
import unittest
import urllib.request

from websockets.asyncio.client import connect

import ascii_chess as ac
import chess_server as cs


class Client:
    def __init__(self, ws):
        self.ws = ws
        self.room = None
        self.state = None

    async def send(self, **payload):
        await self.ws.send(json.dumps(payload))

    async def next(self, timeout=3):
        return json.loads(await asyncio.wait_for(self.ws.recv(), timeout))

    async def until(self, kind, predicate=lambda m: True, timeout=3):
        """Read messages until one of type `kind` satisfies `predicate`; tracks room/state."""
        while True:
            message = await self.next(timeout)
            if message["type"] == "room":
                self.room = message
            if message["type"] == "state":
                self.state = message
            if message["type"] == kind and predicate(message):
                return message


class OnlineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        cs.ROOMS.clear()
        cs.TOKENS.clear()
        self.saved = (cs.TICK, cs.GRACE_SECONDS)
        cs.TICK = 0.05
        self.server = cs.make_server("127.0.0.1", 0)
        await self.server.__aenter__()
        self.port = self.server.sockets[0].getsockname()[1]
        self.sockets = []

    async def asyncTearDown(self):
        for ws in self.sockets:
            await ws.close()
        await self.server.__aexit__(None, None, None)
        cs.TICK, cs.GRACE_SECONDS = self.saved

    async def client(self):
        ws = await connect(f"ws://127.0.0.1:{self.port}")
        self.sockets.append(ws)
        return Client(ws)

    async def start_game(self, minutes=0, increment=0):
        """Return (white, black) clients in a started game."""
        creator = await self.client()
        await creator.send(type="create", minutes=minutes, increment=increment, color="w")
        await creator.until("state")
        joiner = await self.client()
        await joiner.send(type="join", code=creator.room["code"])
        await joiner.until("state", lambda m: m["started"])
        await creator.until("state", lambda m: m["started"])
        return creator, joiner

    async def play(self, white, black, moves):
        for index, uci in enumerate(moves):
            mover, watcher = (white, black) if index % 2 == 0 else (black, white)
            await mover.send(type="move", uci=uci)
            expected = index + 1
            await watcher.until("state", lambda m: len(m["state"]["history"]) == expected)
            await mover.until("state", lambda m: len(m["state"]["history"]) == expected)

    async def test_health_endpoint(self):
        url = f"http://127.0.0.1:{self.port}/health"
        body = await asyncio.to_thread(lambda: urllib.request.urlopen(url, timeout=3).read())
        self.assertEqual(body.strip(), b"OK")

    async def test_fools_mate_ends_with_checkmate(self):
        white, black = await self.start_game()
        await self.play(white, black, ["f2f3", "e7e5", "g2g4"])
        await black.send(type="move", uci="d8h4")
        final = await white.until("state", lambda m: m["result"])
        self.assertEqual(final["result"], {"winner": "b", "reason": "checkmate"})
        self.assertEqual(final["status"], "checkmate")

    async def test_illegal_and_out_of_turn_moves_are_rejected(self):
        white, black = await self.start_game()
        await black.send(type="move", uci="e7e5")
        self.assertEqual((await black.until("error"))["message"], "It's your opponent's turn.")
        await white.send(type="move", uci="e2e5")
        self.assertEqual((await white.until("error"))["message"], "That move isn't legal.")
        await white.send(type="move", uci="zz99")
        self.assertEqual((await white.until("error"))["message"], "That move isn't legal.")
        self.assertEqual(white.state["state"]["history"], [])

    async def test_promotion_needs_a_piece(self):
        game = ac.new_game()
        game["board"] = [[""] * 8 for _ in range(8)]
        game["board"][7][4], game["board"][0][7], game["board"][1][0] = "wk", "bk", "wp"
        self.assertIsNone(cs.parse_move(game, "w", "a7a8"))
        self.assertIsNone(cs.parse_move(game, "w", "a7a8k"))
        self.assertEqual(cs.parse_move(game, "w", "a7a8n"), ((1, 0), (0, 0), "n"))

    async def test_join_errors(self):
        client = await self.client()
        await client.send(type="join", code="NOPE")
        self.assertTrue((await client.until("error"))["fatal"])
        white, _ = await self.start_game()
        third = await self.client()
        await third.send(type="join", code=white.room["code"])
        self.assertEqual((await third.until("error"))["message"], "That room already has two players.")

    async def test_resign_and_draw(self):
        white, black = await self.start_game()
        await white.send(type="draw_offer")
        await black.until("state", lambda m: m["draw_offer"] == "w")
        await black.send(type="draw_decline")
        self.assertEqual((await white.until("notice"))["text"], "Draw offer declined.")
        await white.send(type="draw_offer")
        await black.until("state", lambda m: m["draw_offer"] == "w")
        await black.send(type="draw_accept")
        final = await white.until("state", lambda m: m["result"])
        self.assertEqual(final["result"], {"winner": None, "reason": "agreement"})

        white, black = await self.start_game()
        await black.send(type="resign")
        final = await white.until("state", lambda m: m["result"])
        self.assertEqual(final["result"], {"winner": "w", "reason": "resignation"})

    async def test_chat_is_sanitised_and_relayed(self):
        white, black = await self.start_game()
        await white.send(type="chat", text="  hello\x07 there  ")
        for client in (white, black):
            message = await client.until("chat")
            self.assertEqual((message["from"], message["text"]), ("w", "hello there"))

    async def test_clock_timeout(self):
        white, black = await self.start_game(minutes=1)
        room = cs.ROOMS[white.room["code"]]
        room.clock["w"] = 0.2
        final = await black.until("state", lambda m: m["result"], timeout=5)
        self.assertEqual(final["result"], {"winner": "b", "reason": "timeout"})

    async def test_increment_is_added_after_a_move(self):
        white, black = await self.start_game(minutes=1, increment=5)
        await self.play(white, black, ["e2e4"])
        self.assertGreater(white.state["clocks"]["w"], 60)

    async def test_rejoin_restores_the_game(self):
        white, black = await self.start_game()
        await self.play(white, black, ["e2e4"])
        await black.ws.close()
        await white.until("state", lambda m: not m["opponent_connected"])

        again = await self.client()
        await again.send(type="rejoin", token=black.room["token"])
        restored = await again.until("state")
        self.assertEqual(restored["color"], "b")
        self.assertEqual(restored["state"]["history"], ["e2e4"])
        await white.until("state", lambda m: m["opponent_connected"])

    async def test_abandonment_after_grace_period(self):
        cs.GRACE_SECONDS = 0.3
        white, black = await self.start_game()
        await black.ws.close()
        final = await white.until("state", lambda m: m["result"], timeout=5)
        self.assertEqual(final["result"], {"winner": "w", "reason": "abandonment"})

    async def test_unknown_token_is_fatal(self):
        client = await self.client()
        await client.send(type="rejoin", token="nope")
        self.assertTrue((await client.until("error"))["fatal"])


if __name__ == "__main__":
    unittest.main()
