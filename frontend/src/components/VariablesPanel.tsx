import type { FrameView } from '../types';

type Props = {
  frame: FrameView | null;
};

export function VariablesPanel({ frame }: Props) {
  if (!frame) {
    return <div className="empty-hint">没有可查看的栈帧。</div>;
  }
  return (
    <div className="variables">
      <div className="frame-title">
        栈帧 {frame.name} · 第 {frame.line} 行
      </div>
      {frame.locals.length === 0 ? (
        <div className="empty-hint">该栈帧暂无局部变量。</div>
      ) : (
        <table className="variables-table">
          <thead>
            <tr>
              <th>变量名</th>
              <th>类型</th>
              <th>当前值</th>
            </tr>
          </thead>
          <tbody>
            {frame.locals.map((local) => (
              <tr key={local.name}>
                <td className="var-name">{local.name}</td>
                <td className="var-type">{local.value.type}</td>
                <td className="var-value">{local.value.display}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div className="variables-note">
        递归调用的同名变量分属不同栈帧；切换上方帧即可对比各层值，继续执行后表格刷新为新暂停位置的值。
      </div>
    </div>
  );
}
