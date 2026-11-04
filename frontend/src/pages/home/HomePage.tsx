import { useEffect } from "react";
import { Link, useLocation } from "react-router-dom";
import { FORECAST_HORIZONS, FORECAST_LIMITS } from "../../forecast/config";
import { MAX_HISTORY_QUESTIONS } from "../../history/store";
import { REPLAY_CONFIG, REPLAY_SPEEDS } from "../../replay/config";
import "./HomePage.css";

const capabilities = [
  {
    kind: "observe",
    title: "观察与研判",
    description: "联动查看空间分布、指标趋势与用户对比；固定时刻提问，核查问题、原因和证据。",
    tags: ["历史回放", "固定时刻", "证据核查"],
    guide: "replay-guide",
    question: "如何回放与选择分析时刻",
  },
  {
    kind: "forecast",
    title: "预测与预警",
    description: "查看未来频谱风险与区域态势，结合预测基准、覆盖范围和证据理解趋势变化。",
    tags: ["频谱风险", "区域态势", "未来时段"],
    guide: "forecast-guide",
    question: "如何理解预测范围与更新",
  },
  {
    kind: "plan",
    title: "方案与评估",
    description: "比较候选方案，调整可编辑参数并重新评估，在相同历史基线上比较预计效果。",
    tags: ["候选方案", "参数调整", "效果对比"],
    guide: "simulation-guide",
    question: "如何调整参数与比较结果",
  },
  {
    kind: "record",
    title: "分析记录",
    description: "按问题回看研判、证据、方案与历次评估，保留每次参数和结果，支持搜索与导出。",
    tags: ["问题归档", "评估时间线", "导出备份"],
    guide: "record-guide",
    question: "如何查找与保存分析记录",
  },
] as const;

function FeatureIcon({ kind }: { kind: typeof capabilities[number]["kind"] }) {
  return <svg viewBox="0 0 24 24" fill="none" aria-hidden="true" focusable="false">
    {kind === "observe" ? <>
      <path d="M6 7 12 5 18 9M6 7 8 18 18 9M12 5 8 18" />
      <circle cx="6" cy="7" r="2" /><circle cx="12" cy="5" r="2" />
      <circle cx="18" cy="9" r="2" /><circle cx="8" cy="18" r="2" />
    </> : kind === "forecast" ? <>
      <path d="M4 18h16M5 14l5-5 4 3 5-7M15 5h4v4" />
    </> : kind === "plan" ? <>
      <path d="M5 5v14h14M9 15v-4M13 15V8M17 15V5" />
      <path d="m8 6 4-3 5 1" />
    </> : <>
      <path d="M6 4h12v16H6V4ZM9 8h6M9 12h6M9 16h4" />
    </>}
  </svg>;
}

// 静态结构示意不请求接口，不作为网络数据或AI结果展示。
function WorkspaceIllustration() {
  return <aside className="ain-home-v2-preview" aria-label="工作台结构示意，非实时数据">
    <div className="ain-home-v2-preview-heading">
      <strong>一个工作台，连接观察与研判</strong>
      <span>功能示意</span>
    </div>
    <div className="ain-home-v2-preview-clock">
      <span><i aria-hidden="true" />历史数据回放</span>
      <small>暂停 / 继续 · 历史查看 · 倍速 · 循环</small>
    </div>
    <div className="ain-home-v2-preview-workspace">
      <div className="ain-home-v2-preview-data">
        <div className="ain-home-v2-preview-tabs"><strong>数据展示</strong><span>问题研判</span><span>方案效果</span></div>
        <div className="ain-home-v2-preview-map">
          <svg viewBox="0 0 280 100" fill="none" aria-hidden="true" focusable="false">
            <g stroke="#7bb0c1" opacity=".4"><path d="M18 25h244M18 50h244M18 75h244M55 10v80M110 10v80M165 10v80M220 10v80" /></g>
            <g stroke="#61bfc5" strokeWidth="2"><path d="m68 38 39 39M68 38 75 75M183 35 218 72M183 35 160 73" /></g>
            <g fill="#dff9f4"><rect x="62" y="32" width="12" height="12" rx="3" /><rect x="177" y="29" width="12" height="12" rx="3" /></g>
            <g fill="#93bddc"><circle cx="107" cy="77" r="4" /><circle cx="75" cy="75" r="4" /><circle cx="218" cy="72" r="4" /><circle cx="160" cy="73" r="4" /></g>
          </svg>
          <span>空间分布 · 指标趋势</span>
        </div>
        <p className="ain-home-v2-preview-path">提问后查看研判 → 选择方案并比较效果</p>
      </div>
      <div className="ain-home-v2-preview-ai">
        <strong>AI 网络助手</strong><span>固定分析时刻</span>
        <div className="ain-home-v2-preview-input">输入网络问题</div>
        <span>现存分析 / 预测研判</span>
      </div>
    </div>
    <p className="ain-home-v2-preview-note">实际采样、分析范围和结果以工作台展示为准。</p>
  </aside>;
}

export default function HomePage() {
  const guideContent = {
    "replay-guide": <>
          <p>数据加载后自动回放。主时钟控制数据进度；固定分析时刻或选择历史只改变查看与提问范围，主时钟仍可暂停或继续。退出分析后回到当前。</p>
          <p>历史查看仅限主时钟已到达的时段。默认参考近 {REPLAY_CONFIG.historyMinutes} 分钟，可调整时长或自定义起点。</p>
          <div className="ain-home-v2-speed-guide"><strong>回放速度</strong><span>1×：现实 {REPLAY_CONFIG.realSecondsPerMinute} 秒 → 数据 1 分钟</span><span>默认 {REPLAY_CONFIG.initialSpeed}×：现实 1 秒 → 数据 {Math.round((60 / REPLAY_CONFIG.realSecondsPerMinute) * (REPLAY_CONFIG.initialSpeed ?? 1))} 秒</span><small>支持 {REPLAY_SPEEDS.join(" / ")}×；循环后返回演示起点。</small></div>
            </>,
    "forecast-guide": <>
          <p>打开预测弹窗选择未来 {FORECAST_HORIZONS[0]} 分钟至 {FORECAST_HORIZONS[FORECAST_HORIZONS.length - 1] / 60} 小时，或直接提问未来时段。范围表示从基准时刻起覆盖多久，不代表能提前同样长的时间发现问题。</p>
          <p>开启跟随后，弹窗打开、处于实时视图且回放中时，每 {FORECAST_LIMITS.followStepMinutes} 个模拟分钟更新；暂停或固定分析时需手动刷新，关闭弹窗后停止自动更新。</p>
          <details><summary>预测方法与图表口径</summary><p>当前使用历史趋势外推，未接入训练后的预测模型。频谱预警依据频段功率与占用率；区域态势依据 RSRP、SINR 或吞吐采样估算，两者历史取样窗口可不同，需分别查看时间范围。</p></details>
            </>,
    "simulation-guide": <>
          <p>研判后选择候选方案，修改允许编辑的参数并重新评估，也可导出方案草稿。候选方案来自预设模板，参数变更后需重新计算预计效果。</p>
          <p>评估以同一历史窗口的采样为基线，按经验系数估算配置变化的影响，用于比较方案；它不是未来实测结果，也不会下发真实网络配置。</p>
          <details><summary>如何回看多次尝试</summary><p>建议参数的评估与手动调整后的评估分别保存。记录中可按时间对照每组参数及其结果，失败的评估和未评估的草稿也会保留状态。</p></details>
            </>,
    "record-guide": <>
          <p>在分析记录页按问题回看研判、证据、方案参数与历次评估，支持搜索、导出和批量删除。工作台内的查询摘要用于当前会话快速回看。</p>
          <p>记录保存在当前浏览器，最多 {MAX_HISTORY_QUESTIONS} 个问题及其关联内容；满额后需先导出或删除部分记录，已有记录不会自动淘汰。</p>
          <p className="ain-home-v2-guide-note">同一浏览器的标签页可同步，记录不会自动共享给他人或其他设备。需要留存时请导出备份。</p>
                  <Link to="/history">查看分析记录 →</Link>
    </>,
  };
  const { hash, key } = useLocation();
  useEffect(() => {
    if (!hash) return;
    const frame = requestAnimationFrame(() => {
      const target = document.getElementById(hash.slice(1));
      if (target instanceof HTMLDetailsElement) target.open = true;
      target?.scrollIntoView({ block: "start" });
      (target instanceof HTMLDetailsElement ? target.querySelector("summary") : target)?.focus({ preventScroll: true });
    });
    return () => cancelAnimationFrame(frame);
  }, [hash, key]);

  return <main className="ain-home-v2">
    <section className="ain-home-v2-hero" aria-labelledby="ain-home-v2-title">
      <div className="ain-home-v2-intro">
        <span className="ain-home-v2-eyebrow">AI-NetTune / 无线网络诊断与仿真</span>
        <h1 id="ain-home-v2-title">看见网络变化，<br /><em>让每个判断有据可循。</em></h1>
        <p className="ain-home-v2-lead">在工作台依次查看数据、研判问题、比较方案；右侧 AI 助手负责提问，分析记录保留每次结果。</p>
        <div className="ain-home-v2-actions">
          <Link className="ain-button ain-primary" to="/workbench">进入网络工作台 <span aria-hidden="true">→</span></Link>
          <Link className="ain-home-v2-guide-link" to="#ain-home-v2-flow">查看使用指南 <span aria-hidden="true">↗</span></Link>
        </div>
        <dl className="ain-home-v2-source">
          <div><dt>当前案例</dt><dd>{REPLAY_CONFIG.scenario} · 七天历史回放</dd></div>
          <div><dt>数据时段 · 北京时间</dt><dd>{REPLAY_CONFIG.start} — {REPLAY_CONFIG.end}</dd></div>
          <div><dt>演示起点</dt><dd>{REPLAY_CONFIG.playbackStart} · 默认 {REPLAY_CONFIG.initialSpeed}×</dd></div>
        </dl>
      </div>
      <WorkspaceIllustration />
    </section>

    <section className="ain-home-v2-capabilities" id="ain-home-v2-flow" tabIndex={-1} aria-labelledby="ain-home-v2-capabilities-title">
      <div className="ain-home-v2-section-heading"><h2 id="ain-home-v2-capabilities-title">从网络变化，到方案比较</h2><p>四项能力，串起一次完整分析</p></div>
      <div className="ain-home-v2-feature-grid">
        {capabilities.map(item => <article className="ain-home-v2-feature" key={item.kind}>
          <div className="ain-home-v2-feature-title"><span className="ain-home-v2-icon"><FeatureIcon kind={item.kind} /></span><h3>{item.title}</h3></div>
          <p>{item.description}</p>
          <div className="ain-home-v2-tags">{item.tags.map(tag => <span key={tag}>{tag}</span>)}</div>
          <details className="ain-home-v2-card-guide" id={item.guide}>
            <summary>{item.question}</summary>
            <div className="ain-home-v2-guide-content">{guideContent[item.guide]}</div>
          </details>
        </article>)}
      </div>
    </section>

  </main>;
}
