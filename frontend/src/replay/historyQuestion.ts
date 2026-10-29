import type { QueryScope } from "../types/backend";
import { toEpoch } from "../services/backend";
import { formatReplayTime } from "./replayState";
import { needsFutureForecast } from "./questionIntent";

export interface HistoryTimeContext { scenario: string; masterTime: string; dataStart: string; dataEnd: string }
export type HistoryTimeResult = { kind: "none" } | { kind: "error"; message: string }
  | { kind: "range"; scope: QueryScope; notice: string };

// 只声明能明确处理的时间表达式；不把“方案需要30分钟”等预计代价当作历史窗口。
export function hasHistoryTimeHint(message: string): boolean {
  // 10-29 审计第 3 项：中文数词与"个"也算历史时间提示（"三个小时前"/"半个小时前"原先识别不到，
  // 会静默沿用原有窗口，等于答非所问）。
  return /\d{1,2}[:：]\d{2}|\d{1,2}点|昨天|前天|昨日|前日|今天|今日|(?<![A-Za-z0-9])\d{1,2}[-/]\d{1,2}(?![A-Za-z0-9])|(?:最近|过去|近|前)\s*(?:\d+(?:\.\d+)?|半|[一二两三四五六七八九十])\s*(?:个)?\s*(?:小时|分钟|天)|\d{1,2}月\d{1,2}日|\d{4}[-/]\d{1,2}[-/]\d{1,2}|(?:\d+(?:\.\d+)?|半|[一二两三四五六七八九十])\s*(?:个)?\s*(?:小时|分钟|天)\s*前|上周|上个月/.test(message);
}
export function resolveHistoryQuestion(message: string, context: HistoryTimeContext): HistoryTimeResult {
  if (needsFutureForecast(message)) return { kind: "none" };
  if (!hasHistoryTimeHint(message)) return { kind: "none" };
  try {
    const master = toEpoch(context.masterTime), lower = toEpoch(context.dataStart), upper = Math.min(master, toEpoch(context.dataEnd));
    const baseDate = context.masterTime.slice(0, 10), year = baseDate.slice(0, 4);
    const normalized = message.replace(/：/g, ":").replace(/～/g, "~").replace(/(\d{4}[-/]\d{1,2}[-/]\d{1,2})T(?=\d)/g, "$1 ")
      .replace(/(\d{1,2})点(?:钟)?(?:(半)|(\d{1,2})分?)?/g, (_all, hour: string, half: string | undefined, minute: string | undefined) => `${hour}:${half ? "30" : (minute ?? "00").padStart(2, "0")}`)
      .replace(/(下午|晚上|傍晚|中午|凌晨|上午|早上|早晨)\s*(\d{1,2}):(\d{2}(?::\d{2})?)/g, (_all, part: string, hour: string, rest: string) => {
        let h = Number(hour); if (/下午|晚上|傍晚|中午/.test(part) && h > 0 && h < 12) h += 12;
        if (part === "凌晨" && h === 12) h = 0; return `${h}:${rest}`;
      })
      // 中文数词 / "个" / "半小时" 归一成数字，后续规则只处理一种写法（审计 10-29 第 3 项）
      .replace(/半\s*个?\s*(小时|钟头)/g, "30分钟")
      .replace(/([一二两三四五六七八九十])\s*个?\s*(小时|钟头|分钟|天)\s*前/g,
        (_all, n: string, unit: string) => `${({ 一: 1, 二: 2, 两: 2, 三: 3, 四: 4, 五: 5, 六: 6, 七: 7, 八: 8, 九: 9, 十: 10 } as Record<string, number>)[n]}${unit === "钟头" ? "小时" : unit}前`)
      .replace(/(最近|过去|近|前)\s*([一二两三四五六七八九十])\s*个?\s*(小时|钟头|分钟|天)/g, (_all, prefix: string, n: string, unit: string) => `${prefix}${({一:1,二:2,两:2,三:3,四:4,五:5,六:6,七:7,八:8,九:9,十:10} as Record<string, number>)[n]}${unit === "钟头" ? "小时" : unit}`)
    const finish = (a: number, b: number): HistoryTimeResult => {
      if (a > b) return { kind: "error", message: "开始时间晚于结束时间；跨日查询请写明两个日期。" };
      if (a > upper) return { kind: "error", message: "指定时间尚未到达，不能读取未来数据。" };
      if (b < lower) return { kind: "error", message: "指定时间早于已有七天数据，请选择数据范围内的历史。" };
      const start = Math.max(a, lower), end = Math.min(b, upper);
      const notes = [a < lower ? "开始时间已按数据起点截断" : "", b > upper ? "结束时间已限制到发送时的已知主时钟" : ""].filter(Boolean);
      return { kind: "range", scope: { scenario: context.scenario, start: formatReplayTime(start), end: formatReplayTime(end) }, notice: notes.join("；") };
    };
    const relatives = [...normalized.matchAll(/(?:最近|过去|近|前)\s*(半|\d+(?:\.\d+)?)\s*(小时|分钟|天)/g)];
    const relativeDays = new Set(normalized.match(/昨天|前天|今天|昨日|前日|今日/g) ?? []);
    if (relatives.length > 1 || relativeDays.size > 1)
      return { kind: "error", message: "一次只分析一个历史窗口；多个时期请分别提问或写明一个连续起止范围。" };
    if (/上周|上个月/.test(normalized)) return { kind: "error", message: "请写明具体日期和起止时间，例如01-02 09:30到11:30。" };
    const dayOffset = /前天|前日/.test(normalized) ? 2 : /昨天|昨日/.test(normalized) ? 1 : 0;
    const relativeDate = formatReplayTime(toEpoch(`${baseDate} 00:00:00`) - dayOffset * 86400_000).slice(0, 10);
    const datePattern = /(?:(\d{4})[-/年])?(\d{1,2})[-/月](\d{1,2})(?:日|号)?/g;
    const dates = [...normalized.matchAll(datePattern)].map(token => {
      const date = `${token[1] ?? year}-${token[2].padStart(2, "0")}-${token[3].padStart(2, "0")}`;
      toEpoch(`${date} 00:00:00`); // Validate every explicit date, even if not adjacent to a clock.
      return { date, index: token.index ?? 0, end: (token.index ?? 0) + token[0].length };
    });
    const tokens = [...normalized.matchAll(/(?<!\d)(\d{1,2}):(\d{2})(?::(\d{2}))?(?!\d)/g)];
    if (dates.length > 2 || tokens.length > 2)
      return { kind: "error", message: "一次只分析一个连续时间段，请分开提问。" };
    if (relativeDays.size && dates.some(item => item.date !== relativeDate))
      return { kind: "error", message: "相对日期和明确日期不一致，请保留一种日期表述。" };
    const usedDates = new Set<number>();
    const parseToken = (token: RegExpMatchArray): number => {
      const before = dates.filter(item => item.index < (token.index ?? 0));
      const calendar = before.at(-1) ?? (dates.length === 1 ? dates[0] : undefined);
      if (calendar) usedDates.add(calendar.index);
      return toEpoch(`${calendar?.date ?? relativeDate} ${token[1].padStart(2, "0")}:${token[2]}:${token[3] ?? "00"}`);
    };
    const pointRange = (anchor: number): HistoryTimeResult => {
      if (anchor > upper) return { kind: "error", message: "指定时刻尚未到达，不能读取未来数据。" };
      const minute = Math.floor(anchor / 60_000) * 60_000;
      return finish(minute, minute + 59_000);
    };
    // "最近/过去 X 到 Y前"：一个锚点是相对时长、另一个是相对时点 → 连续窗口
    // （审计 10-29 第 3 项：原先只取"10分钟前"，把 30 分钟的跨度丢了）
    const mixed = normalized.match(/(?:最近|过去|近)\s*(半|\d+(?:\.\d+)?)\s*(小时|分钟|天)\s*(?:到|至|~|—|－|–|−|-)\s*(半|\d+(?:\.\d+)?)\s*(小时|分钟|天)\s*前/);
    if (mixed) {
      if (tokens.length || dates.length || relativeDays.size)
        return { kind: "error", message: "多个时间锚点不明确，请直接写出起止时间。" };
      const toMs = (amount: string, unit: string) =>
        (amount === "半" ? 0.5 : Number(amount)) * (unit === "天" ? 86400_000 : unit === "小时" ? 3600_000 : 60_000);
      const a = master - toMs(mixed[1], mixed[2]);
      const b = master - toMs(mixed[3], mixed[4]);
      if (a >= b) return { kind: "error", message: "开始时间需要早于结束时间（例如“过去30分钟到10分钟前”）。" };
      return finish(a, b);
    }
    // "X前到Y前"：两个相对锚点组成一个连续窗口（审计第 2 条）。
    // 原实现只取第一个"X前"，把"30分钟前到10分钟前"解析成 11:30–11:30:59 的一分钟，
    // 静默丢掉了第二个锚点。这里显式识别成窗口，且支持"个"与中文数词。
    const agoPair = normalized.match(/(半|\d+(?:\.\d+)?|[一二两三四五六七八九十])\s*(?:个)?\s*(小时|分钟|天)\s*前\s*(?:到|至|~|—|－|–|−|-)\s*(半|\d+(?:\.\d+)?|[一二两三四五六七八九十])\s*(?:个)?\s*(小时|分钟|天)\s*前/);
    if (agoPair) {
      if (tokens.length || dates.length || relativeDays.size)
        return { kind: "error", message: "多个时间锚点不明确，请直接写出起止时间。" };
      const cn: Record<string, number> = { 一: 1, 二: 2, 两: 2, 三: 3, 四: 4, 五: 5, 六: 6, 七: 7, 八: 8, 九: 9, 十: 10 };
      const toMs = (amount: string, unit: string): number => {
        const n = amount === "半" ? 0.5 : (amount in cn ? cn[amount] : Number(amount));
        return n * (unit === "天" ? 86400_000 : unit === "小时" ? 3600_000 : 60_000);
      };
      const a = master - toMs(agoPair[1], agoPair[2]);
      const b = master - toMs(agoPair[3], agoPair[4]);
      if (a >= b)
        return { kind: "error", message: "开始时间需要早于结束时间（例如“30分钟前到10分钟前”）。" };
      return finish(a, b);
    }
    const ago = normalized.match(/(半|\d+(?:\.\d+)?)\s*(?:个)?\s*(小时|分钟|天)前/);
    // "X前与Y前"是**两时点对比**，当前只支持连续窗口 → 明确说不支持，
    // 而不是只取第一个锚点静默忽略另一个（审计第 2 条）。
    if (ago && /(?:与|和|、|vs\.?|VS)\s*(?:半|\d+(?:\.\d+)?)\s*(?:个)?\s*(?:小时|分钟|天)\s*前/.test(normalized))
      return { kind: "error", message: "两时点对比暂不支持：可改用“X前到Y前”查连续窗口，或分别提问。" };
    if (ago) {
      if (tokens.length || dates.length || relativeDays.size)
        return { kind: "error", message: "多个时间锚点不明确，请直接写出起止时间。" };
      const n = ago[1] === "半" ? 0.5 : Number(ago[1]);
      const scale = ago[2] === "天" ? 86400_000 : ago[2] === "小时" ? 3600_000 : 60_000;
      return pointRange(master - n * scale);
    }
    const relative = relatives[0];
    if (relative) {
      const n = relative[1] === "半" ? 0.5 : Number(relative[1]);
      if (!(n > 0)) return { kind: "error", message: "参考时长必须大于0。" };
      const scale = relative[2] === "天" ? 86400_000 : relative[2] === "小时" ? 3600_000 : 60_000;
      if (!tokens.length && !dates.length && !relativeDays.size) return finish(master - n * scale, master);
      if (tokens.length === 1 && dates.length <= 1) {
        const afterClock = normalized.slice((tokens[0].index ?? 0) + tokens[0][0].length);
        if (/^\s*(?:之前|以前|往前|前)/.test(afterClock)) {
          const anchor = parseToken(tokens[0]); return finish(anchor - n * scale, anchor);
        }
      }
      return { kind: "error", message: "日期、钟点和参考时长的关系不明确，请直接写出起止时间。" };
    }
    if (tokens.length === 2) {
      const between = normalized.slice((tokens[0].index ?? 0) + tokens[0][0].length, tokens[1].index)
        .replace(datePattern, "").replace(/的|当天|这天/g, "");
      if (!/^\s*(?:到|至|~|—|－|–|−|-)\s*$/.test(between))
        return { kind: "error", message: "请用“到”明确起止时间；两个独立时刻请分别提问。" };
      const a = parseToken(tokens[0]), b = parseToken(tokens[1]);
      if (dates.some(item => !usedDates.has(item.index)))
        return { kind: "error", message: "日期与钟点的对应关系不明确，请在每个钟点前写明日期。" };
      return finish(a, b);
    }
    if (tokens.length === 1) {
      if (dates.length > 1) return { kind: "error", message: "多个日期对应一个钟点，请写明完整起止时间。" };
      const rest = normalized.slice((tokens[0].index ?? 0) + tokens[0][0].length);
      if (/^\s*(?:到|至|~|—|－|–|−|-)\s*\d/.test(rest))
        return { kind: "error", message: "结束时间不完整，请写成HH:mm。" };
      return pointRange(parseToken(tokens[0]));
    }
    if (/点|:/.test(normalized)) return { kind: "error", message: "请使用数字钟点，例如昨天14:00或01-02 09:30到11:30。" };
    if (dates.length === 2) {
      const between = normalized.slice(dates[0].end, dates[1].index).replace(/的/g, "");
      if (!/^\s*(?:到|至|~|—|－|–|−|-)\s*$/.test(between))
        return { kind: "error", message: "多个日期的对比请分开提问；连续日期范围请使用“到”。" };
      return finish(toEpoch(`${dates[0].date} 00:00:00`), toEpoch(`${dates[1].date} 23:59:59`));
    }
    if (dates.length || relativeDays.size) {
      const day = dates[0]?.date ?? relativeDate;
      return finish(toEpoch(`${day} 00:00:00`), toEpoch(`${day} 23:59:59`));
    }
    return { kind: "error", message: "暂不能明确这段历史时间，请用具体日期/钟点，或在数据区手动选择。" };
  } catch { return { kind: "error", message: "时间格式或日期无效，请核对后重新输入。" }; }
}
