import pytest

from backend.debugger.store import SessionStore
from backend.debugger.session import source_hash
from backend.server.app import create_app
from backend.tests.asgi_client import SimpleClient


LOOP = '''let i = 0
while i < 3
    i = i + 1
print(i)
'''

RECURSION = '''fun fact(n)
    if n <= 1
        return 1
    return n * fact(n - 1)
print(fact(4))
'''

ERROR_PROGRAM = '''fun boom(n)
    let q = 10 / n
    return q + boom(n - 1)
print(boom(2))
'''


@pytest.fixture()
def client(tmp_path):
    app = create_app(str(tmp_path / 'data'))
    yield SimpleClient(app)


def test_pause_save_reopen_continue_with_fresh_store(tmp_path):
    data_dir = tmp_path / 'data'
    store = SessionStore(data_dir)
    session = store.create(LOOP, name='循环会话', breakpoints=[3])
    sid = session.id
    store.update(sid, lambda s: s.resume())

    # Simulate closing the page/server: a brand new store reloads from disk.
    reopened_store = SessionStore(data_dir)
    reopened = reopened_store.get(sid)
    assert reopened.status == 'paused'
    assert reopened.current_event['line'] == 3
    assert reopened.breakpoints == {3}

    # Continue from the reloaded session and verify it can run to the end.
    reopened_store.update(sid, lambda s: s.set_breakpoints([]))
    state = reopened_store.update(sid, lambda s: s.resume()).state()
    assert state['status'] == 'ended'

    # Reload again: terminal state is persisted too.
    third_store = SessionStore(data_dir)
    assert third_store.get(sid).status == 'ended'


def test_changed_source_does_not_reuse_old_line_numbers(tmp_path):
    store = SessionStore(tmp_path / 'data')
    session = store.create(LOOP, name='旧程序', breakpoints=[3])
    old_id = session.id
    assert store.source_status(LOOP)['matching_session_ids'] == [old_id]

    status = store.source_status(RECURSION)
    assert status['matching_session_ids'] == []
    assert status['stale'] == [{'id': old_id, 'name': '旧程序'}]

    # Starting the changed program creates a fresh session with no breakpoints;
    # the old session stays available (its line numbers belong to its source).
    new_session = store.create(RECURSION, name='新程序')
    assert new_session.id != old_id
    assert new_session.breakpoints == set()
    assert store.get(old_id).source == LOOP
    assert store.get(old_id).breakpoints == {3}


def test_api_full_flow(client):
    # Start recursion program with a breakpoint, pause, inspect frames.
    created = client.post('/api/sessions', json={
        'source': RECURSION, 'name': '递归', 'breakpoints': [4],
    })
    assert created.status_code == 201
    state = created.json()
    assert state['status'] == 'paused'
    sid = state['id']

    resumed = client.post(f'/api/sessions/{sid}/resume').json()
    assert resumed['event']['line'] == 4
    top_frame = resumed['event']['stack'][-1]
    assert top_frame['name'] == 'fact'
    assert top_frame['locals'][0]['value']['display'] == '4'

    # Change breakpoints while paused: remove the old one, add another valid.
    changed = client.put(
        f'/api/sessions/{sid}/breakpoints', json={'lines': [4, 999]}
    ).json()
    assert 999 in {item['line'] for item in changed['rejected_breakpoints']}

    # Save, list, reopen by GET (simulating page reload), then continue/end.
    assert client.post(f'/api/sessions/{sid}/save').json()['saved']
    listing = client.get('/api/sessions').json()['sessions']
    assert any(item['id'] == sid for item in listing)

    reopened = client.get(f'/api/sessions/{sid}').json()
    assert reopened['event']['stack'][-1]['locals'][0]['value']['display'] == '4'

    client.put(f'/api/sessions/{sid}/breakpoints', json={'lines': []})
    ended = client.post(f'/api/sessions/{sid}/resume').json()
    assert ended['status'] == 'ended'
    assert ended['event']['kind'] == 'end'


def test_api_runtime_error_survives_reopen(client):
    state = client.post('/api/sessions', json={
        'source': ERROR_PROGRAM, 'name': '错误程序', 'breakpoints': [],
    }).json()
    sid = state['id']
    ended = client.post(f'/api/sessions/{sid}/resume').json()
    assert ended['status'] == 'error'
    assert ended['error']['message'] == '除以零'
    boom = [f for f in ended['error']['stack'] if f['name'] == 'boom']
    assert [f['locals'][0]['value']['display'] for f in boom] == ['2', '1', '0']

    # After the program exits, reopen still shows the last error and chain.
    reopened = client.get(f'/api/sessions/{sid}').json()
    assert reopened['status'] == 'error'
    assert len(reopened['error']['stack']) == len(ended['error']['stack'])

    # Stepping a finished program is a 409 conflict.
    response = client.post(f'/api/sessions/{sid}/step/into')
    assert response.status_code == 409


def test_api_compile_error_and_source_check(client):
    response = client.post('/api/sessions', json={'source': 'let x =\n'})
    assert response.status_code == 400
    assert response.json()['code'] == 'compile_error'

    check = client.post('/api/source-check', json={'source': LOOP}).json()
    assert check['compiles'] is True
    assert check['source_hash'] == source_hash(LOOP)
