/**
 * dsh-whale-bridge 的浏览器侧（客户端插件）。
 *
 * 已停用（2026-11-06）：用户决定控制台走外置独立窗口，
 * 不再往 DSH 侧栏里塞页签。这里保留一个空的 apply，避免宿主加载时报错。
 *
 * 想恢复：见同目录 client.js.bak-* 备份（等比缩放那一版最好用）。
 */
window.__ModuleLoader__.load({
	id: "dsh-whale-bridge",
	factory: (require) => {
		var module = { exports: {} };
		var exports = module.exports;
		Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
		exports.name = "whale-bridge-console";
		exports.inject = [];
		exports.apply = function apply() {};
		return module.exports;
	},
});
