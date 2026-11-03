/** 预测工作区的任务边界；时间数字出现在风险证据或方案代价中，不代表改范围。 */
export function forecastQuestionKind(message: string, scenario: string): 'existing' | 'range' | 'followup' {
  let text = message.trim();
  for (const separator of ['：', ':']) {
    if (text.startsWith(`${scenario}${separator}`)) text = text.slice(scenario.length + 1).trim();
  }
  const directive = text.replace(/^(?:请帮我|请|帮我|给我|我想要?|麻烦)\s*/, '');
  const forecastReference = /预测|预警|未来|所选.{0,4}风险|这(?:条|个).{0,3}风险|本次.{0,4}态势/.test(text);
  if (!forecastReference && (/当前|现在|现存/.test(text)
    || /历史(?:数据|问题|异常)|^(?:分析|查看|查询|回顾).*(?:过去|最近|昨天|前天|\d{1,2}[:：]\d{2}|\d{1,2}点|\d{1,2}[-/月]\d{1,2})/.test(directive))) return 'existing';
  // 只把明确的重新预测/查看范围指令交给时间解析器。
  if (/^(?:重新预测|重新查看|(?:调整|修改|更改|扩大|缩小)(?:一下)?(?:预测|未来)(?:范围|时长))/.test(directive)
    || /^(?:预测|分析|查看|查询)(?:一下)?(?:(?:当前|现在)(?:的)?网络(?:在|的)?)?(?:未来|接下来|明天|后天|今晚|明早|\d{1,2}[:：]\d{2}|\d{1,2}点|\d{4}[年/-]|(?:\d+|[一二两三四五六七八九十半]+)\s*(?:个)?\s*(?:分钟|小时|天))/.test(directive)) return 'range';
  return 'followup';
}
