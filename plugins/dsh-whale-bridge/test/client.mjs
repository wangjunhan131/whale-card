/**
 * lib/client.js 的离线测试。
 *
 * 客户端包不是普通 ESM，而是往 window.__ModuleLoader__ 上挂工厂。
 * 这里用假的 window / require / ctx / owner / slots 把它整套跑一遍，
 * 验证四件事：
 *   1) 格式对：确实调用了 __ModuleLoader__.load，id 是我们自己的；
 *   2) 契约齐：factory 返回的对象里 name / apply / ConsolePane 都在；
 *   3) 注册对：页签 id/kind 正确，slot 的 key 等于页签 id，组件是函数；
 *   4) 不炸：组件能渲染出 iframe；宿主服务缺失或抛错时 apply 都不往外抛。
 *
 * 跑法：node test/client.mjs
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const CLIENT = join(HERE, "..", "lib", "client.js");

let pass = 0;
let fail = 0;
function chk(name, cond, extra = "") {
	if (cond) {
		pass++;
		console.log("  [ok] " + name + (extra ? "  " + extra : ""));
	} else {
		fail++;
		console.log("  [FAIL] " + name + (extra ? "  " + extra : ""));
	}
}

/** 最小的 React 替身：只要 createElement 够了（我们不碰 Hooks）。 */
const reactStub = {
	createElement(type, props, ...children) {
		return { type, props: { ...(props || {}), children: children.length === 1 ? children[0] : children } };
	},
};

const requireStub = (id) => {
	if (id === "react") return reactStub;
	throw new Error("意外的 require：" + id);
};

/** 把 client.js 在假 window 下执行一遍，拿回它交给 ModuleLoader 的那个 spec。 */
function loadBundle() {
	const code = readFileSync(CLIENT, "utf8");
	const captured = [];
	const fakeWindow = {
		__ModuleLoader__: {
			load(spec) {
				captured.push(spec);
			},
		},
	};
	// eslint-disable-next-line no-new-func
	new Function("window", code)(fakeWindow);
	return captured;
}

console.log("=== 1. 打包格式 ===");
const specs = loadBundle();
chk("调用了 window.__ModuleLoader__.load", specs.length === 1, "次数=" + specs.length);
const spec = specs[0] || {};
chk("spec.id 是本插件", spec.id === "dsh-whale-bridge", "id=" + spec.id);
chk("spec.factory 是函数", typeof spec.factory === "function");

console.log("=== 2. 工厂返回值契约 ===");
let exportsObj;
try {
	exportsObj = spec.factory(requireStub);
} catch (err) {
	exportsObj = null;
	chk("factory 不抛错", false, String(err && err.message));
}
if (exportsObj) {
	chk("factory 不抛错", true);
	chk("name 存在", typeof exportsObj.name === "string", "name=" + exportsObj.name);
	chk("apply 是函数", typeof exportsObj.apply === "function");
	chk("ConsolePane 是函数", typeof exportsObj.ConsolePane === "function");
	chk("inject 是数组", Array.isArray(exportsObj.inject));
}

console.log("=== 3. 注册行为（假宿主） ===");
function makeCtx(behavior = {}) {
	const got = { tab: null, slot: null, effectRuns: 0, injectServices: null };
	const slots = {
		register(spec2, comp) {
			got.slot = { spec: spec2, comp };
			return { dispose() {} };
		},
		inject(name, fn) {
			got.slotServiceName = name;
			return fn();
		},
	};
	const sidebarTabs = {
		register(t) {
			got.tab = t;
			return { dispose() {} };
		},
	};
	const owner = {
		get(name) {
			if (name === "sidebarRightTabs") return behavior.noTabs ? undefined : sidebarTabs;
			return undefined;
		},
		effect(fn, _label) {
			got.effectRuns++;
			return fn();
		},
		slots,
	};
	const ctx = {
		inject(services, cb) {
			got.injectServices = services;
			if (behavior.injectThrows) throw new Error("宿主不给 inject");
			return cb(owner);
		},
	};
	return { ctx, got };
}

{
	const { ctx, got } = makeCtx();
	exportsObj.apply(ctx);
	chk("ctx.inject 请求的是 sidebarRightTabs", JSON.stringify(got.injectServices) === '["sidebarRightTabs"]', JSON.stringify(got.injectServices));
	chk("页签注册了", !!got.tab);
	chk("页签 id 正确", got.tab && got.tab.id === "dsh-whale-bridge/console", got.tab && got.tab.id);
	chk("页签 kind 正确", got.tab && got.tab.kind === "dsh-whale-bridge-console", got.tab && got.tab.kind);
	chk("页签标题取得到字", got.tab && typeof got.tab.title === "function" && got.tab.title() === "控制台", got.tab && String(got.tab.title && got.tab.title()));
	chk("slot 注册到了 sidebar.right.pane.tab", got.slotServiceName === "sidebar.right.pane.tab", got.slotServiceName);
	chk("slot.key 等于页签 id", got.slot && got.slot.spec.key === "dsh-whale-bridge/console", got.slot && got.slot.spec.key);
	chk("slot 挂的是 ConsolePane", got.slot && got.slot.comp === exportsObj.ConsolePane);
	chk("effect 被用来管理生命周期", got.effectRuns === 1, "runs=" + got.effectRuns);
}

console.log("=== 4. 组件渲染 ===");
{
	let el;
	let threw = null;
	try {
		el = exportsObj.ConsolePane({});
	} catch (err) {
		threw = err;
	}
	chk("ConsolePane 渲染不抛错", !threw, threw ? String(threw.message) : "");
	const stack = [];
	(function walk(node) {
		if (!node || typeof node !== "object") return;
		if (node.type) stack.push(node);
		const kids = node.props && node.props.children;
		if (Array.isArray(kids)) kids.forEach(walk);
		else if (kids) walk(kids);
	})(el);
	const iframe = stack.find((n) => n.type === "iframe");
	chk("里面有 iframe", !!iframe);
	chk("iframe 指向 8848 控制台", iframe && String(iframe.props.src).endsWith(":8848/console"), iframe && iframe.props.src);
	chk("iframe 有 title（无障碍/可辨识）", iframe && !!iframe.props.title, iframe && iframe.props.title);
	const link = stack.find((n) => n.type === "a");
	chk("有「新窗口打开」的退路", link && link.props.target === "_blank");
}

console.log("=== 5. 宿主降级：不能把异常漏出去 ===");
{
	let threw = null;
	try {
		exportsObj.apply(null);
	} catch (err) {
		threw = err;
	}
	chk("apply(null) 不抛", !threw, threw ? String(threw.message) : "");
}
{
	const { ctx, got } = makeCtx({ noTabs: true });
	let threw = null;
	try {
		exportsObj.apply(ctx);
	} catch (err) {
		threw = err;
	}
	chk("拿不到 sidebarRightTabs 时静默退出", !threw && !got.tab, threw ? String(threw.message) : "tab=null");
}
{
	const { ctx } = makeCtx({ injectThrows: true });
	let threw = null;
	try {
		exportsObj.apply(ctx);
	} catch (err) {
		threw = err;
	}
	chk("ctx.inject 抛错时不外泄", !threw, threw ? String(threw.message) : "");
}

console.log("");
console.log(`结果：${pass} 通过，${fail} 失败`);
process.exit(fail === 0 ? 0 : 1);
