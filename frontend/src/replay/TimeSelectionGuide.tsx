import "./ReplayControls.css";

export default function TimeSelectionGuide({ historical }: { historical: boolean }) {
  return <div className="ain-time-guide" aria-label="分析时间选择说明">
    <div className="ain-time-guide-methods">
      <div><strong>分析当前</strong><p>先点“固定当前时刻”，再提问；参考范围决定往前分析多久。</p></div>
      <div><strong>直接问历史</strong><p>输入“分析01-01 14:00到15:00的网络异常”；发送时自动固定起止范围，无需提前固定。</p></div>
      <div><strong><a href="#ain-data-module">手动选历史 ↓</a></strong><p>在数据区拖动双滑块选择开始与结束时间，也可用参考时长快捷选择，然后提问。</p></div>
    </div>
    <p className="ain-time-guide-note">固定查看时刻不会自动暂停主时钟，可通过顶部按钮控制播放。普通问题采用已选范围；明确起止时间会切换范围。相对时间以发送时主时钟计算。</p>
    <details className="ain-time-guide-future">
      <summary>未来时刻／未来时间段怎么问 <span>已接入</span></summary>
      <div className="ain-time-guide-future-methods">
        <div><strong>预测一个未来时刻</strong><p>例如：“预测30分钟后的网络状态，哪些AP可能出现异常？”也可明确写出目标日期与时刻。</p></div>
        <div><strong>预测一个未来时间段</strong><p>例如：“预测未来30分钟的网络风险，说明可能发生的时间及预防建议。”也可明确写出未来起止时间。</p></div>
      </div>
      <p id="ain-forecast-pending">{historical ? "历史回顾期间不提供未来预测；需要预测时先返回当前，再固定预测依据。" : "提问未来时刻或时间段会打开「预测预警」弹窗，并以当前时刻为预测基准；参考范围是已知历史数据。"} 预测只使用基准时刻及以前的数据，不会提前读取目标时段的真实观测。</p>
      <p>当前预测按历史趋势做阻尼外推，趋势不显著时保持末值；若返回预测区间，它是历史拟合残差等形成的经验范围。具体方法、数据是否充足及适用边界以本次弹窗说明为准。</p>
      <p>预测到时后的对比（待接入）：主时钟到达目标区间后，可比较预测与实际回放数据。处理方案的仿真预计效果另行展示。</p>
    </details>
  </div>;
}
