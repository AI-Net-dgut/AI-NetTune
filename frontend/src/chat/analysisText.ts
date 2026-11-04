export interface AnalysisSection { title: string; text: string }

const TITLES: Record<string, string> = {
  "结论": "研判结论", "发现问题": "发现问题", "依据": "判断依据", "判断依据": "判断依据",
  "原因": "原因与限制", "原因与限制": "原因与限制", "方案": "处理建议", "建议": "处理建议",
  "处理建议": "处理建议", "处理与预估": "处理与预估", "不确定性": "不确定性",
};

/** 只按回答中明确写出的标题分段。没有标题的文字原样展示，不推断缺失证据。 */
export function analysisSections(reply: string): AnalysisSection[] {
  const text = reply.trim();
  // 来源已有独立徽标；这里仅去除开头的同义来源标签，不删除任何判断内容。
  const body = text.replace(/^【(?:规则研判|AI研判)】\s*/, "");
  const labels = Object.keys(TITLES).join("|");
  const markers = new RegExp(`【(${labels})】[：:]?|(?:^|\\n)(?:#{1,6}\\s*)?(?:\\*\\*)?(${labels})(?:\\*\\*)?[：:](?:\\*\\*)?|(?<=[。；])\\s*(建议|不确定性)[：:]`, "g");
  const matches = [...body.matchAll(markers)];
  if (!matches.length) return [{ title: "研判结果", text: body || text }];
  const sections: AnalysisSection[] = [];
  const prefix = body.slice(0, matches[0].index).trim();
  if (prefix) sections.push({ title: "研判结果", text: prefix });
  matches.forEach((match, index) => {
    const content = body.slice(match.index + match[0].length, matches[index + 1]?.index).trim();
    if (content) sections.push({ title: TITLES[match[1] || match[2] || match[3]], text: content });
  });
  return sections.length ? sections : [{ title: "研判结果", text }];
}

/** 只移除开头与本次问题完全相同的独立复述行；归档仍保留接口原文。 */
export function withoutQuestionEcho(reply: string, question?: string): string {
  if (!question?.trim()) return reply;
  const source = reply.match(/^【(?:规则研判|AI研判)】\s*/)?.[0] ?? "";
  const body = reply.slice(source.length);
  const echo = `关于你的问题「${question}」`;
  if (!body.startsWith(echo)) return reply;
  const rest = body.slice(echo.length);
  // 同一行还有判断内容时，不能把它当作纯复述删除。
  if (rest && !/^[ \t]*\r?\n/.test(rest)) return reply;
  return `${source.trim()}${rest.trimStart()}`;
}
