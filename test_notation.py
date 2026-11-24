"""Tests for notation and board-display helpers. Run with:  py -m unittest test_notation -v"""

import re
import unittest
from unittest.mock import patch

import ascii_chess as ac


def play(moves):
    game = ac.new_game()
    for uci in moves:
        frm, to, promotion = ac.uci_to_move(uci)
        game = ac.apply_move(game, frm, to, promotion or "q")
    return game


def board_from(placements, turn="w"):
    game = ac.new_game()
    game["board"] = [[""] * 8 for _ in range(8)]
    for square, piece in placements.items():
        game["board"][8 - int(square[1])]["abcdefgh".index(square[0])] = piece
    game["turn"] = turn
    game["castling"] = {key: False for key in game["castling"]}
    return game


class NotationTests(unittest.TestCase):
    def test_opening_moves_use_standard_notation(self):
        moves = ["e2e4", "e7e5", "g1f3", "b8c6", "f1b5", "a7a6", "e1g1"]
        self.assertEqual(ac.san_history(moves), ["e4", "e5", "Nf3", "Nc6", "Bb5", "a6", "O-O"])

    def test_captures_and_en_passant(self):
        moves = ["e2e4", "d7d5", "e4e5", "f7f5", "e5f6", "g8f6", "d1h5"]
        self.assertEqual(ac.san_history(moves), ["e4", "d5", "e5", "f5", "exf6", "Nxf6", "Qh5+"])

    def test_checkmate_gets_a_hash(self):
        self.assertEqual(ac.san_history(["f2f3", "e7e5", "g2g4", "d8h4"])[-1], "Qh4#")

    def test_disambiguation_and_promotion(self):
        game = board_from({"a1": "wr", "h1": "wr", "e8": "bk", "e2": "wk", "a7": "wp"})
        self.assertEqual(ac.move_to_san(game, (7, 0), (7, 4))[0], "Rae1")
        promo = ac.move_to_san(game, (1, 0), (0, 0), "n")[0]
        self.assertEqual(promo, "a8=N")

    def test_history_cache_survives_a_shorter_history(self):
        ac.san_history(["e2e4", "e7e5", "g1f3"])
        self.assertEqual(ac.san_history(["d2d4"]), ["d4"])

    def test_bad_history_falls_back_to_raw_moves(self):
        self.assertEqual(ac.san_history(["e2e4", "zz"]), ["e2e4", "zz"])


class DisplayHelperTests(unittest.TestCase):
    def test_expanded_board_keeps_squares_markers_and_flipped_coordinates(self):
        game = ac.new_game()
        ui = ac.new_ui()
        ui["selected"] = (6, 4)
        ui["targets"] = {(4, 4): False, (5, 5): True}
        lines = ac.build_board_lines(game, ui, flip=True, cell_width=5, cell_height=2)
        plain = [re.sub(r"\033\[[0-9;]*m", "", line) for line in lines]
        self.assertEqual(len(lines), 27)
        self.assertEqual(len(plain[2]), 54)
        self.assertEqual(plain[0].split(), list("hgfedcba"))
        self.assertTrue(plain[2].startswith(" 1 │"))
        self.assertIn("{♙}", "\n".join(plain))
        self.assertIn("○", "\n".join(plain))
        self.assertIn("✕", "\n".join(plain))
        self.assertNotIn("♙", plain[3])

    def test_board_expands_only_when_window_and_footer_fit(self):
        game, ui = ac.new_game(), ac.new_ui()
        for width, height, footer, expected_width, expected_height in [
            (72, 22, (), 38, 19),
            (80, 30, (), 38, 27),
            (87, 30, (), 38, 27),
            (88, 30, (), 54, 27),
            (88, 32, ("chat", "", ""), 38, 19),
            (88, 33, ("chat", "", ""), 54, 27),
        ]:
            with self.subTest(width=width, height=height, footer=footer), \
                    patch.object(ac, "terminal_width", return_value=width), \
                    patch.object(ac, "terminal_height", return_value=height), \
                    patch.object(ac, "draw_frame") as draw:
                ac.render_game(game, ui, "playing", footer=footer)
                lines = [re.sub(r"\033\[[0-9;]*m", "", line) for line in draw.call_args.args[0]]
                border = next(line.lstrip().split("┐")[0] + "┐" for line in lines if "┌" in line)
                self.assertEqual(len(border), expected_width - 5)
                self.assertEqual(sum(line.count("│") == 9 for line in lines),
                                 8 * (2 if expected_height == 27 else 1))
                self.assertLessEqual(len(lines), height)

    def test_captured_pieces_and_material(self):
        game = play(["e2e4", "d7d5", "e4d5", "d8d5"])
        self.assertEqual(ac.captured_pieces(game["board"], "w"), ["p"])
        self.assertEqual(ac.captured_pieces(game["board"], "b"), ["p"])
        self.assertEqual(ac.material(game["board"], "w"), ac.material(game["board"], "b"))

    def test_draw_reason(self):
        bare = board_from({"e1": "wk", "e8": "bk"})
        self.assertEqual(ac.draw_reason(bare), "insufficient material")
        self.assertEqual(ac.draw_reason(play(["e2e4"])), "the fifty-move rule")


if __name__ == "__main__":
    unittest.main()
