"""Tests for the normal-exit outro. Run with: py -m unittest test_shutdown -v"""

import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import ascii_chess as ac


class ShutdownOutroTests(unittest.TestCase):
    def test_outro_ends_with_server_failure_messages(self):
        output = io.StringIO()
        with patch.object(ac.time, "sleep") as sleep, redirect_stdout(output):
            ac.crashing_outro()

        text = output.getvalue()
        self.assertIn("ERROR: server heartbeat timeout", text)
        self.assertIn("[FAILED] Failed to start ChessCore game service.", text)
        self.assertNotIn("[OK]", text)
        self.assertNotIn("shutting down", text)
        self.assertLessEqual(sum(call.args[0] for call in sleep.call_args_list), 3)

    def test_normal_quit_runs_outro(self):
        with patch.object(ac, "enter_raw_mode", return_value=None), \
                patch.object(ac, "leave_raw_mode"), \
                patch.object(ac, "boot"), \
                patch.object(ac, "load_game", return_value=None), \
                patch.object(ac, "run_menu", return_value="quit"), \
                patch.object(ac, "clear_screen"), \
                patch.object(ac.os, "system"), \
                patch.object(ac, "crashing_outro") as outro:
            ac.main()

        outro.assert_called_once_with()

    def test_real_exception_does_not_play_fake_outro(self):
        with patch.object(ac, "enter_raw_mode", return_value=None), \
                patch.object(ac, "leave_raw_mode"), \
                patch.object(ac, "boot", side_effect=RuntimeError("test error")), \
                patch.object(ac, "clear_screen"), \
                patch.object(ac.os, "system"), \
                patch("builtins.print") as print_mock, \
                patch.object(ac, "crashing_outro") as outro:
            with self.assertRaisesRegex(RuntimeError, "test error"):
                ac.main()

        outro.assert_not_called()
        print_mock.assert_any_call("Session terminated.")


if __name__ == "__main__":
    unittest.main()
