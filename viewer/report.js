/* 积分台 · 报表导出（PNG / 打印 PDF 的数据底稿）
   ------------------------------------------------------------------
   为什么要单独一个文件：
   1. 「导出 PNG」不引 html2canvas / dom-to-image 之类第三方库（仓库零依赖约定）。
      做法是把关键数据**重新画成一张 SVG 报表**，再用浏览器原生的
      Image + canvas 把 SVG 光栅化成 PNG。这样导出的是一份排版好的报表，
      而不是把网页截图下来（网页截图会带上按钮、筛选框、滚动条）。
   2. SVG 里必须用**写死的浅色值**，不能用 style.css 的 CSS 变量：
      报表脱离文档上下文后 var(--wb) 解析不出来，会变成黑色。
      同时也保证深色模式下导出的报表仍是白底、适合打印/转发。
   3. 本模块自己算一份聚合，不依赖 app.js 的内部状态 —— 便于单独测试，
      也避免页面切了窗口后报表和界面口径不一致（build 时传入同一个 days）。

   对外接口（挂在 window.CreditsReport）：
     build(D, {days})        -> {svg, width, height}
     renderPNG(svg, w, h, s) -> Promise<dataURL>   s = 像素倍率，默认 2
     download(dataURL, name) -> 触发浏览器下载
   ------------------------------------------------------------------ */
(function () {
  'use strict';

  // 报表固定配色（浅色）
  var C = {
    bg: '#f4f7fc', card: '#ffffff', border: '#e3e9f3',
    text: '#18213a', text2: '#41506b', muted: '#7c889e',
    wb: '#4f6ef7', trae: '#f0913f', danger: '#e0453f',
    warn: '#dd8a12', ok: '#2a9a63', grid: '#eaf0f8'
  };
  var W = 1200, H = 1560;
  var FONT = 'Microsoft YaHei, PingFang SC, Segoe UI, sans-serif';

  var esc = function (s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  };
  var nf = function (n, d) {
    var v = Number(n || 0);
    return v.toLocaleString('zh-CN', {
      minimumFractionDigits: d == null ? 2 : d,
      maximumFractionDigits: d == null ? 2 : d
    });
  };
  var clip = function (s, n) {
    var t = String(s == null ? '' : s);
    return t.length > n ? t.slice(0, n - 1) + '…' : t;
  };
  var pad2 = function (n) { return (n < 10 ? '0' : '') + n; };
  var ymd = function (d) { return d.getFullYear() + '-' + pad2(d.getMonth() + 1) + '-' + pad2(d.getDate()); };
  var addDays = function (s, n) {
    var d = new Date(s + 'T12:00:00'); d.setDate(d.getDate() + n); return ymd(d);
  };
  var dateList = function (start, end) {
    var out = [], cur = start, guard = 0;
    while (cur <= end && guard++ < 400) { out.push(cur); cur = addDays(cur, 1); }
    return out;
  };
  var niceMax = function (v) {
    if (v <= 0) return 1;
    var p = Math.pow(10, Math.floor(Math.log10(v))), n = v / p;
    return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p;
  };

  // ---- SVG 小工具：所有文本都过 esc，避免会话标题里的 < & 破坏 XML ----
  function T(x, y, s, size, color, extra) {
    return '<text x="' + x + '" y="' + y + '" font-size="' + size + '" fill="' + color +
      '" font-family="' + FONT + '" ' + (extra || '') + '>' + esc(s) + '</text>';
  }
  function R(x, y, w, h, fill, r, stroke) {
    return '<rect x="' + x + '" y="' + y + '" width="' + w + '" height="' + h +
      '" rx="' + (r || 0) + '" fill="' + fill + '"' +
      (stroke ? ' stroke="' + stroke + '" stroke-width="1"' : '') + '/>';
  }
  function L(x1, y1, x2, y2, color, width) {
    return '<line x1="' + x1 + '" y1="' + y1 + '" x2="' + x2 + '" y2="' + y2 +
      '" stroke="' + color + '" stroke-width="' + (width || 1) + '"/>';
  }

  function build(D, opts) {
    opts = opts || {};
    var days = opts.days || 30;
    var D2 = D || {};
    var cr = D2.credits || null;
    var now = new Date();
    var stamp = now.getFullYear() + '-' + pad2(now.getMonth() + 1) + '-' + pad2(now.getDate()) +
      ' ' + pad2(now.getHours()) + ':' + pad2(now.getMinutes());

    // ---- 合并两家的逐轮记录，按当前窗口聚合 ----
    var recs = [];
    ((D2.wb && D2.wb.records) || []).forEach(function (r) {
      recs.push({ date: r.date, credit: Number(r.credit) || 0, p: 'wb' });
    });
    ((D2.trae && D2.trae.records) || []).forEach(function (r) {
      recs.push({ date: r.date, credit: Number(r.credit) || 0, p: 'trae' });
    });
    var dates = recs.map(function (r) { return r.date; }).sort();
    var maxDate = dates.length ? dates[dates.length - 1] : ymd(now);
    var minDate = dates.length ? dates[0] : maxDate;
    var start = days ? addDays(maxDate, -(days - 1)) : minDate;
    if (days && start < minDate) start = minDate;
    var list = dateList(start, maxDate);
    var agg = {}, inWin = {};
    list.forEach(function (d) { agg[d] = { wb: 0, trae: 0, n: 0 }; inWin[d] = true; });
    recs.forEach(function (r) {
      if (!inWin[r.date]) return;
      agg[r.date][r.p] += r.credit;
      agg[r.date].n++;
    });
    var total = 0, sumWb = 0, sumTrae = 0, rounds = 0;
    list.forEach(function (d) {
      total += agg[d].wb + agg[d].trae;
      sumWb += agg[d].wb; sumTrae += agg[d].trae; rounds += agg[d].n;
    });
    var last = agg[maxDate] || { wb: 0, trae: 0 };
    var summ = cr && cr.summary ? cr.summary : null;

    var s = [];
    s.push('<svg xmlns="http://www.w3.org/2000/svg" width="' + W + '" height="' + H +
      '" viewBox="0 0 ' + W + ' ' + H + '" font-family="' + FONT + '">');
    s.push(R(0, 0, W, H, C.bg, 0));

    // ---------------- 页眉 ----------------
    s.push(T(56, 66, '积分台 · WorkBuddy & Trae', 34, C.text, 'font-weight="700"'));
    s.push(T(56, 98, '积分消耗与到期预警报表', 16, C.text2));
    s.push(T(1144, 62, '生成于 ' + stamp, 14, C.muted, 'text-anchor="end"'));
    // 整串交给 T() 统一 esc，别自己先转义一次再拼（会变成双重转义）
    var dlRaw = '数据 ' +
      (D2.wb ? String(D2.wb.generatedAt || '').slice(5, 16) : '—') + ' / ' +
      (D2.trae ? String(D2.trae.generatedAt || '').slice(5, 16) : '—') + ' / ' +
      (cr ? String(cr.generatedAt || '').slice(5, 16) : '—');
    s.push(T(1144, 88, dlRaw, 13, C.muted, 'text-anchor="end"'));
    s.push(L(56, 120, 1144, 120, C.border, 1));

    // ---------------- KPI 四卡 ----------------
    var kpis = [
      { label: '窗口内积分消耗', value: nf(total), unit: '分',
        tip: '共 ' + rounds + ' 轮 · 近 ' + list.length + ' 天' },
      { label: '最近一日消耗', value: nf(last.wb + last.trae), unit: '分',
        tip: maxDate.slice(5) + ' · WB ' + nf(last.wb, 1) + ' / Trae ' + nf(last.trae, 1) },
      { label: '当前剩余积分', value: summ ? nf(summ.totalRemain) : '—', unit: summ ? '分' : '',
        tip: summ ? '两家余额快照合计' : '运行 credits_alert.py 后显示' },
      { label: ((cr && cr.thresholds) ? cr.thresholds.warnDays : 7) + ' 天内将到期',
        value: summ ? nf(summ.expiringSoon) : '—', unit: summ ? '分' : '',
        tip: summ ? '其中紧急 ' + nf(summ.urgentRemain) + ' 分' : '运行 credits_alert.py 后显示' }
    ];
    kpis.forEach(function (k, i) {
      var x = 56 + i * 284;
      s.push(R(x, 142, 268, 118, C.card, 12, C.border));
      s.push(T(x + 18, 172, k.label, 13, C.muted));
      // value 和单位放同一个 text，用 tspan 让 SVG 自己排版，
      // 比按字符数 × 字号估算 x 偏移准得多（中英文/千分位宽度都不一样）。
      s.push('<text x="' + (x + 18) + '" y="214" font-size="30" font-weight="700" fill="' +
        ((i === 3 && summ) ? C.danger : C.text) + '" font-family="' + FONT + '">' +
        esc(k.value) +
        (k.unit ? '<tspan font-size="12" font-weight="400" fill="' + C.muted + '" dx="6">' +
          esc(k.unit) + '</tspan>' : '') + '</text>');
      s.push(T(x + 18, 242, clip(k.tip, 30), 11, C.muted));
    });

    // ---------------- 每日消耗趋势 ----------------
    var cy = 282, ch = 356;
    s.push(R(56, cy, 1088, ch, C.card, 14, C.border));
    s.push(T(80, cy + 36, '每日积分消耗（' + (days ? '近 ' + list.length + ' 天' : '全部') + '）', 18, C.text, 'font-weight="700"'));
    s.push(T(80, cy + 58, '堆叠柱：下层 WorkBuddy、上层 Trae · 按逐轮 credit 求和', 11, C.muted));
    var gL = 108, gR = 1116, gT = cy + 78, gB = cy + 292;
    var gw = gR - gL, gh = gB - gT;
    var totals = list.map(function (d) { return agg[d].wb + agg[d].trae; });
    var top = niceMax(Math.max.apply(null, totals.length ? totals : [1]));
    for (var i = 0; i <= 4; i++) {
      var yy = gB - gh * i / 4;
      s.push(L(gL, yy, gR, yy, C.grid, 1));
      s.push(T(gL - 10, yy + 4, nf(top * i / 4, 0), 10, C.muted, 'text-anchor="end"'));
    }
    var step = gw / (list.length || 1);
    var bw = Math.max(2, Math.min(20, step * 0.66));
    var labelEvery = Math.ceil((list.length || 1) / 10);
    list.forEach(function (d, idx) {
      var a = agg[d], x = gL + step * idx + (step - bw) / 2;
      var hWb = a.wb / top * gh, hTrae = a.trae / top * gh;
      if (hWb > 0) s.push(R(x, gB - hWb, bw, hWb, C.wb, 1));
      if (hTrae > 0) s.push(R(x, gB - hWb - hTrae, bw, hTrae, C.trae, 1));
      if (idx % labelEvery === 0 || idx === list.length - 1) {
        s.push(T(gL + step * idx + step / 2, gB + 20, d.slice(5), 10, C.muted, 'text-anchor="middle"'));
      }
    });
    s.push(L(gL, gB, gR, gB, C.border, 1));
    s.push(R(80, cy + 316, 11, 11, C.wb, 3));
    s.push(T(98, cy + 326, 'WorkBuddy', 11, C.text2));
    s.push(R(196, cy + 316, 11, 11, C.trae, 3));
    s.push(T(214, cy + 326, 'Trae', 11, C.text2));
    var peak = Math.max.apply(null, totals.length ? totals : [0]);
    s.push(T(1116, cy + 326, '峰值 ' + nf(peak) + ' 分 · 窗口合计 ' + nf(total) + ' 分', 11, C.muted, 'text-anchor="end"'));

    // ---------------- 两家对比 ----------------
    var py = 656, ph = 196;
    s.push(R(56, py, 1088, ph, C.card, 14, C.border));
    s.push(T(80, py + 36, '两家对比', 18, C.text, 'font-weight="700"'));
    var cmp = [
      { name: 'WorkBuddy', v: sumWb, n: recs.filter(function (r) { return inWin[r.date] && r.p === 'wb'; }).length, color: C.wb },
      { name: 'Trae', v: sumTrae, n: recs.filter(function (r) { return inWin[r.date] && r.p === 'trae'; }).length, color: C.trae }
    ];
    var mx = Math.max(sumWb, sumTrae, 1), tt = sumWb + sumTrae || 1;
    cmp.forEach(function (o, i) {
      var yy = py + 66 + i * 62;
      s.push(T(80, yy + 4, o.name, 13, C.text2));
      s.push(T(1116, yy + 4, nf(o.v) + ' 分', 15, C.text, 'text-anchor="end" font-weight="700"'));
      s.push(R(80, yy + 14, 1036, 10, C.grid, 5));
      s.push(R(80, yy + 14, Math.max(2, o.v / mx * 1036), 10, o.color, 5));
      s.push(T(80, yy + 42, '占合计 ' + (o.v / tt * 100).toFixed(1) + '% · ' + o.n + ' 轮 · 单次均值 ' +
        nf(o.n ? o.v / o.n : 0) + ' 分', 11, C.muted));
    });

    // ---------------- 到期预警 ----------------
    var ay = 870, ah = 546;
    s.push(R(56, ay, 1088, ah, C.card, 14, C.border));
    s.push(T(80, ay + 36, '积分到期预警', 18, C.text, 'font-weight="700"'));
    if (summ) {
      s.push(T(1116, ay + 36, '当前 ' + (summ.level === 'urgent' ? '紧急' : summ.level === 'warn' ? '提醒' : '正常') +
        ' · ' + summ.alertCount + ' 笔待关注', 12, summ.level === 'urgent' ? C.danger : C.muted, 'text-anchor="end"'));
    }
    var srcs = (cr && cr.sources) || [];
    srcs.forEach(function (src, i) {
      var x = 80 + i * 528, y = ay + 58;
      s.push(R(x, y, 504, 118, C.bg, 10, C.border));
      s.push(T(x + 16, y + 28, src.platform, 14, C.text, 'font-weight="700"'));
      if (src.ok) {
        s.push(T(x + 488, y + 28, (src.level === 'urgent' ? '紧急' : src.level === 'warn' ? '提醒' : '正常'),
          11, src.level === 'urgent' ? C.danger : C.muted, 'text-anchor="end"'));
        s.push('<text x="' + (x + 16) + '" y="' + (y + 62) +
          '" font-size="26" font-weight="700" fill="' + C.text + '" font-family="' + FONT + '">' +
          esc(nf(src.totalRemain)) +
          '<tspan font-size="12" font-weight="400" fill="' + C.muted + '" dx="6">/ ' +
          esc(nf(src.totalSize)) + ' 分</tspan></text>');
        // 报表里画「剩余比例」而不是「已用比例」：余额卡的语义就是还剩多少，
        // 而且已用常常只有 2%，画出来只剩一个小点，看着像渲染坏了。
        var pct = src.totalSize ? Math.min(100, src.totalRemain / src.totalSize * 100) : 0;
        var soonTxt = (src.soonest && src.soonest.expireAt) ?
          String(src.soonest.expireAt).slice(5, 16) : '未知';
        s.push(R(x + 16, y + 76, 472, 8, C.grid, 4));
        s.push(R(x + 16, y + 76, Math.max(2, pct / 100 * 472), 8, C.wb, 4));
        s.push(T(x + 16, y + 104, src.batchCount + ' 笔待到期 · 剩余 ' + pct.toFixed(1) +
          '% · 最近 ' + soonTxt, 11, C.muted));
      } else {
        s.push(T(x + 16, y + 62, '取数失败：' + clip(src.error || '未知错误', 24), 12, C.danger));
      }
    });

    // 最急批次清单（最多 8 行）
    var ty = ay + 196;
    s.push(T(80, ty, '最需要关注的批次（按到期时间）', 13, C.text2, 'font-weight="700"'));
    s.push(L(80, ty + 12, 1116, ty + 12, C.border, 1));
    // 最后一列的数据是右对齐到 1116 的，表头必须同列同锚点，否则会错位
    var cols = [90, 200, 490, 800, 1116];
    ['等级', '平台', '包名 / 类型', '到期时间', '剩余'].forEach(function (h, i) {
      s.push(T(cols[i], ty + 36, h, 11, C.muted, i === 4 ? 'text-anchor="end"' : ''));
    });
    var alerts = (cr && cr.alerts) || [];
    if (!alerts.length) {
      s.push(T(80, ty + 68, '窗口内没有需要关注的批次', 12, C.muted));
    }
    alerts.slice(0, 8).forEach(function (a, i) {
      var yy = ty + 62 + i * 32;
      var color = (a.level === 'urgent' || a.level === 'expired') ? C.danger :
        (a.level === 'warn' ? C.warn : C.wb);
      var lname = a.level === 'expired' ? '已过期' : a.level === 'urgent' ? '紧急' : a.level === 'warn' ? '提醒' : '正常';
      s.push(R(80, yy - 12, 48, 20, color, 6));
      s.push(T(104, yy + 2, lname, 10, '#ffffff', 'text-anchor="middle"'));
      s.push(T(cols[1], yy + 2, a.platform === 'WorkBuddy' ? 'WB' : 'Trae', 11, C.text2));
      s.push(T(cols[2], yy + 2, clip(a.name, 22) + ' · ' + clip(a.kind, 8), 11, C.text));
      s.push(T(cols[3], yy + 2, String(a.expireAt || '未知').slice(5), 11, C.text2));
      s.push(T(1116, yy + 2, nf(a.remain) + ' 分', 11, C.text, 'text-anchor="end"'));
      s.push(L(80, yy + 12, 1116, yy + 12, C.grid, 1));
    });

    // ---------------- 页脚 ----------------
    var fy = 1438;
    s.push(L(56, fy, 1144, fy, C.border, 1));
    s.push(T(56, fy + 28, '口径说明', 13, C.text, 'font-weight="700"'));
    s.push(T(56, fy + 52, '· WorkBuddy 积分取本地账本 credit_json（漏记轮次由运行日志兜底），不按 token 反推；', 11, C.text2));
    s.push(T(56, fy + 72, '· Trae 积分取官方账单接口 credits_float，套餐额度是另一套口径；两家的「一分」不等价，合计只看趋势；', 11, C.text2));
    s.push(T(56, fy + 92, '· 「已花多少」是消耗流水，「还剩多少」是余额快照，两本账不必互相加减。', 11, C.text2));
    s.push(T(1144, fy + 92, '由 wb-usage-fetchers 生成 · 数据仅在本机 · 未上传', 11, C.muted, 'text-anchor="end"'));
    s.push('</svg>');

    return { svg: s.join(''), width: W, height: H };
  }

  function renderPNG(svg, w, h, scale) {
    scale = scale || 2;
    return new Promise(function (resolve, reject) {
      var img = new Image();
      // 用 data URL 而非 blob：不含任何外部资源，canvas 不会被污染，toDataURL 可正常导出。
      var url = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg);
      img.onload = function () {
        try {
          var c = document.createElement('canvas');
          c.width = w * scale; c.height = h * scale;
          var ctx = c.getContext('2d');
          ctx.setTransform(scale, 0, 0, scale, 0, 0);
          ctx.fillStyle = C.bg;
          ctx.fillRect(0, 0, w, h);
          ctx.drawImage(img, 0, 0, w, h);
          resolve(c.toDataURL('image/png'));
        } catch (e) { reject(e); }
      };
      img.onerror = function () { reject(new Error('SVG 光栅化失败：请确认浏览器允许 data: SVG')); };
      img.src = url;
    });
  }

  function download(dataURL, filename) {
    var a = document.createElement('a');
    a.href = dataURL;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
  }

  window.CreditsReport = { build: build, renderPNG: renderPNG, download: download, W: W, H: H };
})();
