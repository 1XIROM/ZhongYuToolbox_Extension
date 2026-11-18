/* ============================================================
 * 选项字母答案的可视化渲染（answer-view.js）
 *
 * 平台返回的「答案」有时只是选项字母（A / AB / ABCD / A、C），
 * 直接显示成一个孤立字母很难读。这里在**满足全部触发条件**时把它
 * 换成「徽标 + 选项原文」，否则返回 null，调用方照原样输出，行为不变。
 *
 * 触发条件（三条全中才替换）：
 *   1) 去掉标签与空白后，内容只由字母和分隔符组成；
 *   2) 去掉分隔符后是 1–6 个字母，且互不重复（A–H 范围内的选项字母）；
 *   3) 题干里能找到每个字母对应的选项标记（A. / A、/ A．/ A) / A：）
 *      —— 找不到说明这不是「选项字母」，可能只是英文单词或判断题答案；
 *   4) 字母按 A→H 升序（ABD / A、C 可以；BAD 这种倒序更像英文单词，不替换）。
 *
 * 不替换的情形举例：
 *   「A. 3.5 m/s²」（字母后跟正文）、「对」「错误」「略」、
 *   「见解析」「①②③」、含公式/图片的答案、超过 6 个字母。
 * ============================================================ */
(function () {
    'use strict';

    var STYLE_ID = 'answer-chips-style';

    function ensureStyle() {
        if (document.getElementById(STYLE_ID)) return;
        var css = ''
            + '.ans-chips{display:flex;align-items:center;gap:10px;flex-wrap:wrap;'
            + 'padding:10px 12px;border-radius:10px;background:rgba(120,194,173,.08);'
            + 'border:1px solid rgba(120,194,173,.35);}'
            + '.ans-chips-tag{font-size:12px;line-height:1;padding:4px 8px;border-radius:999px;'
            + 'background:var(--bs-primary,#78c2ad);color:#fff;letter-spacing:1px;flex:0 0 auto;}'
            + '.ans-chip{display:inline-flex;align-items:center;justify-content:center;'
            + 'width:34px;height:34px;border-radius:50%;'
            + 'font:700 17px/1 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;'
            + 'color:#fff;background:var(--bs-primary,#78c2ad);'
            + 'box-shadow:0 2px 6px rgba(0,0,0,.18);flex:0 0 auto;}'
            + '.ans-chip.is-multi{background:var(--bs-info,#6cc3d5);}'
            + '.ans-chip-detail{display:flex;flex-direction:column;gap:4px;min-width:0;}'
            + '.ans-chip-line{display:flex;align-items:center;gap:8px;min-width:0;font-size:14px;color:#495057;}'
            + '.ans-chip-line .ans-chip{width:26px;height:26px;font-size:14px;}'
            + '.ans-chip-text{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:46em;}'
            + '.ans-chips-note{font-size:12px;color:#6c757d;}'
            + '.qa-panel{padding:4px 2px;}'
            + '.qa-head{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:8px;}'
            + '.qa-title{font-weight:600;}'
            + '.qa-line{display:flex;flex-wrap:nowrap;gap:2px;font:14px/1.9 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;overflow-x:auto;}'
            + '.qa-item{white-space:nowrap;}'
            + '.qa-item b{color:#0d6efd;font-weight:700;margin-right:1px;}'
            + '.qa-sep{color:#adb5bd;}'
            + '.qa-tip{font-size:12px;color:#6c757d;margin-top:8px;}';
        var el = document.createElement('style');
        el.id = STYLE_ID;
        el.textContent = css;
        document.head.appendChild(el);
    }

    /** 取 HTML 的纯文本：块级标签先换成换行，再去标签（选项常被 <p> 切开，直接串会粘在一起） */
    function textOf(html) {
        var s = String(html == null ? '' : html)
            .replace(/<br\s*\/?>/gi, '\n')
            .replace(/<\/(p|div|li|tr|h[1-6]|td|th)>/gi, '\n');
        var div = document.createElement('div');
        div.innerHTML = s;
        return (div.textContent || '').replace(/[ \t\u00a0]+/g, ' ').trim();
    }

    /** 从题干里找出「字母 -> 选项正文」的映射 */
    function optionsOf(stemHTML) {
        var text = textOf(stemHTML);
        var map = {};

        // 1) 逐行匹配：选项各占一段时最常见
        text.split(/\n+/).forEach(function (line) {
            var m = line.match(/^\s*[（(]?([A-Ha-h])[）)]?\s*[.．、:：)）]\s*(.+)$/);
            if (m) {
                var k = m[1].toUpperCase();
                if (!map[k]) map[k] = m[2].trim();
            }
        });

        // 2) 整段挤成一行时，按「空格 + 字母 + 标点」再切一次
        if (!Object.keys(map).length) {
            var flat = text.replace(/\n+/g, ' ');
            var re = /[（(]?([A-Ha-h])[）)]?\s*[.．、:：)）]\s*([\s\S]*?)(?=(?:\s+[（(]?[A-Ha-h][）)]?\s*[.．、:：)）])|$)/g;
            var m2;
            while ((m2 = re.exec(flat)) !== null) {
                var kk = m2[1].toUpperCase();
                var vv = (m2[2] || '').trim();
                if (vv && !map[kk]) map[kk] = vv;
            }
        }
        return map;
    }

    /** 去掉「1. 」「（2）」「3、」这类题号前缀 */
    function stripNumPrefix(text) {
        return String(text == null ? '' : text)
            .replace(/^\s*[（(]?\d{1,3}[）)]?\s*[.、．:：)）]\s*/, '');
    }

    /** 答案的短文本：能归成选项字母就给字母，否则截断 */
    function answerShort(answerHTML) {
        var t = stripNumPrefix(textOf(answerHTML)).replace(/\s+/g, ' ').trim();
        if (!t) return '—';
        var solid = t.replace(/[\s、,，/\\|·.;；]+/g, '');
        if (/^[A-Ha-h]{1,6}$/.test(solid)) {
            var L = solid.toUpperCase();
            var uniq = {};
            for (var i = 0; i < L.length; i++) { if (uniq[L[i]]) break; uniq[L[i]] = 1; }
            if (i === L.length && L === L.split('').sort().join('')) return L;
        }
        return t.length > 12 ? t.slice(0, 12) + '…' : t;
    }

    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    /**
     * 若答案可判定为「选项字母」，返回可视化 HTML；否则返回 null。
     * @param {string} answerHTML 答案区的 HTML
     * @param {string} stemHTML   题干 HTML（校验字母确实是选项，并取选项原文）
     */
    window.answerShort = answerShort;

    /**
     * 快捷对答案：只显示「题号.答案」，每行 perLine 项，顿号分隔。
     * @param {Array<{number:number, answer:string}>} questions
     */
    window.answerQuickViewHTML = function (questions, perLine) {
        ensureStyle();
        perLine = perLine || 5;
        var items = (questions || []).map(function (q) {
            return '<span class="qa-item"><b>' + q.number + '</b>.' + esc(answerShort(q.answer)) + '</span>';
        });
        var lines = [];
        for (var i = 0; i < items.length; i += perLine) {
            lines.push('<div class="qa-line">' + items.slice(i, i + perLine).join('<span class="qa-sep">、</span>') + '</div>');
        }
        return lines.join('');
    };

    window.answerChipsHTML = function (answerHTML, stemHTML) {
        var raw = stripNumPrefix(textOf(answerHTML)).trim();
        if (!raw) return null;

        // 只允许字母 + 常见分隔符
        if (!/^[A-Ha-h\s、,，/\\|·.;；]+$/.test(raw)) return null;

        var letters = raw.replace(/[\s、,，/\\|·.;；]+/g, '').toUpperCase();
        if (!/^[A-H]{1,6}$/.test(letters)) return null;

        // 字母互不重复，且按 A→H 升序（答案惯例是 ABD / A、C；"BAD" 这种更像英文单词）
        var uniq = {};
        for (var i = 0; i < letters.length; i++) {
            if (uniq[letters[i]]) return null;
            uniq[letters[i]] = 1;
        }
        if (letters !== letters.split('').sort().join('')) return null;

        // 题干里有选项标记时，必须每个字母都对得上；
        // 无题干（isNoStem 答题卡类测评）时，只接受 1–4 个字母——「1. A」这种编号+字母已足够明确。
        var opts = optionsOf(stemHTML);
        var arr = letters.split('');
        if (Object.keys(opts).length) {
            for (var j = 0; j < arr.length; j++) {
                if (!opts[arr[j]]) return null;
            }
        } else if (arr.length > 4) {
            return null;
        }

        ensureStyle();

        var multi = arr.length > 1;
        var tag = multi ? '多选' : '单选';
        var head = '<span class="ans-chips-tag">' + tag + '</span>';

        if (!multi) {
            return '<div class="ans-chips" data-answer-letters="' + letters + '">'
                + head
                + '<span class="ans-chip">' + arr[0] + '</span>'
                + '<span class="ans-chip-text" title="' + esc(opts[arr[0]]) + '">' + esc(opts[arr[0]]) + '</span>'
                + '</div>';
        }

        var lines = arr.map(function (L) {
            return '<div class="ans-chip-line"><span class="ans-chip is-multi">' + L + '</span>'
                + '<span class="ans-chip-text" title="' + esc(opts[L]) + '">' + esc(opts[L]) + '</span></div>';
        }).join('');

        return '<div class="ans-chips" data-answer-letters="' + letters + '">'
            + head + '<div class="ans-chip-detail">' + lines + '</div>'
            + '<span class="ans-chips-note">共 ' + arr.length + ' 项</span>'
            + '</div>';
    };
})();
