"""Tests for notation and board-display helpers. Run with:  py -m unittest test_notation -v"""

import unittest

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
