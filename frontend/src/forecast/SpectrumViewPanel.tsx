import { useMemo, useState } from "react";
import ChartPanel from "../components/charts/ChartPanel";
import SpectrumChart from "./SpectrumChart";
import { spectrumCurveOption } from "./charts";
import { alertForSpectrum, defaultSpectrumFrame, resolveSpectrumViews, spectrumFrames } from "./spectrumModel";
import type { ForecastAlert, ForecastResult, SpectrumView } from "./types";

export default function SpectrumViewPanel({ result, alert, focusTime }: { result: ForecastResult; alert?: ForecastAlert; focusTime?: string }) {
  const model = useMemo(() => resolveSpectrumViews(result, alert), [result, alert]);
  const [choice, setChoice] = useState<{ result: ForecastResult; alertId: string | null; view: SpectrumView; frame: number | null } | null>(null);
  // 在上下文变化时真正丢弃选择，避免A→B→A时旧曲线/历史时段重新出现。
  if (choice && (choice.result !== result || choice.alertId !== (alert?.id ?? null))) setChoice(null);
  const manual = choice?.result === result && choice.alertId === (alert?.id ?? null) ? choice : null;
  const selected = manual && model.views.some(v => v.id === manual.view && v.available) ? manual.view : model.defaultView;
  const spectrum = selected === "occupancy" ? model.occupancy : model.power;
  const mappedAlert = spectrum ? alertForSpectrum(result, alert, spectrum) : undefined;
  const frames = useMemo(() => model.power ? spectrumFrames(model.power) : [], [model.power]);
  const defaultFrame = model.power ? defaultSpectrumFrame(model.power, alert, focusTime) : 0;
  const index = manual?.frame != null && manual.frame >= 0 && manual.frame < frames.length ? manual.frame : defaultFrame;
  const frame = frames[index];
  const curve = useMemo(() => selected === "curve" && spectrum && frame ? spectrumCurveOption(result, spectrum, frame, mappedAlert?.bandIds) : null,
    [selected, spectrum, frame, result, mappedAlert?.bandIds]);
  const active = model.views.find(v => v.id === selected);
  const choose = (view: SpectrumView, frameIndex = manual?.frame ?? null) => setChoice({ result, alertId: alert?.id ?? null, view, frame: frameIndex });
  const unavailable = model.views.filter(v => !v.available).map(v => `${v.label}：${v.unavailableReason}`).join("；");
  return <section className="ain-spectrum-views" aria-label="电磁频谱多视图">
    <div className="ain-spectrum-views-heading"><h3>频谱态势预测</h3><div className="ain-spectrum-view-buttons" role="group" aria-label="频谱视图切换">
      {model.views.map(v => <button type="button" key={v.id} aria-label={`切换到${v.label}`} aria-pressed={selected === v.id && v.available} disabled={!v.available}
        title={v.available ? v.explanation : v.unavailableReason} onClick={() => choose(v.id)}>{v.label}{v.id === model.defaultView && v.available && <small>推荐</small>}</button>)}
    </div></div>
    <div className="ain-spectrum-recommendation"><p>{manual ? `${active?.explanation ?? ""} ${selected === model.defaultView ? model.reason : "已手动切换，仍对应同一预警与预测基准。"}` : model.reason}</p>
      {manual && <button type="button" onClick={() => setChoice(null)}>恢复推荐</button>}</div>
    {unavailable && <p className="ain-spectrum-unavailable">{unavailable}</p>}
    {active?.available && spectrum ? selected === "curve" && curve && frame ? <figure className="ain-spectrum-curve">
      <figcaption><div><h3>频谱曲线（分频段功率）</h3><p>{spectrum.observationLabel}{result.source === "demo" ? " · 示例数据" : ""}</p></div>
        <label>查看采样时段<select aria-label="频谱曲线采样时段" value={index} onChange={event => { const i = Number(event.target.value); if (Number.isInteger(i) && i >= 0 && i < frames.length) choose("curve", i); }}>
          {frames.map((f, i) => <option key={`${f.kind}-${f.start}`} value={i}>{f.kind === "predicted" ? "预测" : "历史"} · {f.start.slice(5)} — {f.end.slice(5)}</option>)}
        </select></label></figcaption>
      <p className="ain-spectrum-frame-caption" data-kind={frame.kind}>{frame.kind === "predicted" ? "未来预测" : "历史观测"} · {frame.start} — {frame.end}（北京时间）</p>
      <ChartPanel title="频谱曲线" description="横轴频率，纵轴对应频段内接收功率；每点代表一个频段，不是占用率。" option={curve} height={248} />
      <p className="ain-forecast-chart-note">每点表示对应频段在所选时段内的功率值；缺失频段和未提供的频率范围保留缺口。{!frame.values.some(v => v !== null) && " 本时段没有有效功率数据，请选择其他时段。"}</p>
    </figure> : <SpectrumChart result={result} spectrum={spectrum} alert={mappedAlert} />
      : <section className="ain-forecast-chart-empty"><h3>频谱数据暂不可用</h3><p>{alert ? "该风险暂未提供关联频谱或有效数值，不展示其他对象的数据。" : "当前预测暂无可用的频谱数据。"}</p></section>}
  </section>;
}
