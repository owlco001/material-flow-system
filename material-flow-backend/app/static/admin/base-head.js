/* 管理台公共脚本：head 同步加载（csrfHeaders / escHtml / renderMd 等，页面内联脚本依赖）。 */
// 轻量 Markdown 渲染；先转义再套标签，杜绝注入
function escHtml(t) { return String(t == null ? "" : t).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])); }
// 管理台 JSON / 上传接口统一带 CSRF 头（服务端 _agent_guard 校验 X-CSRF-Token）
function csrfToken() { const m = document.querySelector('meta[name="csrf-token"]'); return m ? m.content : ""; }
function csrfHeaders(extra) { return Object.assign({ "X-CSRF-Token": csrfToken() }, extra || {}); }
function inlineMd(t) {
  return escHtml(t)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
    .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, "$1<i>$2</i>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
    .replace(/([+＋])(\d[\d,.]*\s*%)/g, '<span class="up">$1$2</span>')
    .replace(/(^|[\s（(])([-−])(\d[\d,.]*\s*%)/g, '$1<span class="down">$2$3</span>');
}
const NUM_RE = /^[-+−]?[\d,]+(\.\d+)?\s*(%|个|件|台|套|条|笔|次|分钟|小时|h|kg|m|pcs)?$/;
const numOf = t => parseFloat(String(t).replace(/<[^>]+>/g, "").replace(/[,，\s]/g, "").replace("−", "-"));
let mdTblSeq = 0;
function mdTable(rows) {
  const split = r => r.trim().replace(/^\||\|$/g, "").split("|").map(c => c.trim());
  const head = split(rows[0]); const body = rows.slice(2).map(split);
  const isNum = head.map((_, j) => body.length > 0 && body.every(r => !r[j] || NUM_RE.test(r[j])) && body.some(r => r[j]));
  // 只有一个数值列且行数 ≥2 时画内嵌条形，便于看大小
  const numCols = isNum.map((v, j) => v ? j : -1).filter(j => j >= 0);
  const barCol = numCols.length === 1 && body.length >= 2 ? numCols[0] : -1;
  const max = barCol >= 0 ? Math.max(...body.map(r => Math.abs(numOf(r[barCol])) || 0), 0) : 0;
  const LIMIT = 15; const id = "mdt" + (++mdTblSeq);
  let h = '<div class="tbl-wrap" id="' + id + '"><table class="mdt"><tr>' +
    head.map((c, j) => '<th class="' + (isNum[j] ? "num" : "") + '">' + inlineMd(c) + "</th>").join("") + "</tr>";
  body.forEach((r, i) => {
    h += '<tr class="' + (i >= LIMIT ? "more" : "") + '">' + head.map((_, j) => {
      const c = r[j] || ""; let cell = inlineMd(c);
      if (j === barCol && max > 0) cell = '<span class="bar" style="width:' + Math.max(2, Math.round(60 * Math.abs(numOf(c)) / max)) + 'px"></span>' + cell;
      return '<td class="' + (isNum[j] ? "num" : "") + '">' + cell + "</td>";
    }).join("") + "</tr>";
  });
  h += '</table><div class="tbl-bar"><span class="muted">' + body.length + " 行</span>";
  if (body.length > LIMIT) h += '<a onclick="this.closest(\'.tbl-wrap\').classList.toggle(\'open\');this.textContent=this.textContent===\'展开全部\'?\'收起\':\'展开全部\'">展开全部</a>';
  h += '<a onclick="copyTable(\'' + id + '\', this)">复制表格</a></div></div>';
  return h;
}
function copyTable(id, a) {
  const rows = [...document.querySelectorAll("#" + id + " tr")].map(tr => [...tr.children].map(td => td.innerText.trim()).join("\t")).join("\n");
  navigator.clipboard.writeText(rows).then(() => { a.textContent = "已复制"; setTimeout(() => a.textContent = "复制表格", 1500); });
}
// 轻量 Markdown：先转义再套标签，杜绝注入
function renderMd(src) {
  const lines = String(src).replace(/\r/g, "").split("\n"); const out = []; let i = 0; let first = true;
  while (i < lines.length) {
    const l = lines[i];
    if (/^\s*```/.test(l)) {
      const buf = []; i++;
      while (i < lines.length && !/^\s*```/.test(lines[i])) buf.push(lines[i++]);
      i++; out.push("<pre><code>" + escHtml(buf.join("\n")) + "</code></pre>"); first = false; continue;
    }
    if (/^\s*\|.*\|\s*$/.test(l) && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}/.test(lines[i + 1])) {
      const rows = [l, lines[i + 1]]; i += 2;
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) rows.push(lines[i++]);
      out.push(mdTable(rows)); first = false; continue;
    }
    if (/^\s*([-*•]|\d+[.)、])\s+/.test(l)) {
      const ordered = /^\s*\d+/.test(l); let h = ordered ? "<ol>" : "<ul>";
      while (i < lines.length && /^\s*([-*•]|\d+[.)、])\s+/.test(lines[i])) {
        const sub = /^\s{2,}/.test(lines[i]);
        h += '<li' + (sub ? ' style="margin-left:16px;list-style:circle"' : "") + ">" + inlineMd(lines[i].replace(/^\s*([-*•]|\d+[.)、])\s+/, "")) + "</li>"; i++;
      }
      out.push(h + (ordered ? "</ol>" : "</ul>")); first = false; continue;
    }
    if (/^\s*>/.test(l)) {
      const buf = []; while (i < lines.length && /^\s*>/.test(lines[i])) buf.push(inlineMd(lines[i++].replace(/^\s*>\s?/, "")));
      out.push("<blockquote>" + buf.join("<br>") + "</blockquote>"); first = false; continue;
    }
    if (/^\s*(-{3,}|\*{3,})\s*$/.test(l)) { out.push("<hr>"); i++; continue; }
    const m = l.match(/^(#{1,6})\s+(.*)/);
    if (m) { out.push((m[1].length <= 2 ? "<h4>" : "<h5>") + inlineMd(m[2]) + (m[1].length <= 2 ? "</h4>" : "</h5>")); i++; first = false; continue; }
    if (!l.trim()) { i++; continue; }
    // 首段视为结论，加粗突出
    out.push('<p' + (first ? ' class="conclusion"' : "") + ">" + inlineMd(l) + "</p>"); first = false; i++;
  }
  return out.join("");
}
// 智能助手错误文案友好化（v0.7.0）：后端原始 JSON / 英文配置名 → 中文提示
function friendlyAgentError(raw) {
  let msg = String(raw == null ? "" : raw);
  try { const j = JSON.parse(msg); if (j && j.error) msg = j.error.message || j.error.code || msg; } catch (e) {}
  if (/API_KEY|API Key|api[_ ]?key/i.test(msg) && /未配置|missing|not set/i.test(msg)) {
    return "未配置 API Key，请在智能助手页「模型与用量」中填写后保存";
  }
  if (/^(401|UNAUTHORIZED)$|未登录/.test(msg)) return "登录已过期，请刷新页面重新登录";
  if (/^(403|FORBIDDEN)$/.test(msg)) return "没有权限执行此操作";
  if (/^\d{3}$/.test(msg)) return "服务暂时不可用（HTTP " + msg + "），请稍后重试";
  if (/^\s*[{<]/.test(msg)) return "服务暂时不可用，请稍后重试";
  return msg || "服务暂时不可用，请稍后重试";
}
