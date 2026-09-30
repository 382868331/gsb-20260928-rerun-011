export const EXAMPLES: Record<string, { label: string; source: string }> = {
  recursion: {
    label: '递归：阶乘（同名局部变量跟随栈帧）',
    source: [
      'fun fact(n)',
      '    if n <= 1',
      '        return 1',
      '    let prev = fact(n - 1)',
      '    return n * prev',
      'print(fact(4))',
      '',
    ].join('\n'),
  },
  loop: {
    label: '循环：累加求和',
    source: [
      'let i = 1',
      'let total = 0',
      'while i <= 5',
      '    total = total + i',
      '    i = i + 1',
      'print(total)',
      '',
    ].join('\n'),
  },
  error: {
    label: '运行错误：递归中除以零',
    source: [
      'fun risky(n)',
      '    let q = 10 / n',
      '    return q + risky(n - 1)',
      'print(risky(3))',
      '',
    ].join('\n'),
  },
};
