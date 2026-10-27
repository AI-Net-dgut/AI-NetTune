import { Component } from 'react';
import type { ErrorInfo, ReactNode } from 'react';
export default class HistoryErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state: { error: Error | null } = { error: null };
  static getDerivedStateFromError(error: Error) { return { error }; }
  componentDidCatch(error: Error, info: ErrorInfo) { console.error('分析记录前端渲染失败', error, info.componentStack); }
  render() {
    if (!this.state.error) return this.props.children;
    return <div className="history-error" role="alert"><strong>这部分记录无法显示（前端渲染错误）</strong>
      <p>记录没有被删除。可以选择其他问题，或把下方错误信息反馈给前端同学。</p>
      <details><summary>查看错误信息</summary><p>{this.state.error.message || '未知渲染错误'}</p></details>
      <button type="button" onClick={() => this.setState({ error: null })}>重试显示</button></div>;
  }
}
