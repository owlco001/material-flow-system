/* 管理台公共脚本：body 末尾加载（侧边栏、智能助手悬浮球、统一确认弹窗）。各段原为独立内联脚本。 */
(function(){
  var body = document.body;
  try {
    if (localStorage.getItem('sf_side') === 'collapsed' && window.innerWidth > 1024) body.classList.add('side-collapsed');
  } catch(e) {}
  var collapseBtn = document.getElementById('sideCollapse');
  if (collapseBtn) collapseBtn.addEventListener('click', function(){
    body.classList.toggle('side-collapsed');
    try { localStorage.setItem('sf_side', body.classList.contains('side-collapsed') ? 'collapsed' : ''); } catch(e) {}
  });
  var burger = document.getElementById('hamburger');
  var sidebar = document.getElementById('sidebar');
  var overlay = document.getElementById('sideOverlay');
  function closeSide(){ if(sidebar) sidebar.classList.remove('open'); if(overlay) overlay.classList.remove('show'); }
  if (burger) burger.addEventListener('click', function(){
    if(sidebar) sidebar.classList.toggle('open');
    if(overlay) overlay.classList.toggle('show');
  });
  if (overlay) overlay.addEventListener('click', closeSide);
  // 导航分组折叠（v0.7.0）：当前页所在分组始终展开；其余分组记住用户的展开/收起
  var saved = {};
  try { saved = JSON.parse(localStorage.getItem('sf_side_secs') || '{}') || {}; } catch(e) {}
  document.querySelectorAll('.side-sec').forEach(function(sec){
    var key = sec.dataset.sec, btn = sec.querySelector('.side-group');
    var isActive = sec.classList.contains('active-sec') || !!sec.querySelector('.side-link.active');
    if (isActive) sec.classList.add('open');
    else if (saved[key] === 1) sec.classList.add('open');
    else if (saved[key] === 0) sec.classList.remove('open');
    if (btn) {
      btn.setAttribute('aria-expanded', sec.classList.contains('open') ? 'true' : 'false');
      btn.addEventListener('click', function(){
        sec.classList.toggle('open');
        var open = sec.classList.contains('open');
        btn.setAttribute('aria-expanded', open ? 'true' : 'false');
        saved[key] = open ? 1 : 0;
        try { localStorage.setItem('sf_side_secs', JSON.stringify(saved)); } catch(e) {}
      });
    }
  });
})();

;
let agentSessionId = null;
let agentSending = false;
const AGENT_TIPS = ["这周异常比上周多还是少？", "库存不足的物料有哪些？", "做 5 台某机型还缺什么料？"];
function agentWelcome() {
  const box = document.getElementById("agent-msgs"); box.innerHTML = "";
  const d = document.createElement("div"); d.className = "msg asst";
  d.innerHTML = '<div class="md"><p>你好，我是智能助手，可以查询库存、订单、缺料、趋势等工厂数据。</p></div><div class="ap-tips"></div>';
  const tips = d.querySelector(".ap-tips");
  AGENT_TIPS.forEach(t => { const b = document.createElement("button"); b.type = "button"; b.textContent = t;
    b.onclick = () => { const i = document.getElementById("agent-input"); i.value = t; i.focus(); }; tips.appendChild(b); });
  box.appendChild(d);
}
function agentNewSession() { agentSessionId = null; agentWelcome(); }
function toggleAgentPanel() {
  const p = document.getElementById("agent-panel"); p.classList.toggle("open");
  if (p.classList.contains("open")) document.getElementById("agent-input").focus();
}
function agentAddMsg(role, text) {
  const box = document.getElementById("agent-msgs");
  const div = document.createElement("div");
  div.className = "msg " + (role === "user" ? "user" : "asst");
  if (role === "user") div.textContent = text;
  else { const b = document.createElement("div"); b.className = "md"; b.innerHTML = renderMd(text || ""); div.appendChild(b); }
  box.appendChild(div); box.scrollTop = box.scrollHeight;
  return div;
}
function agentMeta(wrap, sources, tokens) {
  const m = document.createElement("div"); m.className = "ap-meta";
  if (sources && sources.length) {
    const det = document.createElement("details"); const s = document.createElement("summary");
    s.textContent = "依据：" + sources.length + " 次查询"; det.appendChild(s);
    sources.forEach(x => { const d = document.createElement("div"); d.style.margin = "2px 0 2px 10px";
      d.textContent = (x.ok === false ? "✗ " : "✓ ") + (x.label || x.name) + (x.summary ? " → " + x.summary : ""); det.appendChild(d); });
    m.appendChild(det);
  } else { const s = document.createElement("span"); s.style.flex = "1"; s.textContent = "未查询数据"; m.appendChild(s); }
  if (tokens) { const t = document.createElement("span"); t.textContent = tokens + " tokens"; m.appendChild(t); }
  const cp = document.createElement("a"); cp.textContent = "复制";
  cp.onclick = () => navigator.clipboard.writeText(wrap.querySelector(".md").innerText).then(() => { cp.textContent = "已复制"; setTimeout(() => cp.textContent = "复制", 1500); });
  m.appendChild(cp); wrap.appendChild(m);
}
async function sendAgentMsg() {
  const input = document.getElementById("agent-input");
  const msg = input.value.trim();
  if (!msg || agentSending) return;
  input.value = ""; input.style.height = "auto";
  agentAddMsg("user", msg);
  const wrap = agentAddMsg("asst", "");
  const body = wrap.querySelector(".md"); body.classList.add("cursor");
  const steps = document.createElement("div"); steps.className = "chat-steps"; steps.textContent = "思考中…";
  wrap.insertBefore(steps, body);
  const box = document.getElementById("agent-msgs");
  const sendBtn = document.getElementById("agent-send-btn");
  agentSending = true; sendBtn.disabled = true;
  let text = "", n = 0, raf = false;
  const paint = () => { raf = false; body.innerHTML = renderMd(text); box.scrollTop = box.scrollHeight; };
  const handle = ev => {
    if (ev.type === "session") agentSessionId = ev.session_id;
    else if (ev.type === "tool_start") {
      if (!n) steps.textContent = ""; n++;
      const d = document.createElement("div"); d.dataset.tool = ev.name;
      d.innerHTML = '<span class="spin">⟳</span> ' + escHtml(ev.label || ev.name); steps.appendChild(d);
    } else if (ev.type === "tool_end") {
      const d = [...steps.querySelectorAll("div")].reverse().find(x => x.dataset.tool === ev.name && x.querySelector(".spin"));
      if (d) d.textContent = (ev.ok ? "✓ " : "✗ ") + d.textContent.replace(/^⟳\s*/, "") + " · " + ev.summary;
      text = "";
    } else if (ev.type === "delta") {
      if (!n && steps.textContent === "思考中…") steps.textContent = "";
      text += ev.text; if (!raf) { raf = true; requestAnimationFrame(paint); }
    } else if (ev.type === "done") {
      text = ev.reply || text || "(空回复)"; paint(); steps.remove(); body.classList.remove("cursor");
      agentMeta(wrap, ev.sources || [], ev.usage ? ev.usage.prompt_tokens + ev.usage.completion_tokens : null);
    } else if (ev.type === "error") { steps.textContent = ""; text = "出错：" + friendlyAgentError(ev.message); paint(); }
  };
  try {
    const r = await fetch("/admin/agent/chat/stream", { method: "POST", headers: csrfHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ session_id: agentSessionId, message: msg }) });
    if (!r.ok || !r.body) { const t = await r.text(); let m = "HTTP " + r.status; try { m = JSON.parse(t).error.message || m; } catch (_e) {} throw new Error(m); }
    const reader = r.body.getReader(); const dec = new TextDecoder(); let buf = "";
    while (true) {
      const { value, done } = await reader.read(); if (done) break;
      buf += dec.decode(value, { stream: true }); let k;
      while ((k = buf.indexOf("\n\n")) >= 0) {
        const chunk = buf.slice(0, k); buf = buf.slice(k + 2);
        const line = chunk.split("\n").find(x => x.startsWith("data:"));
        if (line) { try { handle(JSON.parse(line.slice(5))); } catch (_e) {} }
      }
    }
  } catch (e) { steps.textContent = ""; text = "出错：" + friendlyAgentError(e.message); paint(); }
  finally { agentSending = false; sendBtn.disabled = false; body.classList.remove("cursor"); box.scrollTop = box.scrollHeight; }
}
document.addEventListener("DOMContentLoaded", function() {
  if (!document.getElementById("agent-panel")) return;
  agentWelcome();
  const i = document.getElementById("agent-input");
  i.addEventListener("input", () => { i.style.height = "auto"; i.style.height = Math.min(i.scrollHeight, 120) + "px"; });
  i.addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); sendAgentMsg(); } });
  document.getElementById("agent-send-btn").addEventListener("click", e => { e.preventDefault(); sendAgentMsg(); });
});

;
(function(){
  var mask = document.getElementById('uiConfirmMask');
  var okBtn = document.getElementById('uiConfirmOk');
  var cancelBtn = document.getElementById('uiConfirmCancel');
  var resolver = null, lastFocus = null;
  function close(result){
    mask.classList.remove('show');
    var r = resolver; resolver = null;
    if (lastFocus && lastFocus.focus) lastFocus.focus();
    if (r) r(result);
  }
  window.uiConfirm = function(text, opts){
    opts = opts || {};
    if (resolver) close(false);
    document.getElementById('uiConfirmTitle').textContent = opts.title || '确认操作？';
    document.getElementById('uiConfirmText').textContent = text || '';
    okBtn.textContent = opts.ok || '确认';
    okBtn.className = 'btn ' + (opts.danger ? 'btn-danger' : '');
    lastFocus = document.activeElement;
    mask.classList.add('show');
    // 危险操作默认聚焦「取消」，防止回车误触
    (opts.danger ? cancelBtn : okBtn).focus();
    return new Promise(function(resolve){ resolver = resolve; });
  };
  okBtn.addEventListener('click', function(){ close(true); });
  cancelBtn.addEventListener('click', function(){ close(false); });
  mask.addEventListener('click', function(e){ if (e.target === mask) close(false); });
  document.addEventListener('keydown', function(e){ if (resolver && e.key === 'Escape') close(false); });
  function optsOf(el){
    return { title: el.dataset.confirmTitle, ok: el.dataset.confirmOk, danger: el.hasAttribute('data-confirm-danger') };
  }
  // 按钮上的 data-confirm：保留 submitter（如 name=dry_run value=0）
  document.addEventListener('click', function(e){
    var b = e.target.closest && e.target.closest('button[data-confirm], a[data-confirm], input[type=submit][data-confirm]');
    if (!b) return;
    if (b.dataset.confirmed) { delete b.dataset.confirmed; return; }
    e.preventDefault(); e.stopImmediatePropagation();
    uiConfirm(b.dataset.confirm, optsOf(b)).then(function(ok){
      if (!ok) return;
      b.dataset.confirmed = '1';
      b.click();
    });
  }, true);
  // 表单上的 data-confirm
  document.addEventListener('submit', function(e){
    var f = e.target;
    if (!f.matches || !f.matches('form[data-confirm]')) return;
    if (f.dataset.confirmed) { delete f.dataset.confirmed; return; }
    e.preventDefault(); e.stopImmediatePropagation();
    var submitter = e.submitter;
    uiConfirm(f.dataset.confirm, optsOf(f)).then(function(ok){
      if (!ok) return;
      f.dataset.confirmed = '1';
      if (f.requestSubmit) f.requestSubmit(submitter || undefined); else f.submit();
    });
  }, true);
})();
