from __future__ import annotations

from backend.debugger import DebugSession

RECURSIVE = """fun fact(n)
    let local = n * 10
    if n <= 1
        return 1
    return n * fact(n - 1)
let r = fact(3)
print(r)
"""

RECURSIVE_BASE2 = RECURSIVE.replace("return 1", "return 2")

LOOP = """let i = 0
let total = 0
while i < 5
    total = total + i
    i = i + 1
print(total)
"""

LOOP_SIX = LOOP.replace("i < 5", "i < 6")

LOOP_SHIFTED = "let extra = 0\n" + LOOP

BOOM = """fun explode(x)
    let y = 10 / x
    return y
fun wrapper(v)
    return explode(v)
let z = wrapper(0)
print(z)
"""

BOOM_SAFE = BOOM.replace("10 / x", "10 / (x + 1)")
BOOM_ARG1 = BOOM.replace("wrapper(0)", "wrapper(1)")


def locate(left_src, right_src, **kwargs):
    left = DebugSession(left_src)
    right = DebugSession(right_src)
    return left, right, left.locate_divergence(right, **kwargs)


def test_recursion_base_case_change_locates_first_differing_output():
    left, right, result = locate(
        RECURSIVE, RECURSIVE_BASE2, watch={"output": True, "variables": []}
    )
    assert result["status"] == "diverged"
    div = result["divergence"]
    # Earliest corresponding position whose watched value differs: print(r).
    assert div["left"]["line"] == 7
    assert div["left"]["output"] == "6\n"
    assert div["right"]["output"] == "12\n"
    assert div["reasons"] == ["output"]
    # The changed base-case statements cannot correspond and must not be
    # silently treated as equal.
    assert result["unmatched"]["leftCount"] == 1  # return 1
    assert result["unmatched"]["rightCount"] == 1  # return 2
    # Call sites around the divergence are available on both sides.
    assert div["leftContext"]["before"]
    assert div["leftContext"]["before"][-1]["stack"]


def test_recursion_base_case_change_watched_variable():
    _, _, result = locate(
        RECURSIVE,
        RECURSIVE_BASE2,
        watch={"output": False, "variables": ["r"]},
    )
    assert result["status"] == "diverged"
    div = result["divergence"]
    assert div["variables"] == {"r": {"left": "6", "right": "12"}}


def test_loop_one_extra_iteration_not_aligned_by_step_count():
    left, right, result = locate(
        LOOP, LOOP_SIX, watch={"output": True, "variables": ["total"]}
    )
    assert result["status"] == "diverged"
    div = result["divergence"]
    # The extra iteration itself has no counterpart; the first
    # corresponding position with a different watched value is print(total).
    assert div["left"]["line"] == 6
    assert div["right"]["line"] == 6
    assert div["variables"] == {"total": {"left": "10", "right": "15"}}
    # The pause happens before print runs, so the watched variable is
    # what differs at this position; the outputs catch up one step
    # later (line events fire before their statement executes).
    assert div["reasons"] == ["variables"]
    # Extra iteration body steps are reported as non-correspondable,
    # never paired by raw step order.
    assert result["unmatched"]["rightCount"] > 0
    # Matched loop-body pairs agreed: the difference is not invented early.
    assert result["matchedPairs"] > 5


def test_loop_extra_iteration_only_reports_unmatched_when_values_hidden():
    # Watching only a variable that stays equal in the common range:
    # the extra iteration is one-sided, so the result is "unmatched",
    # never "equal" and never a fabricated divergence.
    _, _, result = locate(
        LOOP, LOOP_SIX, watch={"output": False, "variables": ["i"]},
    )
    # i at print(total) differs (5 vs 6), so this still diverges there.
    assert result["status"] == "diverged"
    _, _, result2 = locate(
        LOOP, LOOP_SIX, watch={"output": False, "variables": ["nonexistent"]},
    )
    assert result2["status"] == "unmatched"
    assert not result2["diverged"]


def test_exception_early_exit_is_not_claimed_equal():
    left, right, result = locate(
        BOOM, BOOM_SAFE, watch={"output": True, "variables": []}
    )
    # Corresponding prefix agrees; the left side then dies on 10 / 0
    # while the right side runs to completion. The terminal events and
    # the remaining right-side steps cannot correspond.
    assert result["status"] == "unmatched"
    assert not result["diverged"]
    left_samples = result["unmatched"]["leftSample"]
    assert any(s["kind"] == "error" for s in left_samples)
    right_samples = result["unmatched"]["rightSample"]
    assert any(s["kind"] == "end" for s in right_samples)
    assert any(s["line"] == 7 for s in right_samples)  # print(z) never matched


def test_watched_variable_diverges_inside_matching_call_path():
    # Same statements, same call path; only the argument value differs.
    _, _, result = locate(
        BOOM, BOOM_ARG1, watch={"output": False, "variables": ["x"]},
    )
    assert result["status"] == "diverged"
    div = result["divergence"]
    assert div["left"]["line"] == 2  # let y = 10 / x
    assert div["variables"] == {"x": {"left": "0", "right": "1"}}
    path = [f["name"] for f in div["left"]["stack"]]
    assert path == ["main", "wrapper", "explode"]


def test_inserted_statement_does_not_shift_alignment():
    # An extra leading statement shifts every line number and step
    # index; correspondence by statement text still lines up, and the
    # inserted statement is reported as one-sided instead of matched.
    _, _, result = locate(LOOP, LOOP_SHIFTED, watch={"output": True, "variables": []})
    assert result["status"] == "unmatched"
    assert result["unmatched"]["rightCount"] == 1
    assert result["unmatched"]["rightSample"][0]["line"] == 1


def test_identical_revisions_are_equal():
    left = DebugSession(LOOP)
    right = left.fork()
    result = left.locate_divergence(right)
    assert result["status"] == "equal"
    assert not result["diverged"]
    assert result["unmatched"]["leftCount"] == 0
    assert result["unmatched"]["rightCount"] == 0


def test_budget_exhaustion_preserves_explored_range():
    left = DebugSession(LOOP)
    right = DebugSession(LOOP_SIX)
    result = left.locate_divergence(right, left_budget=4, right_budget=4)
    assert result["status"] == "budget_exhausted"
    assert not result["diverged"]
    assert result["left"]["exploredSteps"] == 4
    assert result["right"]["exploredSteps"] == 4
    assert not result["left"]["fullyExplored"]
    assert result["matchedPairs"] >= 1  # explored range is kept, not dropped
    # A larger budget does find the real divergence.
    full = left.locate_divergence(right, watch={"output": True, "variables": []})
    assert full["status"] == "diverged"


def test_exploration_never_moves_cursors_or_records():
    left = DebugSession(RECURSIVE)
    right = DebugSession(RECURSIVE_BASE2)
    left.step_into()
    left_cursor = left.cursor
    right_cursor = right.cursor
    left_record = left.to_record()
    left.locate_divergence(right)
    assert left.cursor == left_cursor
    assert right.cursor == right_cursor
    assert left.to_record() == left_record


def test_fork_at_divergence_keeps_original_untouched():
    left = DebugSession(RECURSIVE)
    right = DebugSession(RECURSIVE_BASE2)
    parent_cursor = left.cursor
    result = left.locate_divergence(right)
    index = result["divergence"]["leftContext"]["before"][-1]["index"]
    probe = left.fork_at(index, name="调查")
    # The new branch is paused exactly at the divergence location...
    assert probe.cursor == index
    assert probe.parent_id == left.id
    assert probe.fork_cursor == index
    assert probe.state()["line"] == 4  # return 1, the changed base case
    # ...and can step independently...
    probe.step_into()
    assert probe.cursor != index
    # ...while the original execution and its record stay exactly as-is.
    assert left.cursor == parent_cursor
    assert left.to_record()["cursor"] == parent_cursor


def test_fork_at_rejects_out_of_range_index():
    s = DebugSession(LOOP)
    for bad in (-2, len(s.events), len(s.events) + 10):
        try:
            s.fork_at(bad)
        except ValueError:
            pass
        else:
            raise AssertionError("fork_at accepted out-of-range index")


def test_divergence_context_covers_call_sites_before_and_after():
    left, right, result = locate(
        RECURSIVE, RECURSIVE_BASE2, watch={"output": True, "variables": []}, context=2
    )
    div = result["divergence"]
    before = div["leftContext"]["before"]
    # The divergence itself is the terminal event (outputs 6 vs 12);
    # the pauses right before it are visible with their full stacks.
    assert div["left"]["kind"] == "end"
    assert before[-1]["line"] == 7  # print(r)
    assert before[-1]["stack"]
    assert div["leftContext"]["after"] == []
