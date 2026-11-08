'use strict';

/* 界面只做两件事：把 ws 事件画出来、把用户意图发回去。
   会话标题/步数持久化在 localStorage（刷新不丢）；对话消息本身没有落库，
   点开旧会话会如实提示。工具轨迹 / 版本入栈 / 护栏参数都来自真实运行。 */

const $ = (id) => document.getElementById(id);

const el = {
  messages: $('messages'),
  prompt: $('prompt'),
  sendBtn: $('sendBtn'),
  stopBtn: $('stopBtn'),
  sessionList: $('sessionList'),
  chatTitle: $('chatTitle'),
  rootPath: $('rootPath'),
  stepPill: $('stepPill'),
  tokenPill: $('tokenPill'),
  stack: $('stack'),
  guards: $('guards'),
  guardTag: $('guardTag'),
  connDot: $('connDot'),
  connText: $('connText'),
  connBtn: $('connBtn'),
  modelName: $('modelName'),
  newChat: $('newChat'),
};

const TOOL_META = {
  read: { icon: 'R', label: 'read', kind: 'read' },
  write: { icon: 'W', label: 'write', kind: 'write' },
  edit: { icon: 'E', label: 'edit', kind: 'edit' },
  bash: { icon: '$', label: 'bash', kind: 'bash' },
  use_skill: { icon: 'S', label: 'use_skill', kind: 'skill' },
};

// mcp__git__git_status → 图标 M，显示成 "git · git_status"
function toolMeta(name) {
  if (typeof name === 'string' && name.startsWith('mcp__')) {
    const parts = name.split('__');
    const server = parts[1] || 'mcp';
    const tool = parts.slice(2).join('__') || name;
    return { icon: 'M', label: `${server} · ${tool}`, kind: 'mcp' };
  }
  return TOOL_META[name] || { icon: '?', label: name || 'tool', kind: 'other' };
}

/* ───────────────── 会话持久化（localStorage） ─────────────────
   只存标题 / 步数 / 结果状态 / 时间，消息内容不落库——所以点开旧会话
   能看到当时做了什么，看不到对话全文，提示里写清楚。 */

const SESS_KEY = 'da.sessions.v1';
const SESS_MAX = 30;

function loadSessions() {
  try {
    const list = JSON.parse(localStorage.getItem(SESS_KEY) || '[]');
    return Array.isArray(list) ? list : [];
  } catch {
    return [];
  }
}

function saveSessions() {
  try {
    localStorage.setItem(SESS_KEY, JSON.stringify(SESSIONS.slice(0, SESS_MAX)));
  } catch { /* 隐私模式等场景存不进去就算了，不影响运行 */ }
}

let SESSIONS = loadSessions();

/* 新会话发消息时创建，任务结束时更新结果状态 */
function upsertSession(id, title, meta, status) {
  if (!id) return;
  const found = SESSIONS.find((s) => s.id === id);
  if (found) {
    found.title = title;
    found.meta = meta;
    found.status = status;
    found.ts = Date.now();
  } else {
    SESSIONS.unshift({ id, title, meta, status, ts: Date.now() });
  }
  saveSessions();
  renderSessions();
}

function dayLabel(ts) {
  const one = 24 * 60 * 60 * 1000;
  const start = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const diff = start(new Date()) - start(new Date(ts));
  if (diff < one) return '今天';
  if (diff < 2 * one) return '昨天';
  return '更早';
}


const state = {
  ws: null,
  open: false,
  running: false,
  attempt: 0,
  steps: 0,
  tokens: 0,
  turn: null,
  versions: [],
  titleLocked: false,
  sessionId: null, // localStorage 会话 id，新建/点开旧会话时生成或沿用
  pending: new Map(), // call_id -> 工具卡片
  lastPending: null,
};

/* ───────────────── 小工具 ───────────────── */

function h(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

function nearBottom() {
  const m = el.messages;
  return m.scrollHeight - m.scrollTop - m.clientHeight < 120;
}

function follow(force) {
  if (force || nearBottom()) {
    el.messages.scrollTop = el.messages.scrollHeight;
  }
}

function withFollow(fn) {
  const stick = nearBottom();
  fn();
  if (stick) el.messages.scrollTop = el.messages.scrollHeight;
}

function addNote(kind, text) {
  withFollow(() => {
    const n = h('div', 'note', text);
    n.dataset.kind = kind;
    el.messages.appendChild(n);
  });
}

/* ───────────────── 消息 ───────────────── */

function addUser(text) {
  withFollow(() => {
    const wrap = h('div', 'msg msg-user');
    wrap.appendChild(h('div', 'bubble', text));
    el.messages.appendChild(wrap);
  });
}

function addAiShell() {
  const wrap = h('div', 'msg msg-ai');
  const who = h('div', 'who');
  who.append(h('span', 'badge-mark'), h('span', null, '助手'));
  const text = h('div', 'text');
  const caret = h('span', 'caret');
  text.appendChild(caret);
  wrap.append(who, text);
  el.messages.appendChild(wrap);
  return { wrap, text, caret };
}

function onDelta(chunk) {
  if (!chunk) return;
  const turn = state.turn;
  if (!turn) return;
  withFollow(() => {
    if (!turn.ai) turn.ai = addAiShell();
    const { text, caret } = turn.ai;
    caret.remove();
    text.appendChild(document.createTextNode(chunk));
    text.appendChild(caret);
  });
}

function summarize(name, args) {
  if (!args || typeof args !== 'object') return '';
  if (name === 'bash') return String(args.command || '');
  if (name === 'read') return String(args.path || '') + (args.offset ? `:${args.offset}` : '');
  if (name === 'write' || name === 'edit') return String(args.path || '');
  return JSON.stringify(args).slice(0, 90);
}

function exitLabel(name, text) {
  if (name === 'bash') {
    const m = /\[exit_code\]\s*(-?\d+)/.exec(text || '');
    if (m) return m[1] === '0' ? 'exit 0' : `exit ${m[1]}`;
    return '完成';
  }
  return '完成';
}

function addTool(ev) {
  const meta = toolMeta(ev.name);
  const box = h('div', 'tool');
  box.dataset.status = 'run';

  const head = h('div', 'tool-head');
  const icon = h('span', 'tool-icon', meta.icon);
  icon.dataset.kind = meta.kind;
  const name = h('span', 'tool-name', meta.label);
  const argsText = summarize(ev.name, ev.args);
  const args = h('code', 'tool-args', argsText);
  args.title = argsText;
  const st = h('span', 'tool-state', '运行中');
  head.append(icon, name, args, st);

  const out = h('pre', 'tool-out');
  out.dataset.empty = '1';
  const toggle = h('button', 'tool-toggle', '');
  toggle.type = 'button';
  toggle.hidden = true;

  const rec = { box, st, out, toggle, name: ev.name, args: argsText, status: 'run', lines: 0 };

  toggle.onclick = () => {
    const open = out.classList.toggle('open');
    toggle.textContent = open ? '收起输出' : `展开输出（${rec.lines} 行）`;
  };

  box.append(head, out, toggle);

  withFollow(() => el.messages.appendChild(box));

  if (ev.call_id) state.pending.set(ev.call_id, rec);
  else state.lastPending = rec;
  if (state.turn) state.turn.tools.push(rec);
  return rec;
}

function fillTool(ev) {
  let rec = ev.call_id ? state.pending.get(ev.call_id) : null;
  if (!rec) rec = state.lastPending;
  if (!rec) rec = addTool({ name: ev.name, args: {}, call_id: '' });

  if (ev.call_id) state.pending.delete(ev.call_id);
  if (state.lastPending === rec) state.lastPending = null;

  const text = ev.text || '';
  const lines = text ? text.split('\n').length : 0;
  rec.lines = lines;
  rec.out.textContent = text;
  rec.out.dataset.empty = text ? '0' : '1';
  rec.toggle.hidden = !text;
  rec.toggle.textContent = lines > 8 ? `展开输出（${lines} 行）` : '展开输出';

  const status = ev.is_error ? 'err' : 'ok';
  rec.status = status;
  rec.box.dataset.status = status;
  rec.st.textContent = ev.is_error ? '失败' : exitLabel(rec.name, text);

  withFollow(() => {});
}

/* ───────────────── 一轮结束 ───────────────── */

function updateStats() {
  el.stepPill.textContent = `步数 ${state.steps}`;
  el.tokenPill.textContent = `tokens ${state.tokens.toLocaleString('zh-CN')}`;
}

function setRunning(run) {
  state.running = run;
  el.sendBtn.disabled = run;
  el.stopBtn.disabled = !run;
  el.stopBtn.textContent = '停止';
}

function pushVersion(turn, ev, note) {
  state.versions.unshift({
    n: state.versions.length + 1,
    title: (turn && turn.prompt ? turn.prompt : '未命名任务').slice(0, 30),
    steps: ev.steps || 0,
    tokens: ev.tokens || 0,
    tools: turn ? turn.tools.map((t) => ({ name: t.name, args: t.args, status: t.status })) : [],
    at: new Date(),
    note: note || '',
  });
  renderStack();
}

function finishTurn(kind, ev) {
  const turn = state.turn;

  if (turn && turn.ai) turn.ai.caret.remove();

  if (kind === 'done') {
    if (turn && !turn.ai && ev && ev.content) {
      const shell = addAiShell();
      turn.ai = shell;
      shell.caret.remove();
      shell.text.textContent = ev.content;
    }
    if (turn && turn.ai && !turn.ai.text.textContent.trim()) {
      turn.ai.wrap.remove();
      turn.ai = null;
    }
    state.steps = ev && ev.steps != null ? ev.steps : state.steps;
    state.tokens = ev && ev.tokens != null ? ev.tokens : state.tokens;
    updateStats();
    if (turn) pushVersion(turn, ev);
    addNote('ok', `完成 · 共 ${(ev && ev.steps) || 0} 步`);
    upsertSession(state.sessionId, el.chatTitle.textContent, `${state.steps} 步 · 完成`, 'done');
  } else if (kind === 'cancelled') {
    state.steps = ev && ev.steps != null ? ev.steps : state.steps;
    updateStats();
    if (turn) pushVersion(turn, { steps: (ev && ev.steps) || 0, tokens: state.tokens }, '中断');
    addNote('warn', `已中断 · 停在第 ${(ev && ev.steps) || 0} 步，没有继续跑`);
    upsertSession(state.sessionId, el.chatTitle.textContent, `${state.steps} 步 · 已中断`, 'stop');
  } else if (kind === 'error') {
    addNote('err', `失败 · ${(ev && ev.message) || '未知错误'}`);
    if (turn) pushVersion(turn, { steps: (ev && ev.steps) || 0, tokens: state.tokens }, '失败');
    upsertSession(state.sessionId, el.chatTitle.textContent, '失败', 'err');
  } else if (kind === 'disconnected') {
    addNote('err', '连接中断，本轮状态未知');
  }

  if (turn) {
    turn.tools.forEach((t) => {
      if (t.status === 'run') {
        t.status = 'stop';
        t.box.dataset.status = 'stop';
        t.st.textContent = '未返回';
      }
    });
  }

  state.turn = null;
  state.pending.clear();
  state.lastPending = null;
  setRunning(false);
  follow(true);
}

/* ───────────────── 右栏：版本堆叠 ───────────────── */

function renderStack() {
  el.stack.textContent = '';

  if (!state.versions.length) {
    const empty = h('div', 'stack-empty');
    empty.append(
      h('div', null, '还没有版本。'),
      h('div', null, '每完成一轮，这一轮改了哪些文件、跑了几步，就会压一张卡片进来。')
    );
    el.stack.appendChild(empty);
    return;
  }

  state.versions.forEach((v) => {
    const details = document.createElement('details');
    details.className = 'ver';

    const summary = document.createElement('summary');
    const no = h('span', 'ver-no', `v${v.n}`);
    const title = h('span', 'ver-title', v.title);
    const meta = h('span', 'ver-meta', `${v.steps} 步`);
    title.title = v.title;
    summary.append(no, title, meta);

    const body = h('div', 'ver-body');
    if (v.tools.length) {
      v.tools.forEach((t) => {
        const line = h('div', 'ver-line');
        const m = toolMeta(t.name);
        const k = h('span', 'k', m.label);
        k.dataset.kind = m.kind;
        const value = h('span', 'v', t.args || '—');
        value.title = t.args || '';
        line.append(k, value);
        body.appendChild(line);
      });
    } else {
      body.appendChild(h('div', 'ver-line', '本轮没有调用工具'));
    }
    if (v.note) body.appendChild(h('div', 'ver-time', `结果：${v.note}`));
    body.appendChild(h('div', 'ver-time', v.at.toLocaleTimeString('zh-CN', { hour12: false })));

    details.append(summary, body);
    el.stack.appendChild(details);
  });
}

/* ───────────────── 右栏：护栏 ───────────────── */

function renderGuards(rows) {
  el.guards.textContent = '';
  rows.forEach(([key, value, warn]) => {
    const row = h('div', 'guard-row');
    const dd = h('dd', null, value);
    dd.title = value;
    if (warn) dd.dataset.warn = '1';
    row.append(h('dt', null, key), dd);
    el.guards.appendChild(row);
  });
}

async function loadHealth() {
  try {
    const res = await fetch('/health', { cache: 'no-store' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const j = await res.json();
    el.modelName.textContent = `模型 ${j.model}`;
    el.rootPath.textContent = `工作区 ${j.root}`;
    el.guardTag.textContent = j.has_key ? '已就绪' : '缺 API Key · 点此配置';
    el.guardTag.style.color = j.has_key ? '' : 'var(--warn)';
    el.guardTag.style.cursor = j.has_key ? 'default' : 'pointer';
    el.guardTag.onclick = j.has_key ? null : openCfg;
    renderGuards([
      ['步数上限', String(j.max_steps)],
      ['token 预算', Number(j.token_budget).toLocaleString('zh-CN')],
      ['整轮超时', j.overall_timeout ? `${j.overall_timeout} s` : '—'],
      ['单步超时', j.step_timeout ? `${j.step_timeout} s` : '—'],
      ['bash 超时', j.bash_timeout ? `${j.bash_timeout} s` : '—'],
      ['MCP server', (j.mcp_servers || []).join(' · ') || '未接入'],
      ['技能', (j.skills || []).join(' · ') || '未加载'],
      ['工作区', j.root],
    ]);
  } catch (e) {
    el.guardTag.textContent = '未连通';
    el.guardTag.style.color = 'var(--danger)';
    renderGuards([['服务端', `连不上 /health：${e.message}`, true]]);
  }
}

/* ───────────────── 左栏：会话 ───────────────── */

function renderSessions() {
  el.sessionList.textContent = '';
  if (!SESSIONS.length) {
    el.sessionList.appendChild(
      h('div', 'sess-group', '暂无历史会话 · 发一条消息就有了'),
    );
    return;
  }
  /* 按 今天/昨天/更早 分组，保持时间倒序 */
  let lastLabel = null;
  SESSIONS.forEach((item) => {
    const label = dayLabel(item.ts);
    if (label !== lastLabel) {
      el.sessionList.appendChild(h('div', 'sess-group', label));
      lastLabel = label;
    }
    const btn = h('button', 'sess');
    btn.type = 'button';
    if (item.id === state.sessionId) btn.classList.add('active');
    btn.append(h('span', 'sess-title', item.title), h('span', 'sess-meta', item.meta));
    btn.onclick = () => openSaved(item, btn);
    el.sessionList.appendChild(btn);
  });
}

function openSaved(item, btn) {
  if (state.running) {
    addNote('warn', '当前任务还在跑，先停止再切换');
    return;
  }
  el.sessionList.querySelectorAll('.sess').forEach((b) => b.classList.remove('active'));
  btn.classList.add('active');

  state.titleLocked = true;
  state.sessionId = item.id;
  state.turn = null;
  state.versions = [];
  state.steps = Number((/\d+/.exec(item.meta) || ['0'])[0]);
  state.tokens = 0;

  el.chatTitle.textContent = item.title;
  updateStats();
  renderStack();
  el.messages.textContent = '';
  addNote('warn', '历史会话只保存了标题和步数（localStorage），对话内容没有落库；在这里发新消息会接着这个会话继续。');
  el.prompt.focus();
}

function newSession() {
  if (state.running) {
    addNote('warn', '当前任务还在跑，先停止再新建');
    return;
  }
  state.titleLocked = false;
  state.sessionId = `s${Date.now()}`;
  state.turn = null;
  state.versions = [];
  state.steps = 0;
  state.tokens = 0;

  el.chatTitle.textContent = '新会话';
  el.sessionList.querySelectorAll('.sess').forEach((b) => b.classList.remove('active'));
  renderSessions();
  updateStats();
  renderStack();
  renderWelcome();
  el.prompt.focus();
}

/* ───────────────── 欢迎页 ───────────────── */

function renderWelcome() {
  el.messages.textContent = '';
  const box = h('div', 'welcome');
  box.append(
    h('h2', null, '今天想改点什么？'),
    h('p', null, '助手会先读文件再动手，每一次工具调用都会留在中间的轨迹里。')
  );

  const chips = h('div', 'chips');
  [
    '读一下 samples/stats.py，把空列表的 bug 修掉再跑测试',
    '跑一遍 samples 的测试，失败的话定位原因',
    '看看 tools.py 的 edit 为什么出现多次就要报错',
  ].forEach((text) => {
    const chip = h('button', 'chip', text);
    chip.type = 'button';
    chip.onclick = () => {
      el.prompt.value = text;
      autoGrow();
      el.prompt.focus();
    };
    chips.appendChild(chip);
  });

  box.appendChild(chips);
  el.messages.appendChild(box);
}

/* ───────────────── 发送 / 停止 ───────────────── */

function autoGrow() {
  el.prompt.style.height = 'auto';
  el.prompt.style.height = `${Math.min(el.prompt.scrollHeight, 180)}px`;
}

function send() {
  const text = el.prompt.value.trim();
  if (!text) return;

  if (!state.open) {
    addNote('err', '还没连上服务端：点左下角状态重连，或先运行 python -m uvicorn server:app');
    return;
  }
  if (state.running) {
    addNote('warn', '上一轮还在跑，先停止或等它结束');
    return;
  }

  const welcome = el.messages.querySelector('.welcome');
  if (welcome) welcome.remove();

  addUser(text);
  if (!state.titleLocked) {
    el.chatTitle.textContent = text.slice(0, 26);
    state.titleLocked = true;
  }
  if (!state.sessionId) state.sessionId = `s${Date.now()}`;
  upsertSession(state.sessionId, el.chatTitle.textContent, '运行中', 'run');

  state.turn = { prompt: text, ai: null, tools: [] };
  state.pending.clear();
  state.lastPending = null;

  try {
    state.ws.send(JSON.stringify({ type: 'start', prompt: text }));
  } catch (e) {
    addNote('err', `发送失败：${e.message}`);
    state.turn = null;
    return;
  }

  el.prompt.value = '';
  autoGrow();
  setRunning(true);
  follow(true);
}

function stop() {
  if (!state.running) return;
  if (state.open) state.ws.send(JSON.stringify({ type: 'cancel' }));
  el.stopBtn.disabled = true;
  el.stopBtn.textContent = '停止中…';
  addNote('warn', '已请求停止，等当前步骤收尾');
}

/* ───────────────── WebSocket ───────────────── */

function setConn(mode, text) {
  el.connDot.dataset.state = mode;
  el.connText.textContent = text;
}

function handle(ev) {
  switch (ev.type) {
    case 'start':
      if (ev.root) el.rootPath.textContent = `工作区 ${ev.root}`;
      if (ev.model) el.modelName.textContent = `模型 ${ev.model}`;
      setRunning(true);
      break;
    case 'content_delta':
      onDelta(ev.text || '');
      break;
    case 'content_done':
      if (state.turn && state.turn.ai) state.turn.ai.caret.remove();
      break;
    case 'tool_call':
      addTool(ev);
      break;
    case 'tool_result':
      fillTool(ev);
      break;
    case 'done':
      finishTurn('done', ev);
      break;
    case 'cancelled':
      finishTurn('cancelled', ev);
      break;
    case 'error':
      finishTurn('error', ev);
      break;
    case 'pong':
      break;
    default:
      break;
  }
}

function connect() {
  if (state.ws && (state.ws.readyState === WebSocket.OPEN || state.ws.readyState === WebSocket.CONNECTING)) {
    return;
  }
  setConn('wait', '连接中…');
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const ws = new WebSocket(`${proto}//${location.host}/ws`);
  state.ws = ws;

  ws.onopen = () => {
    state.open = true;
    state.attempt = 0;
    setConn('on', '已连接');
    ws.send(JSON.stringify({ type: 'ping' }));
  };

  ws.onmessage = (e) => {
    let ev;
    try {
      ev = JSON.parse(e.data);
    } catch {
      return;
    }
    handle(ev);
  };

  ws.onclose = () => {
    state.open = false;
    setConn('off', '已断开 · 点击重连');
    if (state.running) finishTurn('disconnected', null);
    if (state.attempt < 6) {
      state.attempt += 1;
      const delay = Math.min(800 * 2 ** state.attempt, 12000);
      window.setTimeout(() => {
        if (!state.open) connect();
      }, delay);
    }
  };

  ws.onerror = () => {
    /* onclose 会跟着来，统一在那儿处理 */
  };
}

/* ───────────────── 配置 Key ───────────────── */

const cfg = {
  dialog: $('cfgDialog'),
  form: $('cfgForm'),
  key: $('cfgKey'),
  base: $('cfgBase'),
  model: $('cfgModel'),
  msg: $('cfgMsg'),
  btn: $('cfgBtn'),
  cancel: $('cfgCancel'),
};

function openCfg() {
  cfg.msg.textContent = '';
  cfg.model.value = el.modelName.textContent.replace(/^模型\s*/, '');
  if (typeof cfg.dialog.showModal === 'function') cfg.dialog.showModal();
}

function closeCfg() {
  if (typeof cfg.dialog.close === 'function') cfg.dialog.close();
}

cfg.btn.addEventListener('click', openCfg);
cfg.cancel.addEventListener('click', closeCfg);

cfg.form.addEventListener('submit', async (e) => {
  e.preventDefault();
  const payload = {
    api_key: cfg.key.value.trim(),
    base_url: cfg.base.value.trim(),
    model: cfg.model.value.trim(),
  };
  if (!payload.api_key) {
    cfg.msg.textContent = 'Key 不能为空';
    return;
  }
  cfg.msg.textContent = '保存中…';
  try {
    const res = await fetch('/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || `HTTP ${res.status}`);
    cfg.msg.textContent = `已保存到 ${data.path}`;
    cfg.key.value = '';
    await loadHealth();
    window.setTimeout(closeCfg, 700);
  } catch (err) {
    cfg.msg.textContent = `保存失败：${err.message}`;
  }
});

/* ───────────────── 绑定 ───────────────── */

el.sendBtn.addEventListener('click', send);
el.stopBtn.addEventListener('click', stop);
el.newChat.addEventListener('click', newSession);
el.connBtn.addEventListener('click', () => {
  state.attempt = 0;
  connect();
});

el.prompt.addEventListener('input', autoGrow);
el.prompt.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    send();
  }
});

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') stop();
});

window.addEventListener('beforeunload', () => {
  if (state.ws) state.ws.close();
});

/* ───────────────── 启动 ───────────────── */

renderSessions();
renderWelcome();
renderStack();
updateStats();
loadHealth();
connect();
window.setInterval(() => {
  if (state.open) state.ws.send(JSON.stringify({ type: 'ping' }));
}, 25000);
