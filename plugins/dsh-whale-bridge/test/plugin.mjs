/**
 * 插件层自检：不用 DSH 也能验「注册出来的东西对不对」。
 *
 *   node test/plugin.mjs
 *
 * 造一个假的 ctx（tools.register / systemPrompt.section / logger），
 * 把真插件装进去，检查：
 *   - 工具都注册了、名字/参数/execute 合法
 *   - 系统提示那段确实塞进去了
 *   - 工具真的能打到卡上（拿 mind_health 验活）
 */

const registered = [];
const sections = [];

const fakeCtx = {
  logger: () => ({ info() {}, warn() {}, error() {}, debug() {} }),
  tools: {
    register(tool) {
      registered.push(tool);
    },
  },
  systemPrompt: {
    section(sec) {
      sections.push(sec);
    },
  },
};

let pass = 0;
let fail = 0;
function chk(title, ok, extra = '') {
  if (ok) {
    pass += 1;
    console.log(`  ✅ ${title}${extra ? '  ' + extra : ''}`);
  } else {
    fail += 1;
    console.log(`  ❌ ${title}${extra ? '  ' + extra : ''}`);
  }
}

console.log('== 1. 模块能不能加载 ==');
const mod = await import('../lib/index.js');
chk('导出 name', mod.name === 'whale-bridge', mod.name);
chk('导出 inject 含 tools', Array.isArray(mod.inject) && mod.inject.includes('tools'));
chk('导出 apply 是函数', typeof mod.apply === 'function');
chk('default 也有 apply', typeof mod.default?.apply === 'function');

console.log('== 2. 装进假 ctx ==');
mod.apply(fakeCtx, { port: 8848, cardDir: 'D:\\新建文件夹 (3)\\whale-card' });
chk('注册了 8 个工具', registered.length === 8, `${registered.length} 个`);
const names = registered.map((t) => t.name).sort();
console.log(`  工具：${names.join(', ')}`);
const want = [
  'mind_context',
  'mind_health',
  'mind_recall',
  'mind_remember',
  'mind_report',
  'mind_skill_list',
  'mind_skill_use',
  'mind_status',
];
chk('名字和契约一致', JSON.stringify(names) === JSON.stringify(want));
chk(
  '每个都有 description / parameters / execute',
  registered.every(
    (t) => typeof t.description === 'string' && t.description.length > 5 && typeof t.parameters === 'object' && typeof t.execute === 'function',
  ),
);
chk('必填参数都标了 required', registered.every((t) => {
  const props = t.parameters?.properties || {};
  const hasRequired = Object.values(props).some((p) => p.required);
  // mind_health / mind_status / mind_skill_list 没有参数，属于正常
  return Object.keys(props).length === 0 ? true : hasRequired;
}));
chk('系统提示塞进去了 1 段', sections.length === 1, sections[0]?.name || '');
chk('提示里写了 mind_context / mind_report', /mind_context/.test(sections[0]?.text || '') && /mind_report/.test(sections[0]?.text || ''));

console.log('== 3. 真打一次卡（mind_health）==');
const healthTool = registered.find((t) => t.name === 'mind_health');
const h = await healthTool.execute({});
chk('返回 ok', h.ok === true, h.error || '');
chk('有记忆条数', typeof h.memories === 'number' && h.memories > 0, `memories=${h.memories}`);
chk('链完好', h.chain_ok === true);
chk('锚一致', h.anchor === 'ok', h.anchor_reason || '');
chk('知道第几天', typeof h.day === 'number', `day=${h.day}`);

console.log('== 4. 真调一次 mind_recall ==');
const recallTool = registered.find((t) => t.name === 'mind_recall');
const r = await recallTool.execute({ query: '白米饭', top_k: 3 });
chk('返回 ok', r.ok === true, r.error || '');
chk('拿到命中', Array.isArray(r.hits) && r.hits.length > 0, `${(r.hits || []).length} 条`);
if (r.hits?.length) console.log(`  第一条：#${r.hits[0].id} ${String(r.hits[0].text).slice(0, 40)}`);

console.log('== 5. 输出渲染函数不炸 ==');
for (const t of registered) {
  if (!t.output?.render) continue;
  try {
    const out = t.output.render({}, { ok: true, demo: 1 });
    chk(`${t.name} render`, Array.isArray(out) && out[0]?.type === 'text');
  } catch (e) {
    chk(`${t.name} render`, false, e?.message || '');
  }
}

console.log('');
console.log(`结果：${pass} 通过，${fail} 失败`);
process.exit(fail === 0 ? 0 : 1);
