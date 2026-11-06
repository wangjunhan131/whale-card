/**
 * 卡通道（Card transport）—— 跟 8848 上的 mind_server 说话。
 *
 * 为什么这里只有 HTTP：卡是 Python 进程，自己是唯一的库写者。
 * 插件要是直接开 card.db，两个进程同时开 WAL 会互踩（这是本项目的铁律）。
 * 所以这里只做一件事：把 HTTP 请求发过去，把 JSON 拿回来。
 */

const ERR_HINT = '这不是「记忆丢了」，是卡现在没连上。别凭印象编。';

export class CardClient {
  constructor(config = {}) {
    this.host = config.host || '127.0.0.1';
    this.port = Number(config.port || 8848);
    this.timeoutMs = Number(config.timeoutMs || 30000);
    this.healthTimeoutMs = Number(config.healthTimeoutMs || 3000);
    this.cardDir = config.cardDir || '';
    this.pythonPath = config.pythonPath || 'python';
    this.autoStart = config.autoStart !== false;
    this._starting = null;
    this.logger = config.logger || null;
  }

  get base() {
    return `http://${this.host}:${this.port}`;
  }

  _log(level, msg) {
    try {
      this.logger?.[level]?.(msg);
    } catch {
      /* 记日志失败不许影响主流程 */
    }
  }

  async _request(path, init = {}, timeoutMs = this.timeoutMs) {
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), timeoutMs);
    try {
      const res = await fetch(this.base + path, { ...init, signal: ctl.signal });
      const text = await res.text();
      if (!res.ok) {
        throw new Error(`HTTP ${res.status} ${path}: ${text.slice(0, 200)}`);
      }
      return text ? JSON.parse(text) : {};
    } catch (e) {
      if (e?.name === 'AbortError') {
        throw new Error(`请求超时（${timeoutMs}ms）：${path}`);
      }
      throw e;
    } finally {
      clearTimeout(timer);
    }
  }

  get(path, timeoutMs) {
    return this._request(path, { method: 'GET' }, timeoutMs);
  }

  post(path, body, timeoutMs) {
    return this._request(
      path,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json; charset=utf-8' },
        body: JSON.stringify(body ?? {}),
      },
      timeoutMs,
    );
  }

  /** 服务在不在。不在就返回 null —— 这个方法永远不抛。 */
  async health() {
    try {
      return await this.get('/health', this.healthTimeoutMs);
    } catch {
      return null;
    }
  }

  /**
   * 服务没起来就拉起来。
   * DSH 开会话时没人记得先起服务，这一层是「打开窗口就是你」的前提。
   */
  async ensure() {
    const h = await this.health();
    if (h) return h;
    if (!this.autoStart || !this.cardDir) return null;
    if (this._starting) return this._starting;

    this._starting = (async () => {
      const { spawn } = await import('node:child_process');
      const path = await import('node:path');
      const server = path.join(this.cardDir, 'mind_server.py');
      try {
        const child = spawn(
          this.pythonPath,
          ['-X', 'utf8', server, '--port', String(this.port)],
          {
            cwd: this.cardDir,
            detached: true,
            windowsHide: true,
            stdio: 'ignore',
          },
        );
        child.unref();
      } catch (e) {
        this._log('warn', `拉起 mind_server 失败：${e?.message || e}`);
        return null;
      }
      const t0 = Date.now();
      while (Date.now() - t0 < 25000) {
        await new Promise((r) => setTimeout(r, 500));
        const h2 = await this.health();
        if (h2) return h2;
      }
      return null;
    })().finally(() => {
      this._starting = null;
    });

    return this._starting;
  }

  fail(msg) {
    return { ok: false, error: msg, hint: ERR_HINT };
  }

  // ── 具体的卡接口 ──────────────────────────────────────────
  async context(message, recent = 12) {
    const h = await this.ensure();
    if (!h) return this.fail('心智服务连不上（8848）');
    const body = { message: String(message || '').trim() };
    let recentLines = 0;
    if (Number(recent) > 0) {
      try {
        const rec = (await this.post('/recent', { n: Number(recent), max_chars: 2000 })).recent || [];
        if (rec.length) {
          body.recent = rec;
          recentLines = rec.length;
        }
      } catch {
        /* 拿不到近期原文不算错，上下文照样能拼 */
      }
    }
    const out = await this.post('/context', body);
    const c = out.context || {};
    return {
      ok: true,
      prompt: c.prompt || '',
      mood: c.mood,
      scene: c.scene,
      weights: c.weights_named,
      quota: c.quota,
      est_tokens: c.est_tokens,
      hits: (c.hits || []).map((x) => ({
        id: x.id,
        text: x.text,
        score: x.score,
        w_now: x.w_now,
      })),
      recent_lines: recentLines,
    };
  }

  async report(userMessage, reply = '') {
    const h = await this.ensure();
    if (!h) return this.fail('心智服务连不上（8848）');
    if (!String(userMessage || '').trim()) {
      return { ok: false, error: 'user_message 不能为空' };
    }
    const out = await this.post('/report', {
      user_message: String(userMessage).trim(),
      reply: String(reply || '').trim(),
    });
    return { ok: true, report: out.report || {} };
  }

  async recall(query, topK = 8) {
    const h = await this.ensure();
    if (!h) return this.fail('心智服务连不上（8848）');
    const hits = (await this.post('/recall', { query: String(query || '').trim(), top_k: Number(topK) })).hits || [];
    if (!hits.length) {
      return { ok: true, hits: [], note: '卡里没翻到相关的 —— 这是「没有」，不是「忘了」' };
    }
    return {
      ok: true,
      hits: hits.map((x) => ({
        id: x.id,
        text: x.text,
        score: x.score,
        w_now: x.w_now,
        region: x.region,
        pool: x.pool,
      })),
    };
  }

  async remember(text, opts = {}) {
    const h = await this.ensure();
    if (!h) return this.fail('心智服务连不上（8848）');
    const body = { text: String(text || '').trim() };
    if (!body.text) return { ok: false, error: 'text 不能为空' };
    for (const k of ['source', 'speaker', 'region', 'pool', 'salience', 'about_uid']) {
      if (opts[k] !== undefined && opts[k] !== null && opts[k] !== '') body[k] = opts[k];
    }
    // 默认进长期池：能走到这一层的都是「判定过值得记」的东西，
    // 要不要淡出由 region 的衰减曲线管，不该由池子容量管。
    if (!body.pool) body.pool = 'long';
    if (!body.region) body.region = 'episode';
    return await this.post('/remember', body);
  }

  async status() {
    const h = await this.ensure();
    if (!h) return this.fail('心智服务连不上（8848）');
    return (await this.get('/status')).status || {};
  }

  async skillList() {
    const h = await this.ensure();
    if (!h) return this.fail('心智服务连不上（8848）');
    const out = await this.get('/skill_list');
    return { skills: out.skills || [], stats: out.stats || {} };
  }

  healthSummary(h) {
    const tl = h.timeline || {};
    return {
      ok: true,
      db: h.db,
      memories: h.memories,
      sealed: h.sealed,
      uptime_s: h.uptime_s,
      day: tl.day,
      today: tl.today,
      chain_ok: tl.chain_ok,
      chain_bad: tl.chain_bad || [],
      birth_ok: tl.birth_ok,
      birth_bad: tl.birth_bad || [],
      anchor: tl.anchor_verdict,
      anchor_reason: tl.anchor_reason,
    };
  }
}
export { ERR_HINT };
