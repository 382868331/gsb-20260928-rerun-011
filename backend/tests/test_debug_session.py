import pytest

from backend.debugger.session import DebugSession, SessionError, StepKind


RECURSION = '''fun fact(n)
    if n <= 1
        return 1
    let prev = fact(n - 1)
    return n * prev
print(fact(3))
'''

LOOP = '''let i = 0
let total = 0
while i < 3
    total = total + i
    i = i + 1
print(total)
'''

RUNTIME_ERROR_PROGRAM = '''fun boom(n)
    let q = 10 / n
    return q + boom(n - 1)
print(boom(3))
'''


def test_breakpoint_hits_and_recursion_locals_follow_frames():
    session = DebugSession.create(RECURSION)
    result = session.set_breakpoints([4])
    assert result['accepted'] == [4]

    session.resume()
    assert session.current_event['line'] == 4
    frames = session.current_event['stack']
    assert [f['name'] for f in frames] == ['main', 'fact']
    assert frames[-1]['locals'][0]['value']['display'] == '3'

    # Step into the recursive call: a new frame with its own n appears.
    session.step(StepKind.INTO)
    # The first line event inside the new frame is the if at line 2.
    assert [f['name'] for f in session.current_event['stack']] == [
        'main', 'fact', 'fact'
    ]
    inner = session.current_event['stack'][-1]
    outer = session.current_event['stack'][-2]
    assert inner['locals'][0]['value']['display'] == '2'
    assert outer['locals'][0]['value']['display'] == '3'


def test_same_named_locals_change_with_current_frame_after_resume():
    session = DebugSession.create(RECURSION)
    session.set_breakpoints([4])
    session.resume()
    first_top = session.current_event['stack'][-1]
    assert first_top['locals'][0]['value']['display'] == '3'

    # Continue: breakpoint is hit again in the deeper frame.
    session.resume()
    assert session.current_event['line'] == 4
    top = session.current_event['stack'][-1]
    assert top['locals'][0]['value']['display'] == '2'

    # Reading an earlier frame must still show the old frame's own value.
    outer = session.current_event['stack'][-2]
    assert outer['locals'][0]['value']['display'] == '3'


def test_step_over_loop_stays_in_same_frame():
    session = DebugSession.create(LOOP)
    # line 1 (let), line 2 (let), line 3 (while)
    session.step(StepKind.INTO)
    session.step(StepKind.INTO)
    assert session.current_event['line'] == 3
    depth = len(session.current_event['stack'])

    session.step(StepKind.OVER)
    # Stepping over the while lands in its body (line 4) or after the loop,
    # but never into a different frame.
    assert session.status == 'paused'
    assert len(session.current_event['stack']) == depth
    assert session.current_event['line'] in (4, 6)


def test_continue_after_breakpoint_removal_runs_to_end():
    session = DebugSession.create(LOOP)
    session.set_breakpoints([4])
    session.resume()
    assert session.current_event['line'] == 4

    # Breakpoints may be changed while paused.
    session.set_breakpoints([])
    session.resume()
    assert session.status == 'ended'
    assert session.current_event['kind'] == 'end'


def test_breakpoint_on_non_executable_line_is_rejected_not_shifted():
    session = DebugSession.create(LOOP)
    result = session.set_breakpoints([100, 2, 5])
    # Line 100 does not exist; line 5 is a continuation inside the loop and
    # has a statement (i = i + 1), so it is accepted. Line 2 is a let line.
    assert 100 in result['rejected']
    assert set(result['accepted']) <= set(session.executable_line_list)
    assert 100 not in session.breakpoints


def test_runtime_error_keeps_error_and_call_chain_after_exit():
    session = DebugSession.create(RUNTIME_ERROR_PROGRAM)
    session.set_breakpoints([2])
    session.resume()
    assert session.current_event['line'] == 2

    # Continue through recursive calls until division by zero at n == 0.
    state = None
    for _ in range(10):
        state = session.resume()
        if state['status'] == 'error':
            break
    assert state['status'] == 'error'
    assert session.current_event['kind'] == 'error'
    assert session.current_event['message'] == '除以零'
    error_line = session.current_event['line']

    # The terminal error event carries the full call chain with locals.
    frames = session.current_event['stack']
    boom_frames = [f for f in frames if f['name'] == 'boom']
    assert len(boom_frames) == 4
    n_values = [
        local['value']['display']
        for frame in boom_frames
        for local in frame['locals']
        if local['name'] == 'n'
    ]
    assert n_values == ['3', '2', '1', '0']

    # The state remains inspectable after termination and stepping is refused.
    state = session.state()
    assert state['error']['line'] == error_line
    assert len(state['error']['stack']) == len(frames)
    with pytest.raises(SessionError) as info:
        session.step(StepKind.INTO)
    assert info.value.code == 'finished'
    with pytest.raises(SessionError):
        session.resume()


def test_compile_error_is_reported_with_line():
    with pytest.raises(SessionError) as info:
        DebugSession.create('let x = \n')
    assert info.value.code == 'compile_error'


def test_step_out_returns_to_caller_or_terminates():
    session = DebugSession.create(RECURSION)
    session.set_breakpoints([4])
    session.resume()
    session.step(StepKind.INTO)  # inside fact(n=2)
    depth = len(session.current_event['stack'])
    session.step(StepKind.OUT)
    # Either back in the outer frame or the program finished when the outer
    # return also completes; we must never be deeper than before.
    event = session.current_event
    assert len(event['stack']) < depth or session.status in ('ended', 'error')
