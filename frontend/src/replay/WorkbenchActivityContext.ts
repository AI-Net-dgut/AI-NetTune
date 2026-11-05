import { createContext } from "react";

// 仅控制工作台离开页面时的计时器，不修改用户原有的播放或暂停选择。
// 默认可用，兼容独立使用回放 Hook 的页面和测试。
export const WorkbenchActivityContext = createContext(true);
