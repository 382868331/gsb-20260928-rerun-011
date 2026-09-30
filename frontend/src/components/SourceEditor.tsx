import { useMemo } from 'react';

type Props = {
  source: string;
  currentLine: number | null;
  breakpoints: number[];
  executableLines: number[];
  frameLines: number[];
  readOnly: boolean;
  onSourceChange: (source: string) => void;
  onToggleBreakpoint: (line: number) => void;
};

export function SourceEditor({
  source,
  currentLine,
  breakpoints,
  executableLines,
  frameLines,
  readOnly,
  onSourceChange,
  onToggleBreakpoint,
}: Props) {
  const lines = useMemo(() => source.split('\n'), [source]);
  const executable = new Set(executableLines);
  const bp = new Set(breakpoints);
  const frameSet = new Set(frameLines);

  return (
    <div className="source-editor">
      <div className="editor-scroll">
        <div className="code-lines">
          {lines.map((text, index) => {
            const line = index + 1;
            const isCurrent = line === currentLine;
            const isBp = bp.has(line);
            const isExecutable = executable.has(line);
            const onFrame = frameSet.has(line);
            return (
              <div
                key={index}
                className={[
                  'code-line',
                  isCurrent ? 'current' : '',
                  onFrame && !isCurrent ? 'frame-line' : '',
                ].join(' ')}
              >
                <button
                  type="button"
                  className={[
                    'gutter',
                    isBp ? 'breakpoint' : '',
                    isExecutable ? 'executable' : '',
                  ].join(' ')}
                  title={
                    isBp
                      ? '点击移除断点'
                      : isExecutable
                        ? '点击添加断点'
                        : '该行没有可执行语句'
                  }
                  onClick={() => onToggleBreakpoint(line)}
                >
                  {isBp ? '●' : isExecutable ? '○' : ''}
                </button>
                <span className="line-number">{line}</span>
                <span className="line-text">{text || ' '}</span>
              </div>
            );
          })}
        </div>
        {!readOnly && (
          <textarea
            className="source-textarea"
            aria-label="源代码编辑器"
            value={source}
            spellCheck={false}
            onChange={(event) => onSourceChange(event.target.value)}
          />
        )}
      </div>
      <div className="editor-note">
        {readOnly
          ? '会话运行中：编辑器只读。要修改源代码，请点击“结束并编辑新程序”，旧会话的断点不会套用到新程序。'
          : '编辑后请点击“编译检查”，再开始调试。'}
      </div>
    </div>
  );
}
