"""Tests for the Stockfish opponent. Run with: py -m unittest test_computer -v"""

import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import ascii_chess as ac


FAKE_ENGINE = """
import sys
skill = None
for command in sys.stdin:
    command = command.strip()
    if command == "uci":
        print("uciok", flush=True)
    elif command == "isready":
        print("readyok", flush=True)
    elif command.startswith("setoption name Skill Level value "):
        skill = int(command.rsplit(" ", 1)[1])
    elif command.startswith("go movetime "):
        expected = {0: "200", 10: "500", 20: "1000"}
        move = "e7e5" if expected.get(skill) == command.split()[-1] else "bad"
        print("bestmove " + move, flush=True)
    elif command == "quit":
        break
"""


class ComputerTests(unittest.TestCase):
    def test_frozen_game_finds_bundled_engine_and_respects_override(self):
        with tempfile.TemporaryDirectory() as folder:
            bundle = os.path.join(folder, "bundle")
            os.makedirs(os.path.join(bundle, "stockfish"))
            binary = os.path.join(bundle, "stockfish", "stockfish.exe" if os.name == "nt" else "stockfish")
            with open(binary, "w", encoding="utf-8"):
                pass
            with patch.object(ac, "__file__", os.path.join(bundle, "ascii_chess.py")), \
                    patch.object(sys, "frozen", True, create=True), \
                    patch.object(sys, "executable", os.path.join(folder, "ChessCore.exe")), \
                    patch.dict(os.environ, {}, clear=True):
                self.assertEqual(ac.stockfish_path(), binary)
                with patch.dict(os.environ, {"CHESSCORE_STOCKFISH": "custom-engine"}):
                    self.assertEqual(ac.stockfish_path(), "custom-engine")

    def test_levels_play_legal_moves_with_their_own_settings(self):
        game = ac.new_game()
        game = ac.apply_move(game, *ac.uci_to_move("e2e4")[:2])
        real_popen = subprocess.Popen

        def launch(*args, **kwargs):
            return real_popen([sys.executable, "-u", "-c", FAKE_ENGINE], **kwargs)

        for level in ac.COMPUTER_LEVELS:
            with self.subTest(level=level), patch.object(ac.subprocess, "Popen", side_effect=launch):
                engine = ac.start_engine("fake")
                try:
                    ac.configure_engine(engine, level)
                    frm, to, promotion = ac.engine_best_move(engine, game, ac.COMPUTER_LEVELS[level][1])
                    self.assertEqual(ac.move_to_uci(frm, to, promotion), "e7e5")
                    self.assertEqual(ac.apply_move(game, frm, to)["turn"], "w")
                finally:
                    ac.stop_engine(engine)

    def test_rejects_illegal_and_invalid_engine_moves(self):
        for response in ("bestmove e2e5", "bestmove e2e4q", "bestmove (none)", "bestmove"):
            with self.subTest(response=response), \
                    patch.object(ac, "send_to_engine"), \
                    patch.object(ac, "read_engine_until", return_value=response):
                with self.assertRaises(RuntimeError):
                    ac.engine_best_move(None, ac.new_game())

    def test_promotion_move_is_applied(self):
        game = ac.new_game()
        game["board"] = [[""] * 8 for _ in range(8)]
        game["board"][7][4], game["board"][0][4] = "wk", "bk"
        game["board"][1][0] = "wp"
        with patch.object(ac, "send_to_engine"), \
                patch.object(ac, "read_engine_until", return_value="bestmove a7a8n"):
            frm, to, promotion = ac.engine_best_move(None, game)
        self.assertEqual(ac.apply_move(game, frm, to, promotion)["board"][0][0], "wn")

    def test_computer_save_survives_moves_and_load(self):
        game = ac.new_game()
        game["computer"] = "intermediate"
        game = ac.apply_move(game, *ac.uci_to_move("e2e4")[:2])
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "save.json")
            self.assertTrue(ac.save_game(game, path))
            loaded = ac.load_game(path)
        self.assertEqual(loaded, game)
        self.assertEqual(loaded["turn"], "b")
        self.assertFalse(ac.is_valid_state(dict(game, computer="impossible")))
        self.assertTrue(ac.is_valid_state(ac.new_game()))

    def test_game_runs_computer_turn_and_cleans_up(self):
        game = ac.new_game()
        game["computer"] = "easy"
        game = ac.apply_move(game, *ac.uci_to_move("e2e4")[:2])
        engine = object()
        rendered = []
        with patch.object(ac, "clear_screen"), \
                patch.object(ac, "render_game", side_effect=lambda state, *args: rendered.append(state)), \
                patch.object(ac, "start_engine", return_value=engine), \
                patch.object(ac, "configure_engine") as configure, \
                patch.object(ac, "engine_best_move", return_value=ac.uci_to_move("e7e5")) as best, \
                patch.object(ac, "stop_engine") as stop, \
                patch.object(ac, "read_key", return_value="QUIT"), \
                patch.object(ac, "confirm_quit", return_value="discard"):
            self.assertEqual(ac.play_game(game), "quit")
        configure.assert_called_once_with(engine, "easy")
        best.assert_called_once()
        self.assertEqual(rendered[-1]["history"], ["e2e4", "e7e5"])
        stop.assert_called_once_with(engine)

    def test_missing_engine_reports_error_and_offers_save(self):
        game = ac.new_game()
        game["computer"] = "easy"
        messages = []
        with patch.object(ac, "clear_screen"), \
                patch.object(ac, "render_game", side_effect=lambda state, ui, status: messages.append(ui["message"])), \
                patch.object(ac, "start_engine", side_effect=FileNotFoundError("missing Stockfish")), \
                patch.object(ac, "read_key", return_value="Y"), \
                patch.object(ac, "save_game", return_value=True) as save:
            self.assertEqual(ac.play_game(game), "menu")
        self.assertIn("missing Stockfish", messages[-1])
        save.assert_called_once_with(game)

    def test_engine_exit_before_handshake_is_reported(self):
        real_popen = subprocess.Popen
        processes = []

        def launch(*args, **kwargs):
            process = real_popen([sys.executable, "-u", "-c", "pass"], **kwargs)
            processes.append(process)
            return process

        with patch.object(ac.subprocess, "Popen", side_effect=launch):
            with self.assertRaises((OSError, RuntimeError)):
                ac.start_engine("fake")
        self.assertIsNotNone(processes[0].poll())
        self.assertTrue(processes[0].stdin.closed)
        self.assertTrue(processes[0].stdout.closed)

    def test_failed_quit_save_keeps_game_open(self):
        game = ac.new_game()
        messages = []
        with patch.object(ac, "clear_screen"), \
                patch.object(ac, "render_game", side_effect=lambda state, ui, status: messages.append(ui["message"])), \
                patch.object(ac, "read_key", side_effect=["QUIT", "QUIT"]), \
                patch.object(ac, "confirm_quit", side_effect=["save", "discard"]), \
                patch.object(ac, "save_game", return_value=False):
            self.assertEqual(ac.play_game(game), "quit")
        self.assertTrue(any("Couldn't save" in message for message in messages))


if __name__ == "__main__":
    unittest.main()
