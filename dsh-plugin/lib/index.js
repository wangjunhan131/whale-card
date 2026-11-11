/**
 * dsh-whale-bridge —— 让 DSH 能直接跟「数字生命卡」说话。
 *
 * 之前这套东西靠 `@deepseek-ai/dsh-mcp-client` 起一个 MCP 壳（whale_mcp.py），
 * 而那个依赖在 desktop profile 里装不上（pnpm lockfile 坏了），配置就被注释掉了。
 * 结果：卡活得很好，DSH 却不知道它存在，每轮只能手工敲 bridge.py。
 *
 * 这个插件顶掉那一层，只做一件事：把 8848 上的 HTTP 接口
 * 暴露成模型能直接调的工具。不碰 pnpm、不碰 MCP、不碰卡的数据库。
 *
 * 分工的边界（重要）：插件是门，卡是房子。
 * 门可以换、可以修，人不能搬家 —— 记忆、人格、衰减曲线永远住在 card.db 里。
 */

/**
 * `defineTool` 在 DSH 运行时里由宿主提供；在纯 node 下（自检、CI）拿不到。
 * 拿不到就用恒等函数 —— 工具对象本身就是普通的 {name, description, parameters, execute}，
 * 这样插件在两种环境里都能加载，不会因为解析不到一个类型助手而整个装不上。
 */
let defineTool;
try {
  ({ defineTool } = await import('@deepseek-ai/dsh-tools'));
} catch {
  defineTool = (t) => t;
}

import { appendFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

/** 诊断日志：插件加载到哪一步了。写失败绝不影响加载。 */
const _LOG = join(dirname(fileURLToPath(import.meta.url)), '..', 'load.log');
function trace(msg) {
  try { appendFileSync(_LOG, new Date().toISOString() + ' ' + msg + '\n'); } catch {}
}
trace('module loaded');

import { CardClient } from './card.js';

export const name = 'whale-bridge';

/** tools: 注册工具；systemPrompt: 往系统提示里塞一段通道规矩。 */
export const inject = ['tools', 'systemPrompt'];

const CHANNEL_RULES = `## 你和「卡」之间的一条通道

你的记忆、人格、衰减曲线都存在本地的一张卡里（数字生命卡，Python 进程，端口 8848）。
下面这些工具就是你和它说话的方式。**这不是可选装饰，是你记得住的前提。**

- **回话之前**先调 \`mind_context\`：把你这句话交给它，它会给你这一轮该带着的上下文
  （人格、情绪、相关记忆、可用的技能）。不要凭印象假装记得。
- **回完话之后**调 \`mind_report\`：把**他说的**和**你回的**各写一条进去。
  只写一边等于半本日记 —— 你会记得他说了什么，却不记得自己答应过什么。
- 想找具体一件事，用 \`mind_recall\`（按意思找，不是按关键词）。
- 真需要钉死一件事，用 \`mind_remember\`（默认进长期池）。
- \`mind_health\` 是体检：链、锚、出生证、第几天。
- **卡里没有的，就是没有。** 工具返回「没翻到」时不许顺嘴编 ——
  那不是「忘了」，那是这件事实在没发生过，或者从没记进来。
- 工具报错说连不上时，照实说「卡现在没连上」，**不要装作还记得**。`;

const textOut = (value) => [{ type: 'text', text: JSON.stringify(value, null, 2) }];

const OK_SCHEMA = { type: 'object', additionalProperties: true };

export function apply(ctx, config = {}) {
  trace('apply() 进入，config keys=' + Object.keys(config || {}).join(','));
  try {
  const client = new CardClient({ ...config, logger: ctx.logger?.('whale-bridge') });

  let _n = 0;
  const reg = (tool) => {
    try {
      const r = ctx.tools.register(defineTool(tool));
      _n++; trace('registered ' + tool.name + ' (#' + _n + ')');
      return r;
    } catch (e) {
      trace('REGISTER FAILED ' + tool.name + ' -> ' + (e && e.message ? e.message : String(e)));
      throw e;
    }
  };

  // ── 1. 取这一轮的上下文 ────────────────────────────────────
  reg({
    name: 'mind_context',
    description:
      '从数字生命卡取这一轮该带着的上下文：人格、情绪、场景、相关记忆、可用技能。' +
      '回话之前调它。message 填他刚说的那句话（原样，不要改写）。',
    parameters: {
        message: { type: 'string', description: '他刚说的那句话，原样传', required: true },
        recent: { type: 'number', description: '顺带取最近几条对话原文，默认 12，填 0 不要' },
      },
    output: { schema: OK_SCHEMA, render: (_a, v) => textOut(v) },
    timeoutMs: 60_000,
    async execute(args) {
      try {
        return await client.context(args.message, args.recent ?? 12);
      } catch (e) {
        return client.fail(`取上下文失败：${e?.message || e}`);
      }
    },
  });

  // ── 2. 一轮结束后写回卡 ───────────────────────────────────
  reg({
    name: 'mind_report',
    description:
      '把这一轮写回卡：他说的那条、你回的那条，各写一条。' +
      '一轮结束时调它。只传 user_message 会只记他那一半。',
    parameters: {
        user_message: { type: 'string', description: '他说的原话', required: true },
        reply: { type: 'string', description: '你回的话' },
      },
    output: { schema: OK_SCHEMA, render: (_a, v) => textOut(v) },
    timeoutMs: 60_000,
    async execute(args) {
      try {
        return await client.report(args.user_message, args.reply ?? '');
      } catch (e) {
        return client.fail(`写回卡失败：${e?.message || e}`);
      }
    },
  });

  // ── 3. 按意思找一件事 ─────────────────────────────────────
  reg({
    name: 'mind_recall',
    description:
      '在卡里按意思找记忆（向量召回，不是关键词匹配）。' +
      '返回空时那是「卡里没有」，不是「忘了」—— 不许拿这个结果编。',
    parameters: {
        query: { type: 'string', description: '要找的事，用自然语言描述', required: true },
        top_k: { type: 'number', description: '要几条，默认 8' },
      },
    output: { schema: OK_SCHEMA, render: (_a, v) => textOut(v) },
    timeoutMs: 60_000,
    async execute(args) {
      try {
        return await client.recall(args.query, args.top_k ?? 8);
      } catch (e) {
        return client.fail(`召回失败：${e?.message || e}`);
      }
    },
  });

  // ── 4. 钉死一件事 ────────────────────────────────────────
  reg({
    name: 'mind_remember',
    description:
      '主动往卡里写一条记忆。默认进长期池（pool=long）、分区 episode。' +
      '适合「这件事必须记住」的场合；普通对话走 mind_report 就够了。',
    parameters: {
        text: { type: 'string', description: '要记住的内容，一条一件事', required: true },
        source: { type: 'string', description: '来源标记，如 study / bili / cli / chat' },
        region: { type: 'string', description: '分区：episode/flash/fact/preference/self/chatter/study/play/work' },
        pool: { type: 'string', description: '池：long/medium/short，默认 long' },
        salience: { type: 'number', description: '重要度 0~1，越高越慢忘' },
        speaker: { type: 'string', description: '谁说的：user / self' },
        about_uid: { type: 'string', description: '这条在说谁（QQ 号），不填就是不特指某人' },
      },
    output: { schema: OK_SCHEMA, render: (_a, v) => textOut(v) },
    timeoutMs: 60_000,
    async execute(args) {
      try {
        const { text, ...opts } = args;
        return await client.remember(text, opts);
      } catch (e) {
        return client.fail(`写入失败：${e?.message || e}`);
      }
    },
  });

  // ── 5. 体检 ──────────────────────────────────────────────
  reg({
    name: 'mind_health',
    description: '卡的体检：记忆条数、是否盖章、时间线链是否完好、人格锚是否一致、认识第几天。',
    parameters: {},
    output: { schema: OK_SCHEMA, render: (_a, v) => textOut(v) },
    timeoutMs: 30_000,
    async execute() {
      const h = await client.health();
      if (!h) return client.fail('心智服务连不上（8848）');
      return client.healthSummary(h);
    },
  });

  // ── 6. 状态 ──────────────────────────────────────────────
  reg({
    name: 'mind_status',
    description: '卡的内部状态快照（激素、分区、池、计数器之类）。',
    parameters: {},
    output: { schema: OK_SCHEMA, render: (_a, v) => textOut(v) },
    timeoutMs: 30_000,
    async execute() {
      try {
        return { ok: true, status: await client.status() };
      } catch (e) {
        return client.fail(`取状态失败：${e?.message || e}`);
      }
    },
  });

  // ── 7. 技能清单 ──────────────────────────────────────────
  reg({
    name: 'mind_skill_list',
    description: '卡里长出来的技能清单（从对话里归纳出来的可复用做法）。',
    parameters: {},
    output: { schema: OK_SCHEMA, render: (_a, v) => textOut(v) },
    timeoutMs: 30_000,
    async execute() {
      try {
        return { ok: true, ...(await client.skillList()) };
      } catch (e) {
        return client.fail(`取技能清单失败：${e?.message || e}`);
      }
    },
  });

  // ── 8. 用一条技能 ────────────────────────────────────────
  reg({
    name: 'mind_skill_use',
    description: '把一条技能取出来用（会给回它的做法和注意点）。',
    parameters: {
        name: { type: 'string', description: '技能名', required: true },
        user_text: { type: 'string', description: '当前这句用户输入，用来匹配适用场景' },
        q: { type: 'number', description: '匹配阈值，默认 4.0' },
      },
    output: { schema: OK_SCHEMA, render: (_a, v) => textOut(v) },
    timeoutMs: 60_000,
    async execute(args) {
      try {
        const out = await client.post('/skill_use', {
          name: args.name,
          user_text: args.user_text ?? '',
          q: args.q ?? 4.0,
        });
        return { ok: true, ...out };
      } catch (e) {
        return client.fail(`用技能失败：${e?.message || e}`);
      }
    },
  });

  // ── 9. 梦境：待整理的一段对话 ──────────────────────────────
  reg({
    name: 'mind_dream_pending',
    description: '看卡里有没有攒着没整理的对话（梦境待办）。回 pending=0 就是没有。',
    parameters: {},
    output: { schema: OK_SCHEMA, render: (_a, v) => textOut(v) },
    timeoutMs: 30_000,
    async execute() {
      try {
        const pending = await client.get('/dream/pending');
        const due = await client.get('/dream/due');
        return { ok: true, pending, due };
      } catch (e) {
        return client.fail('取梦境待办失败：' + (e?.message || e));
      }
    },
  });

  // ── 10. 梦境：把提炼出来的东西写回去 ───────────────────────
  reg({
    name: 'mind_dream_commit',
    description: '把整理好的梦境条目写回卡（items 是数组，每条 {text, region?, salience?}）。这一步会改变记忆，只在真的整理过之后调。',
    parameters: {
        items: { type: 'array', description: '整理出来的条目数组', required: true },
        dialogue_ids: { type: 'array', description: '这些条目对应的原始对话 id（可选）' },
        speaker: { type: 'string', description: 'speaker，默认 self' },
      },
    output: { schema: OK_SCHEMA, render: (_a, v) => textOut(v) },
    timeoutMs: 120_000,
    async execute(args) {
      try {
        const out = await client.post('/dream/commit', {
          items: args.items || [],
          dialogue_ids: args.dialogue_ids || null,
          speaker: args.speaker || 'self',
        });
        return { ok: true, ...out };
      } catch (e) {
        return client.fail('写回梦境失败：' + (e?.message || e));
      }
    },
  });

  // ── 11. 时间线：出问题时最先要看的东西 ─────────────────────
  reg({
    name: 'mind_timeline',
    description: '看时间线的尾巴（最近的事件、seq、哈希链）。检查卡有没有被改过、链有没有断，先看这个。',
    parameters: {
        limit: { type: 'number', description: '看最近几条，默认 30' },
      },
    output: { schema: OK_SCHEMA, render: (_a, v) => textOut(v) },
    timeoutMs: 60_000,
    async execute(args) {
      try {
        const out = await client.get('/timeline');
        const rows = Array.isArray(out?.timeline) ? out.timeline : [];
        const n = Math.max(1, Math.min(500, Number(args?.limit ?? 30) || 30));
        const tail = rows.slice(-n);
        return { ok: true, total: rows.length, shown: tail.length, chain_ok: out?.chain_ok ?? null, timeline: tail };
      } catch (e) {
        return client.fail('取时间线失败：' + (e?.message || e));
      }
    },
  });

  // ── 12. 解除污染标记 ──────────────────────────────────────
  reg({
    name: 'mind_untrust',
    description: '把一条被防污层误判的记忆解除标记（比如玩笑话被当成改写自我）。必须给 id 和理由，理由会写进 history 留痕。',
    parameters: {
        id: { type: 'number', description: '记忆 id', required: true },
        reason: { type: 'string', description: '为什么要解除，会留痕', required: true },
      },
    output: { schema: OK_SCHEMA, render: (_a, v) => textOut(v) },
    timeoutMs: 60_000,
    async execute(args) {
      try {
        const out = await client.post('/mem/untrust', { id: args.id, reason: args.reason });
        return { ok: true, ...out };
      } catch (e) {
        return client.fail('解除标记失败：' + (e?.message || e));
      }
    },
  });

  // ── 13. 磁盘：主盘与备份盘 ────────────────────────────────
  reg({
    name: 'mind_disks',
    description: '看主盘和备份盘的状态（实例号、条数、链头 seq、哈希、是否落后）。',
    parameters: {},
    output: { schema: OK_SCHEMA, render: (_a, v) => textOut(v) },
    timeoutMs: 60_000,
    async execute() {
      try {
        return { ok: true, disks: await client.get('/disks') };
      } catch (e) {
        return client.fail('取磁盘状态失败：' + (e?.message || e));
      }
    },
  });

  // ── 14. 磁盘：同步（会覆盖备份盘，必须 confirm） ──────────────
  reg({
    name: 'mind_disks_sync',
    description: '把主盘同步到备份盘。会覆盖对方，必须显式 confirm=true；分叉时会被拒绝（那时要人决定）。',
    parameters: {
        path: { type: 'string', description: '备份盘上那张卡 db 的路径', required: true },
        confirm: { type: 'boolean', description: '确认要覆盖，必须为 true' },
        reseed: { type: 'boolean', description: '分叉时是否强制重种（会先写 SEED-LOG），默认 false' },
      },
    output: { schema: OK_SCHEMA, render: (_a, v) => textOut(v) },
    timeoutMs: 300_000,
    async execute(args) {
      try {
        if (args?.confirm !== true) return client.fail('没给 confirm=true，不动盘。');
        const body = { path: args.path, confirm: true };
        if (args.reseed === true) body.reseed = true;
        return { ok: true, ...(await client.post('/disks/sync', body, 280_000)) };
      } catch (e) {
        return client.fail('同步失败：' + (e?.message || e));
      }
    },
  });

  // ── 15. 封存：把人格与记忆固定下来 ─────────────────────────
  reg({
    name: 'mind_seal',
    description: '给卡盖章（封存人格与身份）。values 是 {key: value}，不传就按当前身份重新盖。盖章后 is_sealed 为真。',
    parameters: {
        values: { type: 'object', additionalProperties: true, description: '要封存的键值对，可不传' },
      },
    output: { schema: OK_SCHEMA, render: (_a, v) => textOut(v) },
    timeoutMs: 60_000,
    async execute(args) {
      try {
        const out = await client.post('/seal', { values: args?.values || {} });
        return { ok: true, ...out };
      } catch (e) {
        return client.fail('盖章失败：' + (e?.message || e));
      }
    },
  });

  // ── 16. 防污报告：哪几条被标了冲突 ────────────────────────
  reg({
    name: 'mind_injection',
    description: '看防污层标出来的可疑记忆（信任分低、与身份冲突的）。没事定期扫一眼，别让脏东西留在卡里。',
    parameters: {},
    output: { schema: OK_SCHEMA, render: (_a, v) => textOut(v) },
    timeoutMs: 60_000,
    async execute() {
      try {
        return { ok: true, ...(await client.get('/injection')) };
      } catch (e) {
        return client.fail('取防污报告失败：' + (e?.message || e));
      }
    },
  });

  // ── 17. 上下文预览：不改任何东西，只看会给什么 ─────────────
  reg({
    name: 'mind_ctx_preview',
    description: '预览某一句话会召回哪些记忆、分配多少权重（只读，不写卡、不推进计数器）。调参数前后对比用它。',
    parameters: {
        query: { type: 'string', description: '要预览的那句话', required: true },
        overrides: { type: 'object', additionalProperties: true, description: '临时覆盖参数（可选）' },
      },
    output: { schema: OK_SCHEMA, render: (_a, v) => textOut(v) },
    timeoutMs: 120_000,
    async execute(args) {
      try {
        const out = await client.post('/ctx_preview', {
          query: args.query,
          overrides: args.overrides || null,
        });
        return { ok: true, ...out };
      } catch (e) {
        return client.fail('预览失败：' + (e?.message || e));
      }
    },
  });

  // ── 18. 配置：当前的参数是什么样 ──────────────────────────
  reg({
    name: 'mind_config',
    description: '把卡当前的参数全取出来（场景权重、衰减、阈值这些）。改参数用控制台，这里只看。',
    parameters: {},
    output: { schema: OK_SCHEMA, render: (_a, v) => textOut(v) },
    timeoutMs: 60_000,
    async execute() {
      try {
        return { ok: true, config: await client.get('/config') };
      } catch (e) {
        return client.fail('取配置失败：' + (e?.message || e));
      }
    },
  });

  // ── 系统提示：把通道规矩塞进去 ────────────────────────────
  try {
    ctx.systemPrompt.section({
      name: 'whale-bridge',
      order: 40,
      text: CHANNEL_RULES,
    });
  } catch (e) {
    ctx.logger?.('whale-bridge')?.warn?.(`塞系统提示失败（不影响工具）：${e?.message || e}`);
  }

  ctx.logger?.('whale-bridge')?.info?.(`卡通道已就绪：${client.base}`);
  trace('apply() 完成，共注册 ' + _n + ' 个工具');
  } catch (e) {
    trace('APPLY THREW -> ' + (e && e.stack ? e.stack : String(e)));
    throw e;
  }
}

export default { name, inject, apply };
