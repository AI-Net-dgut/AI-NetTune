import type { ReactNode } from "react";
import "./AnalysisNarrative.css";

// 仅渲染文字与加粗，不把助手文本作为 HTML 执行。
export function ReplyText({ text }: { text: string }) {
  return <div className="ain-reply-text">{text.split(/\n\s*\n/).filter(Boolean).map((paragraph, i) =>
    <p key={i}>{paragraph.split(/(\*\*[^*]+\*\*)/g).map((part, j) =>
      part.startsWith("**") && part.endsWith("**") ? <strong key={j}>{part.slice(2, -2)}</strong> : part)}</p>)}</div>;
}

// 共用排版；接口仅返回自由文字时完整展示，不拼造结构化证据。
export default function AnalysisNarrative({ source, sections, afterSection, note }: {
  source: string;
  sections: { title: string; text: string }[];
  afterSection?: (title: string) => ReactNode;
  note?: string;
}) {
  return <div className="ain-analysis-narrative">
    <div className="ain-analysis-source" title={note}>{source}</div>
    {sections.map((section, index) => <section className="ain-analysis-paragraph" key={`${section.title}-${index}`}>
      <h4>{section.title}</h4><ReplyText text={section.text} />{afterSection?.(section.title)}
    </section>)}
  </div>;
}
