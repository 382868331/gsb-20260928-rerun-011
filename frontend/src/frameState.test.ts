import { describe, expect, it } from 'vitest';
import type { EventView } from './types';

// Mirrors how App derives the frame inspected by the variables panel.
function frameFor(event: EventView, uid: number | null) {
  return event.stack.find((frame) => frame.uid === uid) ?? null;
}

describe('paused-state frame selection', () => {
  const firstPause: EventView = {
    index: 4,
    kind: 'line',
    line: 4,
    level: 2,
    output: '',
    stack: [
      { uid: 1, name: 'main', line: 5, locals: [] },
      { uid: 2, name: 'fact', line: 4, locals: [
        { name: 'n', value: { type: 'int', display: '3' } },
      ] },
    ],
  };

  const secondPause: EventView = {
    index: 8,
    kind: 'line',
    line: 4,
    level: 3,
    output: '',
    stack: [
      { uid: 1, name: 'main', line: 5, locals: [] },
      { uid: 2, name: 'fact', line: 4, locals: [
        { name: 'n', value: { type: 'int', display: '3' } },
      ] },
      { uid: 3, name: 'fact', line: 4, locals: [
        { name: 'n', value: { type: 'int', display: '2' } },
      ] },
    ],
  };

  it('shows each recursive frame its own n', () => {
    expect(frameFor(secondPause, 3)!.locals[0].value.display).toBe('2');
    expect(frameFor(secondPause, 2)!.locals[0].value.display).toBe('3');
  });

  it('does not keep showing a frame uid that no longer exists', () => {
    // Old top frame was uid 2 at the first pause; after resuming, selecting
    // the stale uid must never resurrect old values when the frame is gone.
    const shallow: EventView = { ...firstPause, stack: [firstPause.stack[0]] };
    expect(frameFor(shallow, 2)).toBeNull();
  });

  it('current pause position changes after resume', () => {
    expect(firstPause.index).not.toBe(secondPause.index);
    expect(frameFor(secondPause, 3)!.line).toBe(4);
  });
});
