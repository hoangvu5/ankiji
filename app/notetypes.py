"""Ready-made Anki note types (fields, card templates, styling) for the Setup guide.

The Japanese one is the author's own "Japanese Card Format" (word -> meaning, and
meaning -> handwrite the kanji). The handwriting card needs _kanjicanvas.js in Anki's
media folder (KanjiCanvas, MIT License, github.com/asdfjkl/kanjicanvas).
The Mandarin one is the same idea without handwriting (KanjiCanvas only knows Japanese kanji).
"""
import base64
import os

from .paths import STATIC_DIR

HANDWRITING_FILE = "_kanjicanvas.js"

# ---------- shared pieces ----------
WORD_FRONT = r"""<div id="word">{{Front}}</div>

<script>
(function () {
  var el = document.getElementById("word");
  if (!el) return;
  var m = (el.innerText || "").match(/^(.*?)\s*[（(](.*?)[）)]\s*$/);
  if (!m) return;

  var kanji = m[1].trim();
  var reading = m[2].trim();
  var showing = false;

  el.innerText = kanji;
  el.style.cursor = "pointer";

  el.onclick = function (e) {
    e.stopPropagation();
    showing = !showing;
    el.innerText = showing ? reading : kanji;
  };
})();
</script>"""

WORD_ANSWER = r"""<div class="jp" id="c1-word">{{Front}}</div>
<div class="hw-reading" id="c1-reading"></div>

<script>
(function () {
  var el = document.getElementById("c1-word");
  if (!el) return;
  var m = (el.innerText || "").match(/^(.*?)\s*[（(](.*?)[）)]\s*$/);
  if (m) {
    el.innerText = m[1].trim();
    document.getElementById("c1-reading").innerText = m[2].trim();
  }
})();
</script>"""

MEDIA_BLOCK = r"""{{#Media}}
<div class="nade-media">{{Media}}</div>
<script>
(function () {
  function tame(v) {
    if (v.getAttribute("data-tamed")) return;
    v.setAttribute("data-tamed", "1");
    v.controls = false;
    v.removeAttribute("controls");
    v.setAttribute("playsinline", "");
    v.onclick = function (e) {
      e.stopPropagation();
      v.currentTime = 0;
      v.play();
    };
  }
  function scan() {
    var vids = document.querySelectorAll("video");
    for (var i = 0; i < vids.length; i++) tame(vids[i]);
  }
  scan();
  var n = 0;
  var t = setInterval(function () { scan(); if (++n > 20) clearInterval(t); }, 250);
})();
</script>
{{/Media}}"""

# No media yet: "Get clip" runs the iPhone Shortcut "Nadeshiko Clip" (Setup guide, iPhone clips),
# "Browse examples" opens the word on Nadeshiko's website.
NADESHIKO_LINK = r"""{{^Media}}
<div id="nade-link"></div>
<div id="nade-word" style="display:none">{{text:Front}}</div>
<script>
(function () {
  var SHORTCUT = "Nadeshiko Clip";
  var w = (document.getElementById("nade-word").textContent || "")
    .replace(/\[[^\]]*\]/g, "").replace(/[（(][^)）]*[)）]/g, "").trim();
  if (!w) return;
  var box = document.getElementById("nade-link");
  function btn(text, href, cls) {
    var a = document.createElement("a");
    a.href = href;
    a.textContent = text;
    a.className = cls;
    a.addEventListener("click", function (e) { e.stopPropagation(); });
    box.appendChild(a);
  }
  btn("Get clip", "shortcuts://run-shortcut?name=" + encodeURIComponent(SHORTCUT) + "&input=text&text=" + encodeURIComponent(w), "nade-btn");
  btn("Browse", "https://nadeshiko.co/en/search/" + encodeURIComponent('"' + w + '"'), "nade-btn nade-alt");
})();
</script>
{{/Media}}"""

# ---------- Japanese ----------
JP_CARD2_FRONT = r"""<div class="en hw-prompt">{{Back}}</div>

<div id="hw-target" style="display:none">{{text:Front}}</div>

<div id="hw">
  <div id="hw-slots"></div>
  <div id="hw-padwrap">
    <canvas id="hw-pad"></canvas>
    <div id="hw-hint"></div>
  </div>
  <div id="hw-buttons">
    <button id="hw-undo" type="button" aria-label="Undo">
      <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M9 14L4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/></svg>
    </button>
    <button id="hw-clear" type="button" aria-label="Clear">
      <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6M14 11v6"/></svg>
    </button>
    <button id="hw-check" type="button">Skip</button>
  </div>
</div>

<script>
(function () {
  var LIB = "_kanjicanvas.js";

  var raw = document.getElementById("hw-target").textContent || "";
  raw = raw.replace(/\[[^\]]*\]/g, "")
           .replace(/[（(][^)）]*[)）]/g, "")
           .replace(/\s+/g, "");
  var chars = Array.from(raw);
  var isKanji = function (c) { return /[㐀-䶿一-鿿]/.test(c); };

  var slots = document.getElementById("hw-slots");
  var hint = document.getElementById("hw-hint");
  var canvas = document.getElementById("hw-pad");
  var checkBtn = document.getElementById("hw-check");
  var ctx = canvas.getContext("2d");

  var items = chars.map(function (c) { return { ch: c, status: "todo", got: "" }; });
  var current = -1, finished = false, timer = null;
  var strokes = [], stroke = null, size = 240;

  function renderSlots() {
    slots.innerHTML = "";
    items.forEach(function (it, i) {
      var s = document.createElement("span");
      s.className = "slot " + it.status + (i === current ? " current" : "");
      var hidden = it.status === "todo" || (it.status === "given" && !finished);
      s.textContent = hidden ? "" : it.ch;
      slots.appendChild(s);
    });
  }

  function updateBtn() {
    checkBtn.textContent = finished ? "Retry" : (strokes.length || stroke ? "Check" : "Skip");
  }

  function setupCanvas() {
    var w = Math.min(document.getElementById("hw").parentNode.clientWidth || 320, 360);
    size = Math.round(Math.max(176, w * 0.8));
    var dpr = window.devicePixelRatio || 1;
    canvas.style.width = canvas.style.height = size + "px";
    canvas.width = canvas.height = Math.round(size * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    document.getElementById("hw").style.width = size + "px";
    hint.style.fontSize = Math.round(size * 0.7) + "px";
    redraw();
  }

  function inkColor() {
    var night = document.body.classList.contains("nightMode") ||
                document.documentElement.classList.contains("night_mode") ||
                document.body.classList.contains("night_mode");
    return night ? "#f2f2f2" : "#1d1d1f";
  }

  function redraw() {
    ctx.clearRect(0, 0, size, size);
    ctx.lineCap = "round"; ctx.lineJoin = "round";
    ctx.lineWidth = Math.max(4, size / 45);
    ctx.strokeStyle = inkColor();
    strokes.concat(stroke ? [stroke] : []).forEach(function (st) {
      ctx.beginPath();
      st.forEach(function (p, i) { i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1]); });
      if (st.length === 1) ctx.lineTo(st[0][0] + 0.1, st[0][1]);
      ctx.stroke();
    });
  }

  function pos(e) {
    var r = canvas.getBoundingClientRect();
    var t = e.touches ? e.touches[0] : e;
    return [t.clientX - r.left, t.clientY - r.top];
  }
  function down(e) { e.preventDefault(); e.stopPropagation(); if (current < 0) return; stroke = [pos(e)]; redraw(); }
  function move(e) { e.preventDefault(); e.stopPropagation(); if (!stroke) return; stroke.push(pos(e)); redraw(); }
  function up(e)   { e.preventDefault(); e.stopPropagation(); if (!stroke) return; strokes.push(stroke); stroke = null; redraw(); updateBtn(); }

  canvas.addEventListener("touchstart", down, { passive: false });
  canvas.addEventListener("touchmove", move, { passive: false });
  canvas.addEventListener("touchend", up, { passive: false });
  canvas.addEventListener("touchcancel", up, { passive: false });
  canvas.addEventListener("mousedown", down);
  canvas.addEventListener("mousemove", function (e) { if (stroke) move(e); });
  window.addEventListener("mouseup", function (e) { if (stroke) up(e); });
  ["hw-padwrap", "hw-buttons"].forEach(function (id) {
    document.getElementById(id).addEventListener("click", function (e) { e.stopPropagation(); });
  });

  function recognize() {
    var KC = window.KanjiCanvas;
    var k = 256 / size;
    KC["recordedPattern_hwpad"] = strokes.map(function (st) {
      return st.map(function (p) { return [p[0] * k, p[1] * k]; });
    });
    var normalized = KC.momentNormalize("hwpad");
    var features = KC.extractFeatures(normalized, 20);
    var coarse = KC.coarseClassification(features);
    var out = KC.fineClassification(features, coarse) || "";
    return out.trim().split(/\s+/).filter(Boolean);
  }

  function nextChar() {
    timer = null;
    strokes = []; stroke = null; redraw(); hint.textContent = "";
    current = -1;
    for (var i = 0; i < items.length; i++) {
      if (items[i].status === "todo") { current = i; break; }
    }
    renderSlots();
    if (current < 0) finish();
    updateBtn();
  }

  function check() {
    if (finished) return retry();
    if (current < 0 || timer) return;
    var it = items[current];
    if (!strokes.length) {
      it.status = "wrong"; it.got = "(skipped)"; hint.textContent = it.ch;
      renderSlots();
      timer = setTimeout(nextChar, 1000);
      return;
    }
    var cands = recognize();
    var rank = cands.indexOf(it.ch);
    it.got = cands[0] || "?";
    if (rank >= 0 && rank <= 4) it.status = "ok";
    else { it.status = "wrong"; hint.textContent = it.ch; }
    renderSlots();
    timer = setTimeout(nextChar, it.status === "ok" ? 400 : 1400);
  }

  function finish() {
    finished = true;
    renderSlots();
    var quizzed = items.filter(function (x) { return x.status !== "given"; });
    var result = { key: raw,
                   got: quizzed.map(function (x) { return x.ch + ":" + x.got + ":" + x.status; }).join("|") };
    window.__hwResult = result;
    try { sessionStorage.setItem("hwResult", JSON.stringify(result)); } catch (e) {}
  }

  function retry() {
    finished = false;
    items.forEach(function (it) { if (it.status !== "given") { it.status = "todo"; it.got = ""; } });
    window.__hwResult = null;
    try { sessionStorage.removeItem("hwResult"); } catch (e) {}
    nextChar();
  }

  function start() {
    var known = new Set(window.KanjiCanvas.refPatterns.map(function (p) { return p[0]; }));
    items.forEach(function (it) {
      if (!isKanji(it.ch) || !known.has(it.ch)) it.status = "given";
    });
    window.__hwResult = null;
    try { sessionStorage.removeItem("hwResult"); } catch (e) {}
    if (!items.some(function (x) { return x.status === "todo"; })) {
      checkBtn.disabled = true;
      renderSlots();
      return;
    }
    nextChar();
  }

  document.getElementById("hw-undo").onclick = function () { strokes.pop(); redraw(); updateBtn(); };
  document.getElementById("hw-clear").onclick = function () { strokes = []; redraw(); updateBtn(); };
  checkBtn.onclick = check;

  function ready() { return window.KanjiCanvas && window.KanjiCanvas.refPatterns && window.KanjiCanvas.refPatterns.length > 100; }
  setupCanvas();
  renderSlots();
  if (ready()) { start(); return; }
  window.__hwWaiters = window.__hwWaiters || [];
  window.__hwWaiters.push(start);
  if (!window.__hwLoading) {
    window.__hwLoading = true;
    var s = document.createElement("script");
    s.src = LIB;
    s.onload = function () {
      var w = window.__hwWaiters || []; window.__hwWaiters = [];
      w.forEach(function (f) { try { f(); } catch (e) {} });
    };
    s.onerror = function () {
      window.__hwLoading = false;
      checkBtn.disabled = true;
      checkBtn.textContent = "Not loaded";
    };
    document.head.appendChild(s);
  }
})();
</script>"""

JP_CARD2_BACK = r"""<div class="en hw-prompt">{{Back}}</div>

<hr id="answer">

<div class="jp" id="hw-answer">{{Front}}</div>
<div class="hw-reading" id="hw-reading"></div>
<div class="hw-detail" id="hw-detail"></div>

<div id="hw-key" style="display:none">{{text:Front}}</div>

<script>
(function () {
  var ans = document.getElementById("hw-answer");
  var m = (ans.innerText || "").match(/^(.*?)\s*[（(](.*?)[）)]\s*$/);
  if (m) {
    ans.innerText = m[1].trim();
    document.getElementById("hw-reading").innerText = m[2].trim();
  }

  var key = (document.getElementById("hw-key").textContent || "")
    .replace(/\[[^\]]*\]/g, "").replace(/[（(][^)）]*[)）]/g, "").replace(/\s+/g, "");
  var r = window.__hwResult;
  if (!r) { try { r = JSON.parse(sessionStorage.getItem("hwResult") || "null"); } catch (e) {} }
  if (!r || r.key !== key) return;
  var detail = (r.got || "").split("|").filter(Boolean).map(function (s) {
    var p = s.split(":");
    return p[2] === "ok" ? "" : p[1] + " → " + p[0];
  }).filter(Boolean);
  if (detail.length) {
    document.getElementById("hw-detail").textContent = "You wrote: " + detail.join("   ");
  }
})();
</script>

""" + MEDIA_BLOCK + "\n" + NADESHIKO_LINK

JP_CSS = r""".card {
    font-family: arial;
    font-size: 20px;
    line-height: 1.5;
    text-align: center;
    color: black;
    background-color: white;
}

#word, .jp { font-size: 40px; line-height: 1.3; }
.en { font-size: 24px; line-height: 1.3; }
.hw-reading { font-size: 20px; opacity: .75; margin-top: 2px; }
.hw-detail { font-size: 16px; opacity: .8; margin-top: 8px; }

.hw-prompt { margin: 4px 0 10px; }

#hw { margin: 0 auto; box-sizing: border-box;
      -webkit-user-select: none; user-select: none; -webkit-touch-callout: none; }

#hw-slots { display: flex; justify-content: center; gap: 6px; flex-wrap: wrap; margin-bottom: 8px; min-height: 40px; }
#hw-slots .slot { width: 36px; height: 36px; border-radius: 8px; font-size: 26px; line-height: 36px;
                  border: 2px solid rgba(128,128,128,.35); box-sizing: border-box; }
#hw-slots .slot.current { border-color: #3478f6; }
#hw-slots .slot.given   { border-style: dashed; opacity: .7; }
#hw-slots .slot.ok      { border-color: #2e9e55; color: #2e9e55; }
#hw-slots .slot.close   { border-color: #d39600; color: #d39600; }
#hw-slots .slot.wrong   { border-color: #d64545; color: #d64545; }

#hw-padwrap { position: relative; display: inline-block; touch-action: none; }
#hw-pad { display: block; touch-action: none; border-radius: 14px;
          background: rgba(128,128,128,.08); border: 1px solid rgba(128,128,128,.3);
          background-image:
            linear-gradient(rgba(128,128,128,.18), rgba(128,128,128,.18)),
            linear-gradient(90deg, rgba(128,128,128,.18), rgba(128,128,128,.18));
          background-size: 100% 1px, 1px 100%; background-position: center; background-repeat: no-repeat; }
#hw-hint { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
           color: rgba(214,69,69,.25); pointer-events: none; }

#hw-buttons { display: flex; gap: 8px; margin-top: 10px; }
#hw-buttons button { display: flex; align-items: center; justify-content: center;
                     height: 48px; padding: 0; border-radius: 12px; border: none;
                     font-size: 17px; font-weight: 600; }
#hw-buttons #hw-undo  { flex: 1; background: #f5c400; color: #3a2c00; }
#hw-buttons #hw-clear { flex: 1; background: #d64545; color: #fff; }
#hw-buttons #hw-check { flex: 2; background: #3478f6; color: #fff; }
#hw-buttons button:disabled { opacity: .4; }


.nade-media { margin-top: 12px; }
.nade-btn { display: inline-block; margin-top: 14px; padding: 8px 16px; border-radius: 10px;
            background: #e8507a; color: #fff !important; text-decoration: none; font-size: 16px; }
.nade-alt { background: transparent !important; color: #e8507a !important; border: 1px solid #e8507a; margin-left: 8px; }


video::-webkit-media-controls { display: none !important; }
video::-webkit-media-controls-enclosure { display: none !important; }
"""

# ---------- Mandarin ----------
ZH_CARD2_FRONT = r"""<div class="en hw-prompt">{{Back}}</div>
<div class="ask">Viết chữ Hán / Write the characters</div>"""

ZH_CARD2_BACK = r"""<div class="en hw-prompt">{{Back}}</div>

<hr id="answer">

""" + WORD_ANSWER.replace("c1-word", "c2-word").replace("c1-reading", "c2-reading") + "\n\n" + \
    "{{#Media}}\n<div class=\"nade-media\">{{Media}}</div>\n{{/Media}}"

ZH_CSS = r""".card {
    font-family: arial, "Microsoft YaHei", "PingFang SC", "Noto Sans SC", sans-serif;
    font-size: 20px;
    line-height: 1.5;
    text-align: center;
    color: black;
    background-color: white;
}

#word, .jp { font-size: 44px; line-height: 1.3; }
.en { font-size: 22px; line-height: 1.4; }
.hw-reading { font-size: 22px; opacity: .75; margin-top: 2px; }
.hw-prompt { margin: 4px 0 10px; }
.ask { font-size: 15px; opacity: .6; margin-top: 10px; }
.nade-media { margin-top: 12px; }
"""

NOTE_TYPES = {
    "japanese": {
        "name": "Japanese Card Format",
        "fields": ["Front", "Back", "Media"],
        "templates": [
            {"name": "Card 1", "front": WORD_FRONT,
             "back": WORD_ANSWER + "\n\n<hr id=answer>\n\n<div class=\"en\">{{Back}}</div>\n\n" + MEDIA_BLOCK + "\n" + NADESHIKO_LINK},
            {"name": "Card 2", "front": JP_CARD2_FRONT, "back": JP_CARD2_BACK},
        ],
        "css": JP_CSS,
        "handwriting": True,
    },
    "mandarin": {
        "name": "Mandarin Card Format",
        "fields": ["Front", "Back", "Media"],
        "templates": [
            {"name": "Card 1", "front": WORD_FRONT,
             "back": WORD_ANSWER + "\n\n<hr id=answer>\n\n<div class=\"en\">{{Back}}</div>\n\n" +
                     "{{#Media}}\n<div class=\"nade-media\">{{Media}}</div>\n{{/Media}}"},
            {"name": "Card 2", "front": ZH_CARD2_FRONT, "back": ZH_CARD2_BACK},
        ],
        "css": ZH_CSS,
        "handwriting": False,
    },
}


def handwriting_path():
    return os.path.join(STATIC_DIR, HANDWRITING_FILE)


def public():
    return {k: {kk: vv for kk, vv in v.items()} for k, v in NOTE_TYPES.items()}


def create(kind, invoke):
    """Create the note type in Anki through AnkiConnect. Returns a short message."""
    nt = NOTE_TYPES[kind]
    if nt["name"] in invoke("modelNames"):
        msg = f'"{nt["name"]}" is already in Anki, so nothing was changed.'
    else:
        invoke("createModel", modelName=nt["name"], inOrderFields=nt["fields"], css=nt["css"], isCloze=False,
               cardTemplates=[{"Name": t["name"], "Front": t["front"], "Back": t["back"]} for t in nt["templates"]])
        msg = f'Created "{nt["name"]}" in Anki.'
    if nt["handwriting"]:
        with open(handwriting_path(), "rb") as f:
            invoke("storeMediaFile", filename=HANDWRITING_FILE, data=base64.b64encode(f.read()).decode())
        msg += " The handwriting file is in Anki's media folder."
    return msg
