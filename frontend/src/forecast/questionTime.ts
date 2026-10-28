import { toEpoch } from '../services/backend';
import { formatReplayTime } from '../replay/replayState';
import { needsFutureForecast } from '../replay/questionIntent';

export interface ForecastQuestionContext {
  baseTime: string;
  defaultHorizonMinutes: number;
  maxHorizonMinutes: number;
}
export type ForecastQuestionTime =
  | { kind: 'none' }
  | { kind: 'error'; message: string }
  | { kind: 'forecast'; horizonMinutes: number; targetWindow?: { start: string; end: string } };

function chineseNumber(value: string): number {
  if (value === '半') return .5;
  if (/^\d+(?:\.\d+)?$/.test(value)) return Number(value);
  const digits: Record<string, number> = { 零: 0, 一: 1, 二: 2, 两: 2, 三: 3, 四: 4, 五: 5, 六: 6, 七: 7, 八: 8, 九: 9 };
  let sum = 0, current = 0;
  for (const char of value) {
    if (char in digits) current = digits[char];
    else if (char === '十' || char === '百') { sum += (current || 1) * (char === '十' ? 10 : 100); current = 0; }
    else return NaN;
  }
  return sum + current;
}

/** 仅解释有确定含义的时间；无法解释时明确提示，不猜成过去或静默使用30分钟。 */
export function resolveForecastQuestion(message: string, context: ForecastQuestionContext): ForecastQuestionTime {
  const intended = needsFutureForecast(message);
  const explicitDate = /\d{1,2}[-/月]\d{1,2}/.test(message);
  if (!intended && !explicitDate) return { kind: 'none' };
  try {
    const base = toEpoch(context.baseTime);
    const maximum = context.maxHorizonMinutes;
    const fail = (text: string): ForecastQuestionTime => ({ kind: 'error', message: text });
    const horizon = (minutes: number, targetWindow?: { start: string; end: string }): ForecastQuestionTime => {
      if (!Number.isFinite(minutes) || minutes < 1 || minutes > maximum)
        return fail(`当前支持从预测基准起未来1—${maximum}分钟，请缩短时段或选择更近的时刻。`);
      return { kind: 'forecast', horizonMinutes: Math.ceil(minutes), ...(targetWindow ? { targetWindow } : {}) };
    };
    const normalized = message.replace(/今晚\s*(\d{1,2})(?=点|[:：])/g, '今晚晚上$1').replace(/：/g, ':').replace(/～/g, '~')
      .replace(/(\d{1,2})点(?:钟)?(?:(半)|(\d{1,2})分?)?/g,
        (_m, hour: string, half: string | undefined, minute: string | undefined) => `${hour}:${half ? '30' : (minute ?? '00').padStart(2, '0')}`)
      .replace(/(下午|晚上|傍晚|中午|凌晨|上午|早上|早晨)\s*(\d{1,2}):(\d{2}(?::\d{2})?)/g,
        (_m, part: string, hour: string, rest: string) => {
          let h = Number(hour);
          if (/下午|晚上|傍晚|中午/.test(part) && h > 0 && h < 12) h += 12;
          if (part === '凌晨' && h === 12) h = 0;
          return `${h}:${rest}`;
        });
    if (/昨天|前天|昨日|前日/.test(normalized)) return fail('未来预测不能把过去日期作为目标；请写明未来日期，参考历史可通过界面设置。');
    if (/下周|下个月|下星期|下一(?:小时|分钟|天|周)|(?:\d+|[零一二两三四五六七八九十百半]+)\s*(?:秒|周|个月)/.test(normalized)) return fail('请写明具体日期和钟点；当前预测最长支持未来24小时。');
    // 允许中文量词"个"："未来半个小时 / 一个小时 / 三个小时"（审计第 2 条：
    // 这些最常见的中文表达原先一条都匹配不上，直接落回默认 120 分钟）。
    const duration = normalized.match(/(?:未来|接下来(?:的)?|往后|预测(?:未来)?)\s*(\d+(?:\.\d+)?|[零一二两三四五六七八九十百半]+)\s*(?:个)?\s*(分钟|小时|天)/)
      ?? normalized.match(/(\d+(?:\.\d+)?|[零一二两三四五六七八九十百半]+)\s*(?:个)?\s*(分钟|小时|天)\s*(?:之)?后/);
    const clockTokens = [...normalized.matchAll(/(?<!\d)(\d{1,2}):(\d{2})(?::(\d{2}))?(?!\d)/g)];
    if (duration && (clockTokens.length || explicitDate || /明天|后天|今晚|明早/.test(normalized)))
      return fail('一次请选择一个连续未来时段；请使用“未来N分钟”或明确起止日期和钟点。');
    if (duration) {
      const amount = chineseNumber(duration[1]);
      return horizon(amount * (duration[2] === '天' ? 1440 : duration[2] === '小时' ? 60 : 1));
    }
    if (/\d+\s*(?:分钟|小时|天)|半小时/.test(normalized) && intended && !clockTokens.length)
      return fail('请使用“未来120分钟”“接下来两小时”等明确的预测时长。');
    const baseDay = context.baseTime.slice(0, 10);
    const year = baseDay.slice(0, 4);
    const dayWords = [...new Set(normalized.match(/今天|今日|明天|后天|今晚|明早/g) ?? [])];
    if (dayWords.length > 1 && !(/明天/.test(normalized) && /明早/.test(normalized)))
      return fail('多个相对日期不明确，请为起止钟点写明完整日期。');
    const offset = /后天/.test(normalized) ? 2 : /明天|明早/.test(normalized) ? 1 : 0;
    const relativeDay = formatReplayTime(toEpoch(`${baseDay} 00:00:00`) + offset * 86400_000).slice(0, 10);
    const dates = [...normalized.matchAll(/(?:(\d{4})[-/年])?(\d{1,2})[-/月](\d{1,2})(?:日|号)?/g)].map(token => {
      const date = `${token[1] ?? year}-${token[2].padStart(2, '0')}-${token[3].padStart(2, '0')}`;
      toEpoch(`${date} 00:00:00`);
      return { date, index: token.index ?? 0 };
    });
    if (dates.length > 2 || clockTokens.length > 2) return fail('一次只支持一个连续未来时段，请分开提问。');
    if (!clockTokens.length) {
      if (!intended && dates.length && dates.every(item => item.date <= baseDay)) return { kind: 'none' };
      if (dates.length || dayWords.length) return fail('请补充未来的具体钟点，或使用“未来N分钟”指定范围。');
      return intended ? horizon(context.defaultHorizonMinutes) : { kind: 'none' };
    }
    const used = new Set<number>();
    const stamps = clockTokens.map(token => {
      const calendar = dates.filter(d => d.index < (token.index ?? 0)).at(-1) ?? (dates.length === 1 ? dates[0] : undefined);
      if (calendar) used.add(calendar.index);
      if (calendar && dayWords.length && calendar.date !== relativeDay) throw new Error('相对日期与明确日期不一致，请保留一种表述。');
      return toEpoch(`${calendar?.date ?? relativeDay} ${token[1].padStart(2, '0')}:${token[2]}:${token[3] ?? '00'}`);
    });
    if (!intended && stamps.every(stamp => stamp <= base)) return { kind: 'none' };
    if (dates.some(d => !used.has(d.index))) return fail('日期和钟点关系不明确，请在每个钟点前写明日期。');
    if (clockTokens.length === 2) {
      const between = normalized.slice((clockTokens[0].index ?? 0) + clockTokens[0][0].length, clockTokens[1].index)
        .replace(/(?:(\d{4})[-/年])?\d{1,2}[-/月]\d{1,2}(?:日|号)?/g, '').replace(/的|当天/g, '');
      if (!/^\s*(?:到|至|~|—|－|–|−|-)\s*$/.test(between)) return fail('请用“到”明确一个连续未来时段。');
    }
    const start = stamps[0], end = stamps.at(-1)!;
    if (start < base || end <= base) return fail('指定时刻已过去或尚未超过预测基准，请写明未来日期和钟点。');
    if (end < start) return fail('未来时段的开始晚于结束，跨日时请写明两个日期。');
    return horizon((end - base) / 60_000, { start: formatReplayTime(start), end: formatReplayTime(end) });
  } catch (reason) {
    return { kind: 'error', message: reason instanceof Error ? `未来时间无效：${reason.message}` : '未来时间无效，请核对日期与钟点。' };
  }
}
