/* 积分台 · 前端渲染
   ------------------------------------------------------------------
   设计约束（和仓库其它部分保持一致）：
   1. 零第三方依赖：图表是手绘 SVG，不引 Chart.js / ECharts / 任何 CDN。
   2. 数据靠 <script> 挂 window，不用 fetch —— 这样 file:// 双击打开也能看。
      唯一的例外是「一键刷新」，它只在我们自己的 server.py 下才出现。
   3. 任何一份数据缺失都只降级对应模块，绝不整页白屏。
   4. 颜色全部引用 style.css 的 CSS 变量，切深浅主题不用重绘图表。
   ------------------------------------------------------------------ */
(function () {
  'use strict';

  var D = {
    wb: window.WB_USAGE || null,
    trae: window.TRAE_USAGE || null,
    credits: window.CREDITS_ALERT || null
  };
  var $ = function (id) { return document.getElementById(id); };
  var esc = function (s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  };
  var nf = function (n, digits) {
    var d = digits == null ? 2 : digits;
    var v = Number(n || 0);
    return v.toLocaleString('zh-CN', { minimumFractionDigits: d, maximumFractionDigits: d });
  };
  var compact = function (n) {
    var v = Number(n || 0);
    if (Math.abs(v) >= 10000) return (v / 10000).toFixed(1) + '万';
    if (Math.abs(v) >= 1000) return (v / 1000).toFixed(1) + 'k';
    return String(Math.round(v));
  };
  var pad2 = function (n) { return (n < 10 ? '0' : '') + n; };
  var ymd = function (d) { return d.getFullYear() + '-' + pad2(d.getMonth() + 1) + '-' + pad2(d.getDate()); };
  var addDays = function (s, n) {
    // 固定用中午 12 点做日期运算，避开夏令时/时区把日期推偏一天
    var d = new Date(s + 'T12:00:00');
    d.setDate(d.getDate() + n);
    return ymd(d);
  };
  var dateList = function (start, end) {
    var out = [], cur = start, guard = 0;
    while (cur <= end && guard++ < 400) { out.push(cur); cur = addDays(cur, 1); }
    return out;
  };
  var LEVEL_TEXT = { urgent: '紧急', expired: '已过期', warn: '提醒', normal: '正常', unknown: '未知' };
  var LEVEL_RANK = { unknown: 0, normal: 1, warn: 2, urgent: 3, expired: 4 };
  var pill = function (cls, text) { return '<span class="pill ' + cls + '">' + esc(text) + '</span>'; };
  var emptyBox = function (msg) { return '<div class="empty">' + esc(msg) + '</div>'; };

  // ---------------------------------------------------------------- 数据准备
  var records = [];
  if (D.wb && Array.isArray(D.wb.records)) {
    D.wb.records.forEach(function (r) { records.push(Object.assign({}, r, { platform: 'WorkBuddy' })); });
  }
  if (D.trae && Array.isArray(D.trae.records)) {
    D.trae.records.forEach(function (r) { records.push(Object.assign({}, r, { platform: 'Trae' })); });
  }
  records.sort(function (a, b) { return (a.ts || 0) - (b.ts || 0); });
  var maxDate = records.length ? records[records.length - 1].date : ymd(new Date());
  var minDate = records.length ? records[0].date : maxDate;

  var state = { usageDays: 30, expiryDays: 14, tab: 'usage' };

  // ---------------------------------------------------------------- 主题
  var THEME_KEY = 'wb-credits-viewer-theme';
  function effectiveTheme() {
    var saved = null;
    try { saved = localStorage.getItem(THEME_KEY); } catch (e) { /* 隐私模式下可能被禁 */ }
    if (saved === 'dark' || saved === 'light') return saved;
    return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }
  function applyTheme() {
    var saved = null;
    try { saved = localStorage.getItem(THEME_KEY); } catch (e) {}
    var t = effectiveTheme();
    // 没手动选过时**不要**写 data-theme，让 CSS 的 prefers-color-scheme 真正生效；
    // 一旦点过按钮才锁死成显式值（记忆：跟随系统依赖浏览器口径，别自己抢着定死）。
    if (saved === 'dark' || saved === 'light') document.documentElement.setAttribute('data-theme', t);
    else document.documentElement.removeAttribute('data-theme');
    $('themeBtn').textContent = t === 'dark' ? '☀ 日间' : '◐ 夜间';
  }
  $('themeBtn').addEventListener('click', function () {
    try { localStorage.setItem(THEME_KEY, effectiveTheme() === 'dark' ? 'light' : 'dark'); } catch (e) {}
    applyTheme();
  });
  if (window.matchMedia) {
    var mq = window.matchMedia('(prefers-color-scheme: dark)');
    if (mq.addEventListener) mq.addEventListener('change', applyTheme);
  }

  // ---------------------------------------------------------------- 数据时间戳
  function renderStamp() {
    var parts = [['WB', D.wb], ['Trae', D.trae], ['预警', D.credits]];
    var html = [], stale = false;
    parts.forEach(function (p) {
      var t = p[1] && p[1].generatedAt;
      if (!t) { html.push(p[0] + ' <span class="stale">缺数据</span>'); stale = true; return; }
      var short = t.slice(5, 16);
      var dt = new Date(t.replace(' ', 'T'));
      var age = (Date.now() - dt.getTime()) / 3600000;
      var isStale = !isNaN(age) && age > 24;
      if (isStale) stale = true;
      html.push(p[0] + ' ' + (isStale ? '<span class="stale">' : '') + esc(short) + (isStale ? '</span>' : ''));
    });
    $('stamp').innerHTML = '<div>数据 ' + html.join(' · ') + '</div>' +
      (stale ? '<div>有数据源超过 24 小时，建议刷新</div>' : '<div>全部来自本机采集</div>');
  }

  // ---------------------------------------------------------------- 标签切换
  Array.prototype.forEach.call(document.querySelectorAll('.tab'), function (btn) {
    btn.addEventListener('click', function () {
      var name = btn.getAttribute('data-tab');
      state.tab = name;
      Array.prototype.forEach.call(document.querySelectorAll('.tab'), function (b) {
        b.classList.toggle('active', b === btn);
      });
      $('tab-usage').hidden = name !== 'usage';
      $('tab-alert').hidden = name !== 'alert';
    });
  });

  // ================================================================ 消耗看板
  function windowDays(days) {
    var start = days ? addDays(maxDate, -(days - 1)) : minDate;
    if (start < minDate && days) start = minDate;   // 数据不足 N 天时别画一堆空柱
    return dateList(start, maxDate);
  }
  function aggregate(list) {
    var map = {};
    list.forEach(function (d) { map[d] = { wb: 0, trae: 0, n: 0 }; });
    records.forEach(function (r) {
      if (!(r.date in map)) return;
      var c = Number(r.credit) || 0;
      if (r.platform === 'WorkBuddy') map[r.date].wb += c; else map[r.date].trae += c;
      map[r.date].n++;
    });
    return map;
  }

  function renderKPIs(list, agg) {
    var total = 0, wb = 0, trae = 0, n = 0;
    list.forEach(function (d) { total += agg[d].wb + agg[d].trae; wb += agg[d].wb; trae += agg[d].trae; n += agg[d].n; });
    var last = agg[maxDate] || { wb: 0, trae: 0 };
    var cr = D.credits && D.credits.summary;
    var soon = cr ? cr.expiringSoon : null;
    var urgent = cr ? cr.urgentRemain : null;
    var warnDays = (D.credits && D.credits.thresholds) ? D.credits.thresholds.warnDays : 7;
    var cards = [
      { label: '窗口内积分消耗', value: nf(total), unit: '分',
        tip: '共 ' + n + ' 轮 · WorkBuddy ' + nf(wb, 0) + ' / Trae ' + nf(trae, 0) },
      { label: '最近一日消耗', value: nf(last.wb + last.trae), unit: '分',
        tip: maxDate + '（WB ' + nf(last.wb) + ' / Trae ' + nf(last.trae) + '）' },
      { label: '当前剩余积分', value: cr ? nf(cr.totalRemain) : '—', unit: cr ? '分' : '',
        tip: cr ? '两家余额快照合计 · 采集于 ' + esc(D.credits.generatedAt.slice(5, 16)) : '运行 credits_alert.py 后显示' },
      { label: warnDays + ' 天内将到期', cls: (cr && cr.level === 'urgent') ? 'danger' : (cr && cr.level === 'warn') ? 'warn' : '',
        value: soon == null ? '—' : nf(soon), unit: soon == null ? '' : '分',
        tip: urgent == null ? '运行 credits_alert.py 后显示' : '其中 ' + nf(urgent) + ' 分在 3 天内到期' }
    ];
    $('kpis').innerHTML = cards.map(function (c) {
      return '<div class="kpi ' + (c.cls || '') + '">' +
        '<div class="label">' + esc(c.label) + '</div>' +
        '<div class="value">' + c.value + (c.unit ? '<small>' + c.unit + '</small>' : '') + '</div>' +
        '<div class="tip">' + c.tip + '</div></div>';
    }).join('');
  }

  function niceMax(v) {
    if (v <= 0) return 1;
    var p = Math.pow(10, Math.floor(Math.log10(v))), n = v / p;
    return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p;
  }
  function renderTrend(list, agg) {
    var box = $('trendChart');
    if (!list.length || !records.length) { box.innerHTML = emptyBox('暂无用量数据 · 先运行 workbuddy_fetch.py / trae_fetch.py'); return; }
    var W = 760, H = 236, L = 50, R = 10, T = 12, B = 34;
    var pw = W - L - R, ph = H - T - B;
    var totals = list.map(function (d) { return agg[d].wb + agg[d].trae; });
    var top = niceMax(Math.max.apply(null, totals));
    var step = pw / list.length;
    var bw = Math.max(2, Math.min(18, step * 0.66));
    var labelEvery = Math.ceil(list.length / 8);
    var s = ['<svg viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="每日积分消耗堆叠柱状图">'];

    // 横向网格 + Y 轴刻度（4 档）
    for (var i = 0; i <= 4; i++) {
      var y = T + ph - (ph * i / 4);
      s.push('<line class="gridline" x1="' + L + '" y1="' + y + '" x2="' + (W - R) + '" y2="' + y + '"/>');
      s.push('<text class="axis-label" x="' + (L - 7) + '" y="' + (y + 3.5) + '" text-anchor="end">' + compact(top * i / 4) + '</text>');
    }
    list.forEach(function (d, idx) {
      var a = agg[d], x = L + step * idx + (step - bw) / 2;
      var hWb = (a.wb / top) * ph, hTrae = (a.trae / top) * ph;
      var yWb = T + ph - hWb, yTrae = yWb - hTrae;
      var tip = d + ' · 合计 ' + nf(a.wb + a.trae) + ' 分（WorkBuddy ' + nf(a.wb) + ' / Trae ' + nf(a.trae) + '）· ' + a.n + ' 轮';
      if (hWb > 0) s.push('<rect class="bar" style="fill:var(--wb)" x="' + x + '" y="' + yWb + '" width="' + bw + '" height="' + hWb + '" rx="1"><title>' + esc(tip) + '</title></rect>');
      if (hTrae > 0) s.push('<rect class="bar" style="fill:var(--trae)" x="' + x + '" y="' + yTrae + '" width="' + bw + '" height="' + hTrae + '" rx="1"><title>' + esc(tip) + '</title></rect>');
      if (idx % labelEvery === 0 || idx === list.length - 1) {
        s.push('<text class="axis-label" x="' + (L + step * idx + step / 2) + '" y="' + (H - 12) + '" text-anchor="middle">' + esc(d.slice(5)) + '</text>');
      }
    });
    s.push('<line class="gridline" x1="' + L + '" y1="' + (T + ph) + '" x2="' + (W - R) + '" y2="' + (T + ph) + '"/>');
    s.push('</svg>');
    box.innerHTML = s.join('');
    var peak = list[totals.indexOf(Math.max.apply(null, totals))];
    $('trendNote').textContent = list[0] + ' ~ ' + list[list.length - 1] + ' · 峰值 ' + peak + '（' + nf(Math.max.apply(null, totals)) + ' 分）';
  }

  function renderCompare(list, agg) {
    // 先把窗口做成哈希集合：下面按记录统计次数时是 O(1) 查找，
    // 不用对每个日期反复 filter 全量 records（那会是 O(天数 × 记录数)）。
    var inWindow = {};
    list.forEach(function (d) { inWindow[d] = true; });
    var sum = { WorkBuddy: { v: 0, n: 0 }, Trae: { v: 0, n: 0 } };
    list.forEach(function (d) { sum.WorkBuddy.v += agg[d].wb; sum.Trae.v += agg[d].trae; });
    records.forEach(function (r) { if (inWindow[r.date]) sum[r.platform].n++; });
    var total = sum.WorkBuddy.v + sum.Trae.v || 1;
    var max = Math.max(sum.WorkBuddy.v, sum.Trae.v, 1);
    var html = [['WorkBuddy', 'wb', 'var(--wb)'], ['Trae', 'trae', 'var(--trae)']].map(function (p) {
      var o = sum[p[0]];
      return '<div class="compare-row">' +
        '<div class="compare-top"><span>' + pill(p[1], p[0]) + '</span><b>' + nf(o.v) + ' 分</b></div>' +
        '<div class="bar-track"><div class="bar-fill" style="width:' + (o.v / max * 100).toFixed(1) + '%;background:' + p[2] + '"></div></div>' +
        '<div class="compare-meta">占合计 ' + (o.v / total * 100).toFixed(1) + '% · ' + o.n + ' 轮 · 单次均值 ' +
        nf(o.n ? o.v / o.n : 0) + ' 分</div></div>';
    }).join('');
    $('compare').innerHTML = html || emptyBox('暂无用量数据');
  }

  function renderRank(elId, key, list) {
    var box = $(elId);
    var map = {}, inWindow = {};
    list.forEach(function (d) { inWindow[d] = true; });
    records.forEach(function (r) {
      if (!inWindow[r.date]) return;
      var k = r[key] || '未标注';
      map[k] = (map[k] || 0) + (Number(r.credit) || 0);
    });
    var arr = Object.keys(map).map(function (k) { return [k, map[k]]; })
      .sort(function (a, b) { return b[1] - a[1]; }).slice(0, 5);
    if (!arr.length) { box.innerHTML = emptyBox('暂无数据'); return; }
    var max = arr[0][1] || 1;
    box.innerHTML = arr.map(function (p) {
      return '<div class="rank-row"><div class="rank-name" title="' + esc(p[0]) + '">' + esc(p[0]) + '</div>' +
        '<div class="rank-bar"><i style="width:' + (p[1] / max * 100).toFixed(1) + '%"></i></div>' +
        '<div class="rank-val">' + nf(p[1]) + '</div></div>';
    }).join('');
  }

  function renderDetails() {
    var plat = $('detailPlatform').value, q = $('detailSearch').value.trim().toLowerCase();
    var rows = records.filter(function (r) {
      if (plat !== 'all' && r.platform !== plat) return false;
      if (!q) return true;
      return [r.session, r.model, r.project].join(' ').toLowerCase().indexOf(q) >= 0;
    }).sort(function (a, b) { return (b.ts || 0) - (a.ts || 0); });
    var shown = rows.slice(0, 80);
    $('detailCount').textContent = '共 ' + rows.length + ' 条' + (rows.length > 80 ? '，显示最近 80 条' : '');
    $('detailBody').innerHTML = shown.map(function (r) {
      return '<tr><td class="mono">' + esc(r.time) + '</td>' +
        '<td>' + pill(r.platform === 'WorkBuddy' ? 'wb' : 'trae', r.platform === 'WorkBuddy' ? 'WB' : 'Trae') + '</td>' +
        '<td><span class="clip" title="' + esc(r.session) + '">' + esc(r.session) + '</span></td>' +
        '<td>' + esc(r.model) + '</td>' +
        '<td><span class="clip" title="' + esc(r.project) + '">' + esc(r.project) + '</span></td>' +
        '<td class="num">' + nf(r.credit) + '</td></tr>';
    }).join('');
    $('detailEmpty').hidden = shown.length > 0;
  }

  function renderUsage() {
    var list = windowDays(state.usageDays), agg = aggregate(list);
    renderKPIs(list, agg);
    renderTrend(list, agg);
    renderCompare(list, agg);
    renderRank('topModels', 'model', list);
    renderRank('topProjects', 'project', list);
    renderDetails();
  }

  // ================================================================ 到期预警
  function renderBanner() {
    var box = $('alertBanner'), cr = D.credits;
    if (!cr) { box.className = 'banner'; box.innerHTML = '还没有积分预警数据 · 运行 <code>python credits_alert.py</code> 生成 <code>credits-data.js</code>。'; return; }
    var s = cr.summary, th = cr.thresholds || { warnDays: 7, urgentDays: 3 };
    if (s.level === 'urgent' || s.level === 'expired') {
      box.className = 'banner urgent';
      box.innerHTML = '<strong>⚠ 有积分快到期了</strong> —— ' + th.urgentDays + ' 天内 <b>' + nf(s.urgentRemain) +
        ' 分</b>待到期，' + th.warnDays + ' 天内共 <b>' + nf(s.expiringSoon) + ' 分</b>。' +
        '优先把下面清单里最靠上的几笔用掉，别让它们清零。';
    } else if (s.level === 'warn') {
      box.className = 'banner warn';
      box.innerHTML = '<strong>提醒</strong> —— ' + th.warnDays + ' 天内共有 <b>' + nf(s.expiringSoon) +
        ' 分</b>到期，暂时没有 ' + th.urgentDays + ' 天内的紧急批次。';
    } else {
      box.className = 'banner';
      box.innerHTML = '<strong>暂时安全</strong> —— ' + th.warnDays + ' 天内没有批次到期。' +
        '两家当前合计剩余 <b>' + nf(s.totalRemain) + ' 分</b>。';
    }
    // 某家本次取数失败、沿用上次数据时，横幅要明说，别让人把旧数当新数
    var stale = (cr.sources || []).filter(function (x) { return x.stale; });
    if (stale.length) {
      box.innerHTML += '<div class="stale-note">⚠ ' +
        stale.map(function (x) { return esc(x.platform); }).join('、') +
        ' 本次取数失败，以下是 ' + esc(stale[0].staleFrom || '上次') +
        ' 采集的数据（对应余额卡已标「上次数据」）。</div>';
    }
    var badge = $('alertBadge');
    if (s.alertCount) { badge.hidden = false; badge.textContent = s.alertCount; } else { badge.hidden = true; }
  }

  function renderBalances() {
    var box = $('balanceCards');
    if (!D.credits || !D.credits.sources || !D.credits.sources.length) {
      box.innerHTML = '<div class="card">' + emptyBox('暂无余额数据') + '</div>'; return;
    }
    box.innerHTML = D.credits.sources.map(function (s) {
      var tag = !s.ok ? pill('urgent', '取数失败')
        : s.stale ? pill('warn', '上次数据')
          : pill(s.level || 'unknown', LEVEL_TEXT[s.level] || s.level);
      var head = '<div class="balance-head"><h2>' + esc(s.platform) + '</h2>' + tag + '</div>';
      if (!s.ok) return '<div class="card balance">' + head + '<div class="balance-err">' + esc(s.error || '未知错误') + '</div></div>';
      var size = Number(s.totalSize) || 0, used = Number(s.totalUsed) || 0;
      var pct = size > 0 ? Math.min(100, used / size * 100) : 0;
      var soon = s.soonest;
      return '<div class="card balance">' + head +
        '<div class="balance-num">' + nf(s.totalRemain) + '<small>/ ' + nf(size) + ' 分</small></div>' +
        '<div class="balance-track"><div class="balance-used" style="width:' + pct.toFixed(1) + '%"></div></div>' +
        '<div class="balance-meta"><span>已用 ' + nf(used) + ' 分（' + pct.toFixed(1) + '%）</span>' +
        '<span>' + s.batchCount + ' 笔待到期</span></div>' +
        '<div class="balance-soon">最近一笔：<b>' + esc(soon ? soon.expireAt : '未知') + '</b>' +
        (soon ? '（' + (soon.daysLeft < 0 ? '已过期' : '还剩 ' + soon.daysLeft + ' 天') + '，' + nf(soon.remain) + ' 分）' : '') +
        '<br>' + esc(s.subtitle || '') + ' · 窗口内 <b>' + nf(s.expiringSoon) + '</b> 分到期' +
        (s.urgentRemain ? '，其中紧急 <b>' + nf(s.urgentRemain) + '</b> 分' : '') +
        (s.stale ? '<br><span class="stale-inline">本次失败：' +
          esc(String(s.staleReason || '').slice(0, 60)) + ' · 数据来自 ' +
          esc(s.staleFrom || '上次采集') + '</span>' : '') + '</div></div>';
    }).join('');
  }

  function renderExpiry(days) {
    var box = $('expiryChart');
    var cr = D.credits;
    if (!cr || !cr.sources) { box.innerHTML = emptyBox('暂无到期数据'); return; }
    var now = Date.now(), end = now + days * 86400000;
    var byDay = {};
    cr.sources.forEach(function (s) {
      if (!s.ok) return;
      (s.items || []).forEach(function (it) {
        var ms = it.expireMs;
        if (!ms || ms > end) return;
        var key = ms < now ? '已过期' : (it.expireAt || '').slice(0, 10);
        if (!key) return;
        if (!byDay[key]) byDay[key] = { v: 0, rank: 0 };
        byDay[key].v += Number(it.remain) || 0;
        byDay[key].rank = Math.max(byDay[key].rank, LEVEL_RANK[it.level] || 0);
      });
    });
    var keys = Object.keys(byDay).sort();
    if (!keys.length) { box.innerHTML = emptyBox('未来 ' + days + ' 天内没有待到期的批次'); $('expiryNote').textContent = ''; return; }
    var W = 760, H = 236, L = 50, R = 10, T = 12, B = 34;
    var pw = W - L - R, ph = H - T - B;
    var top = niceMax(Math.max.apply(null, keys.map(function (k) { return byDay[k].v; })));
    var step = pw / keys.length, bw = Math.max(5, Math.min(34, step * 0.62));
    var labelEvery = Math.ceil(keys.length / 8);
    var colors = ['var(--grid)', 'var(--wb)', 'var(--warn)', 'var(--danger)', 'var(--danger)'];
    var s = ['<svg viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="未来到期积分分布柱状图">'];
    for (var i = 0; i <= 4; i++) {
      var y = T + ph - ph * i / 4;
      s.push('<line class="gridline" x1="' + L + '" y1="' + y + '" x2="' + (W - R) + '" y2="' + y + '"/>');
      s.push('<text class="axis-label" x="' + (L - 7) + '" y="' + (y + 3.5) + '" text-anchor="end">' + compact(top * i / 4) + '</text>');
    }
    keys.forEach(function (k, idx) {
      var o = byDay[k], h = o.v / top * ph;
      var x = L + step * idx + (step - bw) / 2, y = T + ph - h;
      s.push('<rect class="bar" style="fill:' + colors[o.rank] + '" x="' + x + '" y="' + y + '" width="' + bw + '" height="' + h + '" rx="2">' +
        '<title>' + esc(k + ' · ' + nf(o.v) + ' 分') + '</title></rect>');
      if (idx % labelEvery === 0 || idx === keys.length - 1) {
        s.push('<text class="axis-label" x="' + (L + step * idx + step / 2) + '" y="' + (H - 12) + '" text-anchor="middle">' + esc(k.slice(5)) + '</text>');
      }
    });
    s.push('<line class="gridline" x1="' + L + '" y1="' + (T + ph) + '" x2="' + (W - R) + '" y2="' + (T + ph) + '"/></svg>');
    box.innerHTML = s.join('');
    $('expiryNote').textContent = keys.length + ' 个到期日 · 合计 ' + nf(keys.reduce(function (a, k) { return a + byDay[k].v; }, 0)) + ' 分';
  }

  function renderAlertList() {
    var cr = D.credits, level = $('alertLevel').value;
    var rows = ((cr && cr.alerts) || []).filter(function (a) {
      if (level === 'all') return true;
      if (level === 'urgent') return a.level === 'urgent' || a.level === 'expired';
      return a.level === 'warn';
    });
    $('alertCount').textContent = '共 ' + rows.length + ' 笔';
    $('alertBody').innerHTML = rows.map(function (a) {
      var d = a.daysLeft == null ? '—' : (a.daysLeft < 0 ? '已过期' : a.daysLeft + ' 天');
      return '<tr><td>' + pill(a.level, LEVEL_TEXT[a.level] || a.level) + '</td>' +
        '<td>' + pill(a.platform === 'WorkBuddy' ? 'wb' : 'trae', a.platform === 'WorkBuddy' ? 'WB' : 'Trae') + '</td>' +
        '<td><span class="clip" title="' + esc(a.name) + '">' + esc(a.name) + '</span></td>' +
        '<td>' + esc(a.kind) + '</td><td class="mono">' + esc(a.expireAt) + '</td>' +
        '<td class="num">' + nf(a.remain) + '</td><td class="num">' + d + '</td></tr>';
    }).join('');
    $('alertEmpty').hidden = rows.length > 0;
  }

  function renderBatchTable() {
    var cr = D.credits, plat = $('batchPlatform').value, q = $('batchSearch').value.trim().toLowerCase();
    var rows = [];
    ((cr && cr.sources) || []).forEach(function (s) {
      if (!s.ok || (plat !== 'all' && s.platform !== plat)) return;
      (s.items || []).forEach(function (it) { rows.push(Object.assign({}, it, { platform: s.platform })); });
    });
    rows = rows.filter(function (it) {
      return !q || [it.name, it.reason, it.kind].join(' ').toLowerCase().indexOf(q) >= 0;
    }).sort(function (a, b) { return (a.expireMs || 0) - (b.expireMs || 0); });
    var shown = rows.slice(0, 100);
    $('batchCount').textContent = '共 ' + rows.length + ' 笔' + (rows.length > 100 ? '，显示前 100 笔' : '');
    $('batchBody').innerHTML = shown.map(function (it) {
      return '<tr><td class="mono">' + esc(it.expireAt || '未知') + '</td>' +
        '<td>' + pill(it.level || 'unknown', LEVEL_TEXT[it.level] || it.level) + '</td>' +
        '<td>' + pill(it.platform === 'WorkBuddy' ? 'wb' : 'trae', it.platform === 'WorkBuddy' ? 'WB' : 'Trae') + '</td>' +
        '<td><span class="clip" title="' + esc(it.name) + '">' + esc(it.name) + '</span></td>' +
        '<td>' + esc(it.kind) + '</td><td class="num">' + nf(it.remain) + '</td>' +
        '<td class="num">' + nf(it.size) + '</td></tr>';
    }).join('');
    $('batchEmpty').hidden = shown.length > 0;
  }

  function renderAlert() {
    renderBanner();
    renderBalances();
    renderExpiry(state.expiryDays);
    renderAlertList();
    renderBatchTable();
  }

  // ================================================================ 交互绑定
  $('rangeSeg').addEventListener('click', function (e) {
    var b = e.target.closest('button[data-days]'); if (!b) return;
    state.usageDays = Number(b.getAttribute('data-days'));
    Array.prototype.forEach.call(this.querySelectorAll('button'), function (x) { x.classList.toggle('on', x === b); });
    renderUsage();
  });
  $('expirySeg').addEventListener('click', function (e) {
    var b = e.target.closest('button[data-days]'); if (!b) return;
    state.expiryDays = Number(b.getAttribute('data-days'));
    Array.prototype.forEach.call(this.querySelectorAll('button'), function (x) { x.classList.toggle('on', x === b); });
    renderExpiry(state.expiryDays);
  });
  ['detailPlatform', 'detailSearch'].forEach(function (id) {
    $(id).addEventListener(id === 'detailSearch' ? 'input' : 'change', renderDetails);
  });
  ['alertLevel'].forEach(function (id) { $(id).addEventListener('change', renderAlertList); });
  ['batchPlatform', 'batchSearch'].forEach(function (id) {
    $(id).addEventListener(id === 'batchSearch' ? 'input' : 'change', renderBatchTable);
  });

  // 一键刷新只在自己的 server.py 下可用；file:// 直接打开时给降级提示。
  if (location.protocol === 'file:') {
    $('refreshBtn').hidden = true;
    $('fileHint').hidden = false;
  } else {
    $('refreshBtn').addEventListener('click', function () {
      var btn = this;
      btn.disabled = true; btn.textContent = '采集中…';
      fetch('/api/refresh', { method: 'POST' }).then(function (r) { return r.json(); }).then(function (j) {
        if (!j.ok) throw new Error(j.error || '刷新失败');
        location.reload();
      }).catch(function (err) {
        btn.disabled = false; btn.textContent = '↻ 刷新数据';
        alert('刷新失败：' + err.message + '\n请确认是用「启动看板.bat」启动的本地服务。');
      });
    });
  }

  // ================================================================ 导出 / 打印
  $('pngBtn').addEventListener('click', function () {
    var btn = this;
    if (!window.CreditsReport) { alert('报表模块 report.js 没加载成功'); return; }
    btn.disabled = true; btn.textContent = '生成中…';
    var restore = function () { btn.disabled = false; btn.textContent = '⬇ 导出 PNG'; };
    try {
      // 传当前窗口天数：报表和屏幕上的 KPI/趋势用的是同一段时间
      var built = window.CreditsReport.build(D, { days: state.usageDays });
      window.CreditsReport.renderPNG(built.svg, built.width, built.height, 2)
        .then(function (url) {
          window.CreditsReport.download(url, '积分台报表_' + ymd(new Date()) + '.png');
          restore();
        })
        .catch(function (e) { restore(); alert('导出 PNG 失败：' + e.message); });
    } catch (e) { restore(); alert('生成报表失败：' + e.message); }
  });

  $('printBtn').addEventListener('click', function () { window.print(); });

  // 打印抬头在启动时就填好，beforeprint 再刷新一次时间。
  // 这样即使某些浏览器/无头打印流程不派发 beforeprint，PDF 也不会是空抬头。
  function updatePrintHead() {
    var t = function (o) { return o && o.generatedAt ? o.generatedAt : '缺数据'; };
    $('printHead').textContent = '积分台报表 · WorkBuddy & Trae ｜ 数据：WB ' + t(D.wb) +
      ' / Trae ' + t(D.trae) + ' / 预警 ' + t(D.credits) +
      ' ｜ 生成于 ' + new Date().toLocaleString('zh-CN');
  }
  // 深色模式下打印会印出一大片深色底（浪费墨也看不清）：打印前临时锁浅色，
  // 结束后交给 applyTheme() 按用户原来的偏好恢复（含「跟随系统」）。
  window.addEventListener('beforeprint', function () {
    document.documentElement.setAttribute('data-theme', 'light');
    updatePrintHead();
  });
  window.addEventListener('afterprint', applyTheme);

  // ---------------------------------------------------------------- 启动
  applyTheme();
  updatePrintHead();
  renderStamp();
  renderUsage();
  renderAlert();
})();
