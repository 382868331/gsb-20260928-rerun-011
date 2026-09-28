import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from edl.debugger import create_session, source_hash
from edl.interpreter import Interpreter
from edl.parser import parse_program
from edl.store import SessionStore, StoreError


RECURSION = """fun factorial(n)
    if n <= 1
        return 1
    smaller = n - 1
    part = factorial(smaller)
    return n * part

fun main()
    result = factorial(3)
    print("done", result)
"""

LOOP = """fun main()
    i = 0
    while i < 3
        print("tick", i)
        i = i + 1
    print("end")
"""

DIV_ZERO = """fun main()
    a = 10
    b = 0
    print(a / b)
"""


class InterpreterTests(unittest.TestCase):
    def test_run_to_completion_outputs_in_order(self):
        interp = Interpreter(parse_program(LOOP))
        events = list(interp.run())
        outputs = [e["text"] for e in events if e["type"] == "output"]
        self.assertEqual(outputs, ["tick 0", "tick 1", "tick 2", "end"])
        self.assertEqual(events[-1]["type"], "exit")

    def test_runtime_error_is_an_event(self):
        interp = Interpreter(parse_program(DIV_ZERO))
        events = list(interp.run())
        errors = [e for e in events if e["type"] == "error"]
        self.assertEqual(len(errors), 1)
        self.assertIn("division by zero", errors[0]["message"])


class DebugSessionTests(unittest.TestCase):
    def test_start_pauses_at_first_statement(self):
        session = create_session("s1", LOOP)
        session.start()
        self.assertEqual(session.status, "paused")
        self.assertEqual(session.current_line, 2)
        self.assertEqual(len(session.snapshot), 1)

    def test_step_into_follows_recursion_with_isolated_locals(self):
        session = create_session("s2", RECURSION)
        session.start()
        session.step_into()  # result = factorial(3): enter factorial n=3
        # Walk until the deepest recursive frame by stepping many times.
        deepest = []
        for _ in range(40):
            if session.status != "paused":
                break
            count = len([f for f in session.snapshot if f["name"] == "factorial"])
            if count == 3:
                deepest = session.snapshot
                break
            session.step_into()
        frames = session.snapshot
        factorial_frames = deepest or [f for f in frames if f["name"] == "factorial"]
        self.assertGreaterEqual(len(factorial_frames), 2)
        n_values = [f["locals"]["n"] for f in factorial_frames]
        self.assertEqual(n_values, sorted(n_values, reverse=True))
        # Mutating a returned variable view must not touch the real frame.
        import copy

        view = session.variables()
        view["frames"][0]["locals"]["hacked"] = 999
        self.assertNotIn("hacked", session.variables()["frames"][0]["locals"])

    def test_step_over_completes_call_before_pausing(self):
        session = create_session("s3", RECURSION)
        session.start()
        session.step_into()  # into factorial body
        session.step_over()  # to smaller = n - 1
        self.assertEqual(session.current_line, 4)
        session.step_over()  # over recursive call, lands at return line
        self.assertEqual(session.current_line, 5)
        # Back in main: stepping over the call site completes the whole call.
        for _ in range(20):
            session.step_into()
            if session.current_line == 9:
                break
        session.step_over()
        session.step_over()
        self.assertEqual(session.status, "paused")
        self.assertEqual(session.current_line, 10)  # print line in main
        self.assertEqual([f["name"] for f in session.snapshot], ["main"])
        self.assertEqual(session.snapshot[0]["locals"]["result"], 6)

    def test_continue_runs_to_breakpoint_then_finishes(self):
        session = create_session("s4", LOOP)
        result = session.set_breakpoint(5)
        self.assertTrue(result["ok"])
        session.start()
        session.continue_run()
        self.assertEqual(session.current_line, 5)
        self.assertEqual(session.snapshot[0]["locals"]["i"], 0)
        session.clear_breakpoint(5)
        session.continue_run()
        self.assertEqual(session.status, "finished")

    def test_breakpoint_only_on_statement_lines(self):
        session = create_session("s5", LOOP)
        result = session.set_breakpoint(1)  # fun header cannot break
        self.assertFalse(result["ok"])

    def test_reading_variables_does_not_advance_execution(self):
        session = create_session("s6", LOOP)
        session.start()
        seq = len(session.events)
        line = session.current_line
        session.variables()
        session.state()
        session.variables()
        self.assertEqual(len(session.events), seq)
        self.assertEqual(session.current_line, line)

    def test_error_status_captures_line_and_frames(self):
        session = create_session("s7", DIV_ZERO)
        session.start()
        for _ in range(10):
            if session.status != "paused":
                break
            session.step_into()
        self.assertEqual(session.status, "error")
        self.assertTrue(session.error["line"])


class StoreTests(unittest.TestCase):
    def test_session_persists_and_replays(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(tmp)
            session = store.create(LOOP)
            store.apply_action(session, "setBreakpoint", {"line": 5})
            store.apply_action(session, "continue")
            events_before = len(session.events)
            frames_before = session.snapshot

            reloaded = SessionStore(tmp)
            restored = reloaded.require(session.session_id)
            self.assertEqual(restored.status, "paused")
            self.assertEqual(restored.current_line, 5)
            self.assertEqual(len(restored.events), events_before)
            self.assertEqual(restored.snapshot, frames_before)
            self.assertEqual(restored.breakpoints, {5})

    def test_changed_source_is_rejected_for_old_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(tmp)
            session = store.create(LOOP)
            changed = LOOP.replace("i < 3", "i < 4")
            self.assertNotEqual(source_hash(changed), session.src_hash)
            with self.assertRaises(StoreError):
                store.assert_source_matches(session, changed)


if __name__ == "__main__":
    unittest.main()
