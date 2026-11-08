/**
 * 自检：绕开 DSH，直接验「插件这套传输层」对不对。
 *
 *   node test/selftest.mjs            # 只读（健康/上下文/召回/状态/技能）
 *   node test/selftest.mjs --write    # 额外验写回（会真往卡里写两条测试记忆）
 *
 * 注意：--write 会写卡。想验又不想污染真卡，把 WHALE_DB 指到副本上再跑。
 */

import { CardClient } from '../lib/card.js';

const args = process.argv.slice(2);
const doWrite = args.includes('--write');
const client = new CardClient({
  port: Number(process.env.WHALE_MIND_PORT || 8848),
  cardDir: process.env.WHALE_CARD_DIR || 'workspace (3)\\whale-card',
});

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

console.log('== 1. 服务在不在（ensure 应该秒回，不重复拉起）==');
const h = await client.ensure();
chk('能连上 8848', !!h);
if (!h) {
  console.log('  连不上就没法往下验了。');
  process.exit(1);
}
const hs = client.healthSummary(h);
chk('记忆条数 > 0', hs.memories > 0, `memories=${hs.memories}`);
chk('时间线链完好', hs.chain_ok === true);
chk('人格锚一致', hs.anchor === 'ok', hs.anchor_reason || '');
chk('知道第几天', typeof hs.day === 'number', `day=${hs.day}`);

console.log('== 2. 取上下文 ==');
const ctxOut = await client.context('我今天有点累', 6);
chk('ok', ctxOut.ok === true, ctxOut.error || '');
chk('给回了 prompt', typeof ctxOut.prompt === 'string' && ctxOut.prompt.length > 0, `${(ctxOut.prompt || '').length} 字`);
chk('带上了场景/权重之类', !!(ctxOut.scene || ctxOut.weights || ctxOut.quota));
chk('hits 是数组', Array.isArray(ctxOut.hits));

console.log('== 3. 召回 ==');
const rc = await client.recall('白米饭', 5);
chk('ok', rc.ok === true, rc.error || '');
chk('命中是数组', Array.isArray(rc.hits));
if (!rc.hits.length) {
  console.log(`  ℹ️ 空命中时给的是：${rc.note}`);
} else {
  chk('每一条都有 id/text', rc.hits.every((x) => x.id != null && typeof x.text === 'string'));
  console.log(`  第一条：#${rc.hits[0].id} ${String(rc.hits[0].text).slice(0, 40)}`);
}

console.log('== 4. 状态与技能 ==');
const st = await client.status();
chk('status 有内容', st && Object.keys(st).length > 0, `${Object.keys(st || {}).length} 个键`);
const sk = await client.skillList();
chk('skill_list 能取', Array.isArray(sk.skills), `${(sk.skills || []).length} 条技能`);

console.log('== 5. 连不上时的行为（把端口指到死端口）==');
const dead = new CardClient({ port: 1, autoStart: false });
const deadH = await dead.health();
chk('health 不抛异常，返回 null', deadH === null);
const deadCtx = await dead.context('测试');
chk('context 返回 ok:false 而不是抛', deadCtx.ok === false);
chk('带上了那句 hint', typeof deadCtx.hint === 'string' && deadCtx.hint.includes('别凭印象编'));

if (doWrite) {
  console.log('== 6. 写回（真写卡）==');
  const marker = `通道自检 ${new Date().toISOString()}`;
  const rep = await client.report(marker, '自检用的回话，可以忽略。');
  chk('report ok', rep.ok === true, rep.error || '');
  const rem = await client.remember(`${marker}：插件写入测试`, { source: 'cli', region: 'episode' });
  chk('remember ok', rem.ok === true, rem.error || '');
  chk('默认进了长期池', rem.ok !== true || rem.pool === 'long' || rem.mem_id != null, `mem_id=${rem.mem_id}`);
} else {
  console.log('== 6. 写回 跳过（加 --write 才跑）==');
}

console.log('');
console.log(`结果：${pass} 通过，${fail} 失败`);
process.exit(fail === 0 ? 0 : 1);
