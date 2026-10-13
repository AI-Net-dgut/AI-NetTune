import { Link } from "react-router-dom";
import { REPLAY_CONFIG, REPLAY_SPEEDS } from "../../replay/config";
import "./HomePage.css";

const capabilities = [
  {
    kind: "observe",
    title: "观察网络，定位异常",
    description: "空间主图、网络概括、指标趋势与用户横向对比同步展示，选中对象后查看位置和服务连接。",
    tags: ["空间分布", "指标趋势", "用户对比"],
    question: "哪里出现了网络问题？",
  },
  {
    kind: "diagnose",
    title: "固定时刻，核查原因",
    description: "用自然语言询问异常与处理建议，结合回答中的问题、候选原因和证据图表，理解判断依据。",
    tags: ["自然语言", "原因研判", "证据图表"],
    question: "为什么变差，依据是什么？",
  },
  {
    kind: "plan",
    title: "比较方案，查看预计效果",
    description: "查看处理建议，修改支持编辑的参数并导出方案草稿；预计效果根据后端返回展示，缺失时提示待评估。",
    tags: ["方案建议", "参数编辑", "预计效果"],
    question: "怎样调整，预计有什么改善？",
  },
] as const;

const workflow = [
  { title: "进入工作台", description: "从1月2日09:15开始演示，数据加载后自动回放。" },
  { title: "固定当前时刻", description: "主时钟继续运行；也可查看已到达的历史时刻。" },
  { title: "提问并核查证据", description: "可指定历史日期/时段提问，图表同步；也可固定当前查看证据。" },
  { title: "查看方案与预计效果", description: "比较建议、调整参数，退出分析后返回当前。" },
];

function FeatureIcon({ kind }: { kind: typeof capabilities[number]["kind"] }) {
  return <svg viewBox="0 0 24 24" fill="none" aria-hidden="true" focusable="false">
    {kind === "observe" ? <>
      <path d="M6 7 12 5 18 9M6 7 8 18 18 9M12 5 8 18" />
      <circle cx="6" cy="7" r="2" /><circle cx="12" cy="5" r="2" />
      <circle cx="18" cy="9" r="2" /><circle cx="8" cy="18" r="2" />
    </> : kind === "diagnose" ? <>
      <path d="M5 4h14v12H9l-4 4V4ZM8 8h8M8 12h5" />
    </> : <>
      <path d="M5 5v14h14M9 15v-4M13 15V8M17 15V5" />
      <path d="m8 6 4-3 5 1" />
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
      <small>主时钟持续运行 · 历史查看 · 倍速 · 循环</small>
    </div>
    <div className="ain-home-v2-preview-ai">
      <div className="ain-home-v2-preview-ai-heading"><strong>AI 交互</strong><span>固定时刻分析</span></div>
      <div className="ain-home-v2-preview-answer"><span>问题研判与证据</span><span>处理方案与预计效果</span></div>
    </div>
    <div className="ain-home-v2-preview-grid">
      <div className="ain-home-v2-preview-map">
        <strong>网络空间主图</strong>
        <svg viewBox="0 0 280 100" fill="none" aria-hidden="true" focusable="false">
          <g stroke="currentColor" strokeWidth="1.2" opacity=".5">
            <path d="M18 25h244M18 50h244M18 75h244M55 10v80M110 10v80M165 10v80M220 10v80" />
          </g>
          <g stroke="#61bfc5" strokeWidth="1.8"><path d="m68 38 39 39M68 38 75 75M183 35 218 72M183 35 160 73M183 35 240 38" /></g>
          <g fill="#dff9f4" stroke="#79dad6" strokeWidth="1.5">
            <rect x="62" y="32" width="12" height="12" rx="3" /><rect x="177" y="29" width="12" height="12" rx="3" />
          </g>
          <g fill="#93bddc"><circle cx="107" cy="77" r="4" /><circle cx="75" cy="75" r="4" /><circle cx="218" cy="72" r="4" /><circle cx="160" cy="73" r="4" /><circle cx="240" cy="38" r="4" /></g>
          <g fill="#c5e8f2" fontSize="10"><text x="59" y="24">AP</text><text x="175" y="22">AP</text></g>
        </svg>
        <span>位置 · 覆盖 · 服务连接</span>
      </div>
      <div className="ain-home-v2-preview-overview"><strong>网络概括</strong><span>对象与核心指标</span><span>展开历史连接</span></div>
      <div className="ain-home-v2-preview-trend"><strong>指标趋势</strong><span>看清随时间的变化</span></div>
      <div className="ain-home-v2-preview-compare"><strong>用户横向对比</strong><span>选中用户高亮</span></div>
    </div>
    <p className="ain-home-v2-preview-note">实际采样、分析范围和结果以工作台展示为准。</p>
  </aside>;
}

export default function HomePage() {
  return <main className="ain-home-v2">
    <section className="ain-home-v2-hero" aria-labelledby="ain-home-v2-title">
      <div className="ain-home-v2-intro">
        <span className="ain-home-v2-eyebrow">AI-NetTune / 无线网络诊断与仿真</span>
        <h1 id="ain-home-v2-title">看见网络变化，<br /><em>让每个判断有据可循。</em></h1>
        <p className="ain-home-v2-lead">随历史数据观察网络，在关注的时刻固定视图提问，将问题研判、证据核查、处理建议和预计效果放在同一个工作台。</p>
        <div className="ain-home-v2-actions">
          <Link className="ain-button ain-primary" to="/workbench">进入网络工作台 <span aria-hidden="true">→</span></Link>
          <a className="ain-home-v2-guide-link" href="#ain-home-v2-flow">了解使用流程 <span aria-hidden="true">↗</span></a>
        </div>
        <dl className="ain-home-v2-source">
          <div><dt>当前案例</dt><dd>{REPLAY_CONFIG.scenario} · 七天历史回放</dd></div>
          <div><dt>数据时段 · 北京时间</dt><dd>{REPLAY_CONFIG.start} — {REPLAY_CONFIG.end}</dd></div>
          <div><dt>基础速度</dt><dd>默认{REPLAY_CONFIG.initialSpeed}× · 1×时现实{REPLAY_CONFIG.realSecondsPerMinute}秒推进模拟1分钟</dd></div>
        </dl>
      </div>
      <WorkspaceIllustration />
    </section>

    <section className="ain-home-v2-capabilities" aria-labelledby="ain-home-v2-capabilities-title">
      <div className="ain-home-v2-section-heading"><h2 id="ain-home-v2-capabilities-title">从网络现象，到有依据的处理建议</h2><p>观察、研判与方案集中在同一页面</p></div>
      <div className="ain-home-v2-feature-grid">
        {capabilities.map(item => <article className="ain-home-v2-feature" key={item.kind}>
          <div className="ain-home-v2-feature-title"><span className="ain-home-v2-icon"><FeatureIcon kind={item.kind} /></span><h3>{item.title}</h3></div>
          <p>{item.description}</p>
          <div className="ain-home-v2-tags">{item.tags.map(tag => <span key={tag}>{tag}</span>)}</div>
          <p className="ain-home-v2-question">{item.question}</p>
        </article>)}
      </div>
    </section>

    <section className="ain-home-v2-flow" id="ain-home-v2-flow" aria-labelledby="ain-home-v2-flow-title">
      <div className="ain-home-v2-section-heading"><h2 id="ain-home-v2-flow-title">如何开始</h2><p>固定AI与数据查看时刻，主时钟继续运行；退出后同步当前</p></div>
      <ol className="ain-home-v2-steps">{workflow.map(step => <li key={step.title}><h3>{step.title}</h3><p>{step.description}</p></li>)}</ol>
    </section>

    <section className="ain-home-v2-status" aria-labelledby="ain-home-v2-status-title">
      <div className="ain-home-v2-status-title"><h2 id="ain-home-v2-status-title">当前功能状态</h2><span>以现有接口返回为准</span></div>
      <div className="ain-home-v2-status-item"><span className="ain-home-v2-badge">已具备</span><p><strong>历史回放与AI研判</strong>支持{REPLAY_SPEEDS.join(" / ")}倍速和循环；默认近{REPLAY_CONFIG.historyMinutes}分钟参考，可调整时长或自定义开始时间；仅使用截至查看时刻的采样。</p></div>
      <div className="ain-home-v2-status-item"><span className="ain-home-v2-badge is-pending">待接入</span><p><strong>未来预测与预警</strong>入口已预留，将与现存问题共用AI交互；当前尚未提供未来预测结果。</p></div>
      <div className="ain-home-v2-status-item"><span className="ain-home-v2-badge is-limited">临时记录</span><p><strong>工作台内查询摘要</strong>保留最近6次提问摘要及分析截止时间，离开页面或刷新后不保留；独立分析记录页尚未接入保存功能。</p></div>
      <p className="ain-home-v2-boundary">本阶段通过历史数据回放观察网络，处理方案用于参数讨论与预计效果比较，不下发真实网络配置。</p>
    </section>
  </main>;
}
