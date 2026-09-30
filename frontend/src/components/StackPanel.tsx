import type { FrameView } from '../types';

type Props = {
  frames: FrameView[];
  selectedUid: number | null;
  onSelect: (uid: number) => void;
};

export function StackPanel({ frames, selectedUid, onSelect }: Props) {
  if (!frames.length) {
    return <div className="empty-hint">当前没有调用栈。</div>;
  }
  return (
    <ol className="stack-list">
      {[...frames].reverse().map((frame) => (
        <li key={frame.uid}>
          <button
            type="button"
            className={
              frame.uid === selectedUid ? 'stack-frame selected' : 'stack-frame'
            }
            onClick={() => onSelect(frame.uid)}
          >
            <span className="frame-name">{frame.name}</span>
            <span className="frame-line">第 {frame.line} 行</span>
          </button>
        </li>
      ))}
    </ol>
  );
}
