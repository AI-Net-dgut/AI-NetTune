import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { historyProblem } from './store';

export default function HistoryNotice() {
  const [text, setText] = useState(historyProblem);
  useEffect(() => {
    const update = () => setText(historyProblem());
    window.addEventListener('aintune-history', update);
    return () => window.removeEventListener('aintune-history', update);
  }, []);
  return text ? <div className="history-save-warning" role="alert">{text}<Link to="/history">查看与导出记录</Link></div> : null;
}
