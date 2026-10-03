from __future__ import annotations

import pytest

from backend.debugger import (
    ComparisonStore,
    DebugSession,
    SessionStore,
    SourceError,
    source_hash,
)

RECURSIVE = """fun fact(n)
    let local = n * 10
    if n <= 1
        return 1
    return n * fact(n - 1)
let r = fact(3)
print(r)
"""

LOOP = """let i = 0
let total = 0
while i < 5
    total = total + i
    i = i + 1
print(total)
"""

BOOM = """fun explode(x)
    let y = 10 / x
    return y
fun wrapper(v)
    return explode(v)
let z = wrapper(0)
print(z)
"""


def locals_of(state, frame_index=-1):
    frame = state["stack"][frame_index]
    return {v["name"]: v["value"]["display"] for v in frame["locals"]}


def test_recursion_locals_follow_current_frame():
    s = DebugSession(RECURSIVE)
    s.set_breakpoints([2])  # let local = n * 10
    s.continue_()
    assert s.state()["line"] == 2
    # Outermost call: n == 3
    assert locals_of(s.state())["n"] == "3"
    s.continue_()
    # Deeper frame: same variable names, different values, top frame wins.
    top = locals_of(s.state())
    assert top["n"] == "2"
    assert len(s.state()["stack"]) == 3  # main + fact(3) + fact(2)
    s.continue_()
    top = locals_of(s.state())
    assert top["n"] == "1"
    # Older frames keep their own locals, not overwritten by deeper calls.
    frames = s.state()["stack"]
    assert {v["name"]: v["value"]["display"] for v in frames[-2]["locals"]}["n"] == "2"


def test_continue_does_not_show_stale_values():
    s = DebugSession(LOOP)
    s.set_breakpoints([4])  # total = total + i
    s.continue_()
    first = locals_of(s.state())
    assert first["i"] == "0"
    s.continue_()
    second = locals_of(s.state())
    assert second["i"] == "1"
    assert second["total"] == "0"
    s.continue_()
    third = locals_of(s.state())
    assert third["i"] == "2"
    assert third["total"] == "1"


def test_breakpoints_adjustable_while_paused():
    s = DebugSession(LOOP)
    s.set_breakpoints([4])
    s.continue_()
    assert s.state()["line"] == 4
    # Paused at line 4; move the breakpoint to line 5 instead.
    s.set_breakpoints([5])
    s.continue_()
    assert s.state()["line"] == 5
    # Paused before line 5 executes, so i is still 0.
    assert locals_of(s.state())["i"] == "0"


def test_step_over_skips_call_body():
    s = DebugSession(RECURSIVE)
    s.step_into()  # line 6: let r = fact(3)
    assert s.state()["line"] == 6
    s.step_over()
    assert s.state()["line"] == 7  # print(r), call body skipped
    assert locals_of(s.state())["r"] == "6"


def test_unparseable_edit_preserves_whole_session():
    s = DebugSession(LOOP)
    s.set_breakpoints([4, 5])
    s.continue_()
    assert s.state()["status"] == "paused"
    paused_line = s.state()["line"]
    paused_locals = locals_of(s.state())
    paused_stack_len = len(s.state()["stack"])
    paused_cursor = s.cursor

    # Repeated invalid submissions must never touch the live session.
    for bad in ("let = \n", "while \n", "fun ("):
        with pytest.raises(SourceError):
            s.preview_source_change(bad)
        with pytest.raises(SourceError):
            s.commit_source_change(bad, [])
        kept = s.state()
        assert kept["source"] == LOOP
        assert kept["breakpoints"] == [4, 5]
        assert kept["line"] == paused_line
        assert len(kept["stack"]) == paused_stack_len
        assert locals_of(kept) == paused_locals
        assert s.cursor == paused_cursor


def test_recursive_pause_edit_keeps_old_frames_until_commit():
    s = DebugSession(RECURSIVE)
    s.set_breakpoints([2])
    s.continue_()
    s.continue_()
    assert len(s.state()["stack"]) == 3
    top_n = locals_of(s.state())["n"]

    new_source = RECURSIVE.replace("n * 10", "n * 20")
    preview = s.preview_source_change(new_source)
    assert preview["changed"] is True
    # Preview never mutates: old recursive frames and current line remain.
    assert len(s.state()["stack"]) == 3
    assert locals_of(s.state())["n"] == top_n
    assert s.state()["breakpoints"] == [2]


def test_preview_marks_reusable_and_confirm_breakpoints():
    s = DebugSession(LOOP)
    s.set_breakpoints([3, 4])
    # Line 3 (while header) is unique and shifts by one -> reusable.
    # Line 4's statement text also exists at new line 5, and a second
    # identical statement is inserted -> ambiguous -> needs confirm.
    edited = (
        "let i = 0\n"
        "let total = 0\n"
        "let note = 1\n"
        "while i < 5\n"
        "    total = total + i\n"
        "    total = total + i\n"
        "    i = i + 1\n"
        "print(total)\n"
    )
    preview = s.preview_source_change(edited)
    by_old = {m["oldLine"]: m for m in preview["migrations"]}
    assert by_old[3]["status"] == "reusable"
    assert by_old[3]["newLine"] == 4
    assert by_old[4]["status"] == "confirm"
    assert by_old[4]["newLine"] is None
    assert {c["line"] for c in by_old[4]["candidates"]} == {5, 6}
    assert preview["executableLines"]


def test_cancel_switch_returns_to_original_session():
    s = DebugSession(LOOP)
    s.set_breakpoints([4])
    s.continue_()
    before = s.state()
    s.preview_source_change("print(1)\n")
    # "Cancel" = simply don't commit; session state is byte-for-byte usable.
    s.continue_()
    after = s.state()
    assert after["line"] == 4
    assert locals_of(after)["i"] == "1"
    assert after["breakpoints"] == before["breakpoints"]


def test_commit_resets_trace_with_confirmed_breakpoints():
    s = DebugSession(LOOP)
    s.set_breakpoints([4])
    s.continue_()
    assert s.state()["status"] == "paused"

    new_source = "print(1)\nprint(2)\n"
    s.commit_source_change(new_source, [])
    state = s.state()
    assert state["source"] == new_source
    assert state["line"] is None
    assert state["stack"] == []
    assert state["breakpoints"] == []
    assert state["status"] == "paused"
    # The new trace is live and never carries old frames.
    s.continue_()
    assert s.state()["status"] == "finished"
    assert s.state()["output"] == "1\n2\n"


def test_switch_after_save_and_reopen(tmp_path):
    store = SessionStore(directory=tmp_path)
    s = store.create(RECURSIVE)
    s.set_breakpoints([2])
    s.continue_()
    s.continue_()
    store.touch(s)
    old_line = s.state()["line"]
    assert len(s.state()["stack"]) == 3

    reopened = SessionStore(directory=tmp_path).get(s.id)
    assert reopened.state()["line"] == old_line
    new_source = RECURSIVE.replace("fact", "fct")
    preview = reopened.preview_source_change(new_source)
    assert preview["changed"] is True
    migrated = [m["newLine"] for m in preview["migrations"] if m["status"] == "reusable"]
    reopened.commit_source_change(new_source, migrated)
    store.touch(reopened)
    state = reopened.state()
    assert state["line"] is None
    assert state["stack"] == []
    # The newly reopened record belongs entirely to the new program.
    again = SessionStore(directory=tmp_path).get(s.id)
    assert again.state()["source"] == new_source
    assert again.state()["stack"] == []
    again.continue_()
    assert again.state()["line"] == 2


def test_save_and_reopen_restores_pause(tmp_path):
    store = SessionStore(directory=tmp_path)
    s = store.create(RECURSIVE)
    s.set_breakpoints([2])
    s.continue_()
    s.continue_()
    store.touch(s)
    paused_line = s.state()["line"]
    paused_locals = locals_of(s.state())

    fresh = SessionStore(directory=tmp_path)
    reopened = fresh.get(s.id)
    assert reopened.state()["line"] == paused_line
    assert locals_of(reopened.state()) == paused_locals
    assert reopened.state()["breakpoints"] == [2]
    # Can keep debugging after reopen.
    reopened.continue_()
    assert reopened.state()["line"] == 2
    assert locals_of(reopened.state())["n"] == "1"


def test_error_state_keeps_call_chain_after_exit():
    s = DebugSession(BOOM)
    s.continue_()  # no breakpoints -> runs to the error
    state = s.state()
    assert state["status"] == "error"
    assert "除以零" in state["error"]["message"]
    names = [f["name"] for f in state["error"]["stack"]]
    assert names == ["main", "wrapper", "explode"]
    # Stepping/continuing after exit must not move or clear the error.
    s.step_into()
    s.continue_()
    again = s.state()
    assert again["status"] == "error"
    assert [f["name"] for f in again["error"]["stack"]] == names


def test_loop_runs_to_clean_finish():
    s = DebugSession(LOOP)
    s.continue_()
    state = s.state()
    assert state["status"] == "finished"
    assert state["output"] == "10\n"


def test_bad_source_rejected():
    with pytest.raises(SourceError):
        DebugSession("let = \n")


def test_fork_shares_pause_but_branches_stay_independent():
    parent = DebugSession(RECURSIVE)
    parent.set_breakpoints([2])
    parent.continue_()
    parent.continue_()
    assert len(parent.state()["stack"]) == 3

    branch_a = parent.fork(name="实验A")
    branch_b = parent.fork(name="实验B")
    # Both start at the exact same pause with the same breakpoints.
    for branch in (branch_a, branch_b):
        assert branch.state()["line"] == parent.state()["line"]
        assert locals_of(branch.state())["n"] == "2"
        assert branch.state()["breakpoints"] == [2]
        assert branch.parent_id == parent.id

    # Branch A moves on and changes breakpoints; B and parent stay put.
    branch_a.set_breakpoints([4])
    branch_a.continue_()
    assert branch_a.state()["line"] == 4
    assert branch_b.state()["line"] == parent.state()["line"]
    assert branch_b.state()["breakpoints"] == [2]
    assert parent.state()["breakpoints"] == [2]
    # Stepping B does not move A.
    branch_b.step_into()
    assert branch_a.state()["line"] == 4


def test_fork_with_edit_failure_leaves_all_branches_intact():
    parent = DebugSession(LOOP)
    parent.set_breakpoints([4])
    parent.continue_()
    sibling = parent.fork()
    with pytest.raises(SourceError):
        parent.fork(source="while \n")
    # Parent and existing sibling are untouched by the failed experiment.
    assert parent.state()["line"] == 4
    assert parent.state()["breakpoints"] == [4]
    assert locals_of(sibling.state()) == locals_of(parent.state())


def test_fork_with_edit_records_confirmed_migrations():
    parent = DebugSession(LOOP)
    parent.set_breakpoints([4])
    parent.continue_()
    edited = "let note = 1\n" + LOOP
    preview = parent.preview_source_change(edited)
    migration = preview["migrations"][0]
    assert migration["status"] == "reusable"
    branch = parent.fork(
        source=edited,
        breakpoints=[migration["newLine"]],
        bp_migrations=[{
            "oldLine": migration["oldLine"],
            "newLine": migration["newLine"],
            "fromHash": parent.source_hash,
            "toHash": preview["newSourceHash"],
        }],
    )
    # Edited branch restarts from the top with migrated breakpoints.
    assert branch.state()["line"] is None
    assert branch.state()["breakpoints"] == [5]
    assert branch.bp_migrations[0]["oldLine"] == 4
    assert branch.bp_migrations[0]["newLine"] == 5
    # Parent keeps its own pause position and old breakpoints.
    assert parent.state()["line"] == 4
    assert parent.state()["breakpoints"] == [4]


def test_compare_finds_first_variable_divergence():
    base = DebugSession(RECURSIVE)
    edited = DebugSession(RECURSIVE.replace("n * 10", "n * 20"))
    # Comparison only uses execution facts that have actually happened;
    # both branches must run before any divergence can be reported.
    base.continue_()
    edited.continue_()
    result = base.compare(edited)
    assert result["diverged"] is True
    assert result["reason"] == "variables"
    assert result["sameRevision"] is False
    left_top = result["left"]["event"]["stack"][-1]
    right_top = result["right"]["event"]["stack"][-1]
    # First difference is local = n * 10 vs n * 20 inside fact.
    assert {v["name"]: v["value"]["display"] for v in left_top["locals"]}["local"] == "30"
    assert {v["name"]: v["value"]["display"] for v in right_top["locals"]}["local"] == "60"
    # Each side reports which program revision its record belongs to.
    assert result["left"]["sourceHash"] == base.source_hash
    assert result["right"]["sourceHash"] == edited.source_hash
    assert result["left"]["event"]["sourceHash"] == base.source_hash


def test_compare_finds_output_divergence_and_identical_traces():
    a = DebugSession("print(1)\n")
    b = DebugSession("print(2)\n")
    a.continue_()
    b.continue_()
    result = a.compare(b)
    assert result["diverged"] is True
    assert result["reason"] == "output"
    same = a.compare(DebugSession("print(1)\n"))
    # A freshly created counterpart has executed nothing while ``a`` ran
    # to its end: the counter side is still waiting and the finished side
    # is simply ahead — never a divergence, never a proven equality.
    assert same["status"] == "ahead"
    assert same["diverged"] is False
    finished = DebugSession("print(1)\n")
    finished.continue_()
    assert a.compare(finished)["status"] == "identical"


def test_compare_before_first_step_reports_waiting_not_divergence():
    # Neither session has executed a single event. The traces differ in
    # their (already known) future, but future behavior is not a fact yet.
    base = DebugSession(RECURSIVE)
    edited = DebugSession(RECURSIVE.replace("n * 10", "n * 20"))
    result = base.compare(edited)
    assert result["status"] == "waiting"
    assert result["diverged"] is False
    assert result["index"] is None
    assert result["left"]["event"] is None
    assert result["right"]["event"] is None
    assert result["left"]["executedCount"] == 0
    assert result["right"]["executedCount"] == 0
    assert result["left"]["terminal"] is False


def test_compare_one_side_ahead_is_not_divergence():
    # Same program; left runs one loop iteration further than right.
    left = DebugSession(LOOP)
    right = DebugSession(LOOP)
    left.set_breakpoints([4])
    right.set_breakpoints([4])
    left.continue_()  # i == 0
    right.continue_()  # i == 0
    left.continue_()  # i == 1, right stays at i == 0
    result = left.compare(right)
    assert result["status"] == "ahead"
    assert result["diverged"] is False
    assert result["reason"] == "ahead"
    # The longer side shows the event the other side has not reached;
    # the waiting side explicitly has no event there.
    assert result["left"]["event"] is not None
    assert result["right"]["event"] is None
    assert result["right"]["executedCount"] < result["left"]["executedCount"]
    # Reverse direction: the other side is then reported as ahead.
    swapped = right.compare(left)
    assert swapped["status"] == "ahead"
    assert swapped["left"]["event"] is None
    assert swapped["right"]["event"] is not None


def test_compare_at_chosen_saved_pause_boundaries():
    left = DebugSession(LOOP)
    right = DebugSession(LOOP)
    left.set_breakpoints([4])
    left.continue_()  # i == 0
    left.continue_()  # i == 1
    right.set_breakpoints([4])
    right.continue_()  # i == 0

    # Pick the older saved pause on the left while the cursor moved on:
    # the old comparison boundary is preserved exactly as it was.
    older = [b for b in left.pause_boundaries() if b["line"] == 4][0]["index"]
    result = left.compare(right, left_boundary=older)
    assert result["left"]["boundary"] == older
    assert result["left"]["cursor"] == left.cursor
    # At that older boundary both sides had executed the same facts.
    assert result["status"] in {"identical", "waiting"}
    assert result["diverged"] is False


def test_compare_rejects_future_boundary():
    left = DebugSession(LOOP)
    right = DebugSession(LOOP)
    left.step_into()
    with pytest.raises(ValueError):
        left.compare(right, left_boundary=left.cursor + 1)
    with pytest.raises(ValueError):
        left.compare(right, right_boundary=0)
    # Comparing before the first step (boundary -1) is legitimate.
    result = left.compare(right, right_boundary=-1)
    assert result["status"] == "ahead"
    assert result["right"]["executedCount"] == 0


def test_compare_recursion_return_distinguishes_frames():
    # Same program, same cursor positions deep in recursion, but the
    # deeper side must show the extra frame rather than matching on
    # identical line numbers.
    shallow = DebugSession(RECURSIVE)
    deep = DebugSession(RECURSIVE)
    shallow.set_breakpoints([2])
    deep.set_breakpoints([2])
    shallow.continue_()  # fact(3)
    deep.continue_()  # fact(3)
    deep.continue_()  # fact(2): same line 2, deeper call
    result = shallow.compare(deep)
    assert result["status"] == "ahead"
    # The first position the shallow side has not reached is shown on the
    # deep side, with its own call-site/variable snapshot (line 3 inside
    # fact(3)); line numbers alone are never treated as the same event.
    ahead_event = result["right"]["event"]
    assert ahead_event is not None
    assert ahead_event["line"] == 3
    assert {f["name"] for f in ahead_event["stack"]} == {"main", "fact"}
    top_frame = ahead_event["stack"][-1]
    assert {v["name"]: v["value"]["display"] for v in top_frame["locals"]}[
        "n"
    ] == "3"

    # Once the deep side actually pauses inside fact(2), the reported
    # position carries the extra stack frame rather than matching shallow
    # merely by sharing line number 2.
    deep.step_into()  # line 5 (return n * fact(...))
    deep.step_into()  # line 2 inside fact(2)
    result = shallow.compare(deep)
    assert result["status"] == "ahead"
    later = result["right"]["event"]
    assert later["line"] == 3 or len(later["stack"]) >= 2
    deeper_steps = deep.pause_boundaries()
    deepest_line2 = [b for b in deeper_steps if b["line"] == 2][-1]["index"]
    pinned = shallow.compare(deep, right_boundary=deepest_line2)
    # The old/saved boundary is preserved even though deep kept moving
    # (boundary pinned, cursor shown separately for freshness).
    assert pinned["right"]["boundary"] == deepest_line2
    assert pinned["right"]["cursor"] == deep.cursor
    assert pinned["status"] == "ahead"
    # The pinned position itself is a real recorded pause with three
    # frames (main + fact(3) + fact(2)); line 2 alone never implied the
    # same event as shallow's two-frame line 2.
    pinned_steps = deep._executed_steps(deepest_line2)
    assert pinned_steps[-1]["line"] == 2
    assert len(pinned_steps[-1]["stack"]) == 3


def test_branches_survive_save_and_reopen(tmp_path):
    store = SessionStore(directory=tmp_path)
    parent = store.create(RECURSIVE)
    parent.set_breakpoints([2])
    parent.continue_()
    parent.continue_()
    store.touch(parent)
    edited = RECURSIVE.replace("n * 10", "n * 20")
    branch = parent.fork(
        source=edited,
        name="乘20",
        breakpoints=[2],
        bp_migrations=[{"oldLine": 2, "newLine": 2,
                        "fromHash": parent.source_hash,
                        "toHash": source_hash(edited)}],
    )
    store.register(branch)
    branch.continue_()
    store.touch(branch)
    branch_line = branch.state()["line"]

    fresh = SessionStore(directory=tmp_path)
    reopened_parent = fresh.get(parent.id)
    reopened_branch = fresh.get(branch.id)
    # Each branch resumes its own unfinished execution independently.
    assert len(reopened_parent.state()["stack"]) == 3
    assert reopened_branch.state()["line"] == branch_line
    assert reopened_branch.state()["source"] == edited
    assert reopened_branch.parent_id == parent.id
    assert reopened_branch.name == "乘20"
    # Confirmed breakpoint migration relations are preserved.
    assert reopened_branch.bp_migrations[0]["oldLine"] == 2
    assert reopened_branch.bp_migrations[0]["toHash"] == source_hash(edited)
    # Stepping one reopened branch does not move the other.
    reopened_branch.step_into()
    assert reopened_parent.state()["line"] != reopened_branch.state()["line"] or \
        reopened_parent.cursor != reopened_branch.cursor

    listed = {item["id"]: item for item in fresh.list_saved()}
    assert listed[branch.id]["parentId"] == parent.id
    assert listed[branch.id]["name"] == "乘20"


def _loop_pair(store):
    left = store.create(LOOP)
    right = store.register(left.fork(name="实验"))
    return left, right


def test_saved_comparison_is_pinned_and_reopenable(tmp_path):
    store = SessionStore(directory=tmp_path / "sessions")
    comparisons = ComparisonStore(directory=tmp_path / "comparisons")
    left, right = _loop_pair(store)
    left.set_breakpoints([4])
    left.continue_()  # i == 0
    right.set_breakpoints([4])
    right.continue_()
    right.continue_()  # i == 1: one side ahead

    result = left.compare(right)
    assert result["status"] == "ahead"
    record = comparisons.save(result)

    # Both branches keep moving afterwards; the saved record must not change.
    left.continue_()
    left.continue_()
    right.continue_()
    store.touch(left)
    store.touch(right)

    fresh = ComparisonStore(directory=tmp_path / "comparisons")
    reopened = fresh.get(record["id"])
    assert reopened["result"] == result
    assert reopened["result"]["left"]["cursor"] != left.cursor
    assert reopened["result"]["left"]["boundary"] == result["left"]["boundary"]
    # The pinned ahead event keeps its call site and variable values.
    event = reopened["result"]["right"]["event"]
    assert event is not None
    assert event["kind"] == "line"
    top = {v["name"]: v["value"]["display"] for v in event["stack"][-1]["locals"]}
    assert top["i"] == "0"

    summaries = fresh.list()
    assert [s["id"] for s in summaries] == [record["id"]]
    assert summaries[0]["status"] == "ahead"
    assert summaries[0]["leftId"] == left.id
    assert summaries[0]["rightId"] == right.id


def test_multiple_comparisons_same_pair_coexist(tmp_path):
    store = SessionStore(directory=tmp_path / "sessions")
    comparisons = ComparisonStore(directory=tmp_path / "comparisons")
    left, right = _loop_pair(store)

    zero = comparisons.save(left.compare(right))
    left.step_into()
    store.touch(left)
    one_sided = comparisons.save(left.compare(right))
    right.step_into()
    right.step_into()
    left.step_into()
    both = comparisons.save(left.compare(right))

    fresh = ComparisonStore(directory=tmp_path / "comparisons")
    ids = [s["id"] for s in fresh.list()]
    assert set(ids) == {zero["id"], one_sided["id"], both["id"]}
    # Zero-event comparison: empty executed sets are waiting, never
    # reported as whole-program equality.
    assert fresh.get(zero["id"])["result"]["status"] == "waiting"
    assert fresh.get(zero["id"])["result"]["left"]["executedCount"] == 0
    # One side moved first.
    assert fresh.get(one_sided["id"])["result"]["status"] == "ahead"
    # Both reached pauses; still waiting until both terminate.
    both_result = fresh.get(both["id"])["result"]
    assert both_result["status"] in {"waiting", "identical", "ahead"}
    # Records are independent: deleting one keeps the others.
    fresh.delete(one_sided["id"])
    remaining = {s["id"] for s in fresh.list()}
    assert remaining == {zero["id"], both["id"]}


def test_saved_comparison_survives_branch_abandon(tmp_path):
    store = SessionStore(directory=tmp_path / "sessions")
    comparisons = ComparisonStore(directory=tmp_path / "comparisons")
    left, right = _loop_pair(store)
    left.step_into()
    right.step_into()
    # A deeper descendant keeps its own explainable origin.
    grandchild = store.register(right.fork(name="后代"))
    record = comparisons.save(left.compare(right))

    # Abandon the intermediate experiment branch.
    store.delete(right.id)

    fresh = ComparisonStore(directory=tmp_path / "comparisons")
    reopened = fresh.get(record["id"])
    # The saved comparison keeps the abandoned branch's pinned facts.
    assert reopened["result"]["right"]["id"] == right.id
    assert reopened["result"]["status"] == "waiting"
    # The descendant branch still loads with its own source and parent link.
    fresh_store = SessionStore(directory=tmp_path / "sessions")
    loaded = fresh_store.get(grandchild.id)
    assert loaded.parent_id == right.id
    assert loaded.source == LOOP
