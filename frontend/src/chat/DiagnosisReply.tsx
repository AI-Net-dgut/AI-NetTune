// 兼容旧引用：把旧的 DiagnosisReply 路径统一到当前研判组件。
// 注意：当前全仓已没有地方 import 本文件（QueryPanel 直接从 ./DiagnosisAnswer 引入），
// 可以安全删除；保留它只是为了不打断可能存在的旧分支引用。
// 只 re-export 组件（不 re-export ReplyText 之类的非组件导出），否则会触发
// react-refresh/only-export-components —— 需要 ReplyText 请直接从 ./DiagnosisAnswer 引入。
export { default } from './DiagnosisAnswer';
