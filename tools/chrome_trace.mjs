#!/usr/bin/env node
/**
 * Record a frame-by-frame trace of the real Chrome dino game.
 *
 * Chrome (headless, light mode) runs the offline error page under a fake
 * clock and a seeded Math.random, a scripted bot plays it, and every canvas
 * call (drawImage / clearRect / fill) of every frame is hashed. The Python
 * port replays the same inputs and must reproduce every hash.
 *
 *   node tools/chrome_trace.mjs --frames 20000 --seed 12345 --bot 7 --out trace.json
 *
 * Needs Chrome and Node 22+ (global WebSocket/fetch). Dev tool only.
 */
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const args = Object.fromEntries(process.argv.slice(2).reduce((acc, a, i, all) => {
  if (a.startsWith('--')) acc.push([a.slice(2), all[i + 1] && !all[i + 1].startsWith('--') ? all[i + 1] : 'true']);
  return acc;
}, []));
const FRAMES = Number(args.frames ?? 6000);
const SEED = Number(args.seed ?? 12345);
const BOT_SEED = Number(args.bot ?? 7);
const MISTAKE = Number(args.mistake ?? 0.03);
const OUT = args.out ?? 'trace.json';
const TA = Number(args.ta ?? 20), TB = Number(args.tb ?? 7.5), CHAOS = Number(args.chaos ?? 1);
const HIGH = Number(args.hs ?? 0);
const CHROME = args.chrome ?? process.env.CHROME ?? 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const KEEP = (args.keep ?? '-1:-1').split(':').map(Number);
const port = 9400 + Math.floor(Math.random() * 400);

const PAGE_SCRIPT = `(function () {
  const SEED = ${SEED}, BOT_SEED = ${BOT_SEED}, MISTAKE = ${MISTAKE}, TA = ${TA}, TB = ${TB}, CHAOS = ${CHAOS}, KEEP_FROM = ${KEEP[0]}, KEEP_TO = ${KEEP[1]};
  const DT = 1000 / 60;

  // ---- deterministic Math.random --------------------------------------
  function mulberry32(a) {
    return function () {
      a |= 0; a = (a + 0x6D2B79F5) | 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  const gameRand = mulberry32(SEED);
  window.__randCount = 0;
  Math.random = function () { window.__randCount++; return gameRand(); };

  // ---- fake clock + requestAnimationFrame -------------------------------
  let fakeNow = 1000;
  performance.now = function () { return fakeNow; };
  let pending = new Map(), batch = new Map(), nextId = 1;
  window.requestAnimationFrame = function (cb) { const id = nextId++; pending.set(id, cb); return id; };
  window.cancelAnimationFrame = function (id) { pending.delete(id); batch.delete(id); };

  // ---- canvas call log --------------------------------------------------
  const fmt = (n) => (Number.isNaN(n) ? 'NaN' : Number(n).toFixed(5));
  let log = [];
  const P = CanvasRenderingContext2D.prototype;
  const origDraw = P.drawImage, origClear = P.clearRect, origRect = P.rect, origFill = P.fill, origBegin = P.beginPath;
  let pathRects = [];
  P.drawImage = function (img, sx, sy, sw, sh, dx, dy, dw, dh) {
    const m = this.getTransform();
    log.push('d|' + [sx, sy, sw, sh, dx + m.e, dy + m.f, dw, dh, this.globalAlpha].map(fmt).join('|'));
    return origDraw.apply(this, arguments);
  };
  P.clearRect = function (x, y, w, h) {
    const m = this.getTransform();
    log.push('c|' + [x + m.e, y + m.f, w, h].map(fmt).join('|'));
    return origClear.apply(this, arguments);
  };
  P.beginPath = function () { pathRects = []; return origBegin.apply(this, arguments); };
  P.rect = function (x, y, w, h) { const m = this.getTransform(); pathRects.push([x + m.e, y + m.f, w, h]); return origRect.apply(this, arguments); };
  P.fill = function () {
    for (const r of pathRects) log.push('f|' + r.map(fmt).join('|'));
    return origFill.apply(this, arguments);
  };

  // ---- hashing ----------------------------------------------------------
  function fnv(str, seed) {
    let h = seed >>> 0;
    for (let i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = Math.imul(h, 16777619) >>> 0; }
    return h.toString(16).padStart(8, '0');
  }
  const hash2 = (s) => fnv(s, 2166136261) + fnv(s, 3266489917);

  // ---- state snapshot (for debugging a divergence) ----------------------
  function snapshot() {
    const r = Runner.getInstance();
    const t = r.tRex, h = r.horizon, nm = h.nightMode, dm = r.distanceMeter;
    const o = h.obstacles;
    return [
      fakeNow, r.distanceRan, r.currentSpeed, r.runningTime, r.time, r.invertTimer,
      +r.playing, +r.crashed, +r.paused, +r.activated, +r.playingIntro, +r.inverted,
      +document.documentElement.classList.contains('inverted'),
      t.xPos, t.yPos, t.jumpVelocity, t.status, +t.jumping, +t.ducking, +t.speedDrop, t.blinkCount, t.currentFrame, t.timer,
      o.length, o.length ? o[0].xPos : -1, o.length ? o[o.length - 1].xPos : -1, o.length ? o[0].size : 0,
      nm.opacity, nm.currentPhase, nm.xPos, h.clouds.length, h.horizonLines[0].xPos[0], h.horizonLines[0].xPos[1],
      dm.highScore.length, r.highestScore, window.__randCount,
    ].map(fmt).join('|');
  }

  // ---- fake CSS animation (intro) ----------------------------------------
  let animStart = null;
  function noteAnimation() {
    const r = Runner.getInstance();
    if (animStart === null && r.containerEl && r.containerEl.style.webkitAnimation) animStart = fakeNow;
  }

  function key(type, code) {
    document.dispatchEvent(new KeyboardEvent(type, { keyCode: code, bubbles: true }));
    noteAnimation();
  }

  function advance() {
    fakeNow += DT;
    const r = Runner.getInstance();
    if (animStart !== null && fakeNow >= animStart + 400) {
      animStart = null;
      r.containerEl.dispatchEvent(new Event('webkitAnimationEnd'));
    }
    batch = pending; pending = new Map();
    while (batch.size) {
      const id = batch.keys().next().value;
      const cb = batch.get(id); batch.delete(id);
      cb(fakeNow);
    }
    noteAnimation();
  }

  // ---- the bot ------------------------------------------------------------
  const botRand = mulberry32(BOT_SEED);
  const events = [];
  let releases = [];  // [frame, type, code]
  let crashFrame = -1, startFrame = 30 + Math.floor(botRand() * 60), duckUntil = -1, skipObstacle = null;
  function send(f, type, code) { events.push([f, type, code]); key(type, code); }
  function schedule(f, type, code) { releases.push([f, type, code]); }

  function bot(f) {
    const due = releases.filter((x) => x[0] <= f); releases = releases.filter((x) => x[0] > f);
    for (const [, type, code] of due) send(f, type, code);
    const r = Runner.getInstance();
    if (!r.playing && !r.crashed && !r.paused && !r.activated) {
      if (f === startFrame) { send(f, 'keydown', 32); schedule(f + 2 + Math.floor(botRand() * 20), 'keyup', 32); }
      return;
    }
    if (r.crashed) {
      if (crashFrame < 0) crashFrame = f;
      if (f - crashFrame === 25) { // too early: the game must ignore it
        send(f, 'keydown', 32); send(f, 'keyup', 32);
      }
      if (f - crashFrame === 90) {
        const useEnter = botRand() < 0.5;
        const code = useEnter ? 13 : (botRand() < 0.5 ? 32 : 38);
        send(f, 'keydown', code); send(f, 'keyup', code);
        crashFrame = -1; skipObstacle = null;
      }
      return;
    }
    crashFrame = -1;
    if (!r.playing) return;
    const t = r.tRex, h = r.horizon, ob = h.obstacles[0];
    const speed = r.currentSpeed;
    if (ob && ob !== skipObstacle) {
      const dx = ob.xPos - (t.xPos + 44);
      const ptero = ob.typeConfig.type === 'pterodactyl';
      if (!t.jumping && !t.ducking) {
        if (!ptero || ob.yPos === 100) {
          const trigger = TA + speed * (ptero ? TB * 0.85 : TB);
          if (dx < trigger && dx > -20) {
            if (botRand() < MISTAKE) { skipObstacle = ob; return; }
            send(f, 'keydown', botRand() < 0.5 ? 32 : 38);
            const hold = botRand() >= 0.2 * CHAOS ? 60 : 1 + Math.floor(botRand() * 25);
            schedule(f + hold, 'keyup', 32); // releases whichever key
            schedule(f + hold, 'keyup', 38);
          }
        } else if (ob.yPos === 75 && dx < 30 + speed * 12 && dx > -50) {
          if (duckUntil < f) { send(f, 'keydown', 40); duckUntil = f + 40; schedule(duckUntil, 'keyup', 40); }
        }
      } else if (t.jumping && botRand() < 0.01 * CHAOS) {
        send(f, 'keydown', 40); schedule(f + 3 + Math.floor(botRand() * 10), 'keyup', 40);   // speed drop
      }
    } else if (!t.jumping && !t.ducking && botRand() < 0.004 * CHAOS) {
      send(f, 'keydown', 40); schedule(f + 10 + Math.floor(botRand() * 40), 'keyup', 40);    // idle duck
    }
  }

  // ---- public API for the harness ---------------------------------------
  window.__trace = { frames: [], states: [], rand: [] };
  window.__events = events;
  window.__runFrames = function (from, count) {
    let f = from;
    for (; f < from + count; f++) {
      log = [];
      bot(f);
      advance();
      window.__trace.frames.push(hash2(log.join('\\n')) + ':' + log.length);
      window.__trace.states.push(hash2(snapshot()));
      window.__lastLog = log;
      if (f >= KEEP_FROM && f <= KEEP_TO) { (window.__detail = window.__detail || {})[f] = { log: log.slice(), state: snapshot() }; }
    }
    return f;
  };
  window.__initLog = null;
  window.__captureInit = function () { window.__initLog = log.slice(); window.__initHash = hash2(log.join('\\n')) + ':' + log.length; log = []; };
  window.__take = function () { const r = JSON.stringify(window.__trace); window.__trace = { frames: [], states: [], rand: [] }; return r; };
  window.__canvasPng = function () { return document.querySelector('canvas').toDataURL('image/png'); };

  // The browser calls initializeEasterEggHighScore() from native code at a
  // wall-clock moment. Swallow that and let the harness apply it at a known point.
  let realInit = null;
  Object.defineProperty(window, 'initializeEasterEggHighScore', {
    configurable: true,
    get() { return function () { /* ignored: applied explicitly below */ }; },
    set(fn) { realInit = fn; },
  });
  window.__initHigh = function (n) { realInit(n); };

  // CSS: no real animations/transitions (they run on wall-clock time)
  const style = () => { const s = document.createElement('style'); s.textContent = '*{animation:none!important;-webkit-animation:none!important;transition:none!important}.interstitial-wrapper{padding-left:0!important;padding-right:0!important;max-width:600px!important}'; document.head.appendChild(s); };
  if (document.head) style(); else document.addEventListener('DOMContentLoaded', style);
})();`;

// ---- minimal CDP client ---------------------------------------------------
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'dino-trace-'));
const proc = spawn(CHROME, ['--headless=new', '--disable-gpu', `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`, '--window-size=900,500', 'about:blank'], { stdio: 'ignore' });
let targets;
for (let i = 0; i < 80; i++) { try { targets = await (await fetch(`http://127.0.0.1:${port}/json`)).json(); if (targets.length) break; } catch { /* retry */ } await sleep(250); }
const ws = new WebSocket(targets.find((t) => t.type === 'page').webSocketDebuggerUrl);
await new Promise((r) => (ws.onopen = r));
let nextMsg = 0; const waiting = new Map();
ws.onmessage = (e) => { const m = JSON.parse(e.data); if (m.id && waiting.has(m.id)) { waiting.get(m.id)(m); waiting.delete(m.id); } };
const cdp = (method, params = {}) => new Promise((r) => { const id = ++nextMsg; waiting.set(id, r); ws.send(JSON.stringify({ id, method, params })); });
const evaluate = async (expression) => {
  const m = await cdp('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
  if (m.result.exceptionDetails) throw new Error(JSON.stringify(m.result.exceptionDetails).slice(0, 600));
  return m.result.result.value;
};

await cdp('Page.enable'); await cdp('Runtime.enable');
await cdp('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-color-scheme', value: 'light' }] });
await cdp('Page.addScriptToEvaluateOnNewDocument', { source: PAGE_SCRIPT });
// the draw log must start collecting before the game boots
await cdp('Page.navigate', { url: 'chrome://network-error/-106' });
for (let i = 0; i < 80; i++) { await sleep(100); try { if (await evaluate('!!(window.Runner && Runner.getInstance && (()=>{try{return Runner.getInstance().tRex}catch(e){return null}})())')) break; } catch { /* page not ready */ } }
await evaluate('window.__captureInit(), 1');
await evaluate(`window.__initHigh(${HIGH}), 1`);
const initInfo = JSON.parse(await evaluate('JSON.stringify({initHash: window.__initHash, initLen: (window.__initLog||[]).length, randCount: window.__randCount, init: window.__initLog, width: Runner.getInstance().dimensions.width, dpr: devicePixelRatio, alt: Runner.getInstance().isAltGameModeEnabled(), highScore: Runner.getInstance().distanceMeter.highScore})'));

const frames = []; const states = [];
let done = 0;
while (done < FRAMES) {
  const n = Math.min(500, FRAMES - done);
  await evaluate(`window.__runFrames(${done}, ${n}), 1`);
  const chunk = JSON.parse(await evaluate('window.__take()'));
  frames.push(...chunk.frames); states.push(...chunk.states);
  done += n;
}
const events = JSON.parse(await evaluate('JSON.stringify(window.__events)'));
const finalState = await evaluate('(()=>{const r=Runner.getInstance();return JSON.stringify({score:r.distanceMeter.getActualDistance(Math.ceil(r.distanceRan)),highest:r.highestScore,speed:r.currentSpeed,crashed:r.crashed})})()');

const detail = JSON.parse(await evaluate('JSON.stringify(window.__detail || {})'));
fs.writeFileSync(OUT, JSON.stringify({ detail, chrome: await (async () => (await (await fetch(`http://127.0.0.1:${port}/json/version`)).json()).Browser)(), seed: SEED, botSeed: BOT_SEED, mistake: MISTAKE, frames: FRAMES, highScore: HIGH, dt: 1000 / 60, start: 1000, init: initInfo, events, frameHashes: frames, stateHashes: states, final: JSON.parse(finalState) }));
console.log(`wrote ${OUT}: ${FRAMES} frames, ${events.length} input events, final ${finalState}, init draws ${initInfo.initLen}, rand@init ${initInfo.randCount}, width ${initInfo.width}`);
ws.close(); proc.kill();
try { fs.rmSync(profile, { recursive: true, force: true }); } catch { /* chrome may still hold files */ }
process.exit(0);
