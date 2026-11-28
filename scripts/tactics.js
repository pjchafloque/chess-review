#!/usr/bin/env node
// Tags the tactic behind each of the user's costly moves, for the Insights page's "Tactics you miss most".
// A costly move is one of his moves that lost 10 or more points of winning chance (an inaccuracy or worse on
// Game review). For each one it stores two pattern names: what Stockfish's better move had (the tactic he
// missed) and what his move let the opponent do (the tactic he walked into). Either can be empty.
//
// It runs the site's own explanation code, cut out of index.html between these markers, so the page and this
// file always say the same thing:
//   "// Move grades, in the order" ... "// This page's own opening lines"   (grade names)
//   "/* ---------- PGN parsing" ... "function parseGame"                    (reading games)
//   "/* ---------- evaluation maths" ... "/* ---------- analysis queue"      (grades and explanations)
//   "const PIECE_VAL" ... "async function refutationFor"                   (line helpers)
// If one of those comments is renamed in index.html, rename it here too.
//
// It also counts the grades of all his moves in each game (c: Brilliant, Great, Miss, Blunder, Mistake,
// Inaccuracy, the order of COUNTED below and of GCOUNT in index.html), for the games list's filters on phones
// ("games with my Brilliants"). Brilliant and Great need the board replayed, too slow to do for every game on
// a phone (version 2, 2026-11-27).
//
// Input: data/games.json, data/analysis.json. Output: data/tactics.json. Only games whose review changed
// since the last run are redone (a fingerprint per game), so a scheduled run takes a few seconds.
// Usage: node scripts/tactics.js        (CHESS_USER defaults to chaflox)
"use strict";
const fs = require("fs"), path = require("path"), vm = require("vm");
const ROOT = path.join(__dirname, "..");
const VERSION = 2;
const COUNTED = ["brilliant", "great", "miss", "blunder", "mistake", "inacc"];
const USER = (process.env.CHESS_USER || "chaflox").toLowerCase();
const { Chess } = require("./vendor/chess.js");

function pageCode() {
  const html = fs.readFileSync(path.join(ROOT, "index.html"), "utf8");
  const script = html.slice(html.indexOf('"use strict";'), html.lastIndexOf("</script>"));
  const cut = (a, b) => {
    const i = script.indexOf(a); if (i < 0) throw new Error("index.html has no " + JSON.stringify(a));
    const j = script.indexOf(b, i + a.length); if (j < 0) throw new Error("index.html has no " + JSON.stringify(b));
    return script.slice(i, j);
  };
  return [
    cut("// Move grades, in the order", "// This page's own opening lines"),
    cut("/* ---------- PGN parsing", "function parseGame"),
    'const moverAt = (g, i) => ((g.start === "w") === (i % 2 === 0)) ? "w" : "b";',
    cut("/* ---------- evaluation maths", "/* ---------- analysis queue"),
    cut("const PIECE_VAL", "async function refutationFor"),
    'function sideName(c) { return c === "w" ? "White" : "Black"; }',
    'function pvFor(g, i, kind) { const a = S.analyses[g.id]; const src = a && (kind === "ref" ? a.ref : a.pv); return src && src[i] && src[i].length ? src[i] : null; }'
  ].join("\n");
}

function fnv(str) { let h = 2166136261; for (let i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = Math.imul(h, 16777619); } return (h >>> 0).toString(36); }

function main() {
  const ctx = { Chess, console, Math, JSON, Object, Array, Map, Set, String, Number, Promise, Error, RegExp };
  ctx.S = { analyses: {}, games: [], user: USER, depth: 14 };
  ctx.esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  ctx.engine = { ready: false };
  ctx.store = { get: (k, d) => d, set() {} };
  vm.createContext(ctx);
  vm.runInContext(pageCode() + "\n;globalThis.__x = { lexGame, replay, expandEval, gradeAt, epFor, moverAt, explainMove, moveTactics, GRADE_INACC };", ctx, { filename: "index.html" });
  const x = ctx.__x;

  const games = JSON.parse(fs.readFileSync(path.join(ROOT, "data/games.json"), "utf8")).games || [];
  const an = JSON.parse(fs.readFileSync(path.join(ROOT, "data/analysis.json"), "utf8"));
  const OUT = path.join(ROOT, "data/tactics.json");
  let old = { v: VERSION, games: {} };
  try { const o = JSON.parse(fs.readFileSync(OUT, "utf8")); if (o && o.v === VERSION && o.games) old = o; } catch (e) {}

  const out = { v: VERSION, games: {} };
  let kept = 0, done = 0, moves = 0, errors = 0;
  const t0 = Date.now();
  for (const e of games) {
    const a = an[e.id];
    if (!a || !a.e || (a.v || 1) < 2) continue;           // only Stockfish 19 reviews (they have the lines)
    const fp = fnv(JSON.stringify([VERSION, a.v, a.d, a.b, a.e, a.p, a.r]));
    if (old.games[e.id] && old.games[e.id].f === fp) { out.games[e.id] = old.games[e.id]; kept++; continue; }
    let g;
    try { g = x.lexGame(e.pgn); } catch (err) { continue; }
    const h = g.headers, white = (h.White || "").toLowerCase(), black = (h.Black || "").toLowerCase();
    const me = white === USER ? "w" : black === USER ? "b" : null;
    if (!me) continue;
    x.replay(g);
    Object.defineProperty(g, "moves", { get() { return this._moves; } });
    Object.defineProperty(g, "fens", { get() { return this._fens; } });
    Object.assign(g, { id: e.id, me, start: h.FEN ? h.FEN.split(" ")[1] : "w", nply: g.tokens.length, white: h.White || "White", black: h.Black || "Black" });
    const ev = a.e.map(x.expandEval);
    ctx.S.analyses[e.id] = { v: a.v, depth: a.d, evals: ev, pv: a.p || null, ref: a.r || null, book: a.b != null ? a.b : null, bookNames: a.bn || null };
    const m = [], c = COUNTED.map(() => 0);
    for (let i = 0; i < Math.min(g.nply, g._moves.length); i++) {
      if (x.moverAt(g, i) !== me || !ev[i] || !ev[i + 1]) continue;
      let r;
      try { r = x.gradeAt(g, ev, i); } catch (err) { errors++; if (errors <= 3) console.error("error", e.id, i, err.message); continue; }
      const k = r ? COUNTED.indexOf(r.cls) : -1; if (k >= 0) c[k]++;
      if (x.epFor(ev[i], me) - x.epFor(ev[i + 1], me) < x.GRADE_INACC) continue;
      try {
        const o = x.explainMove(g, i, r, ev, a.r && a.r[i], a.p && a.p[i]);
        const [missed, allowed] = x.moveTactics(o);
        m.push([i, missed || 0, allowed || 0]); moves++;
      } catch (err) { errors++; if (errors <= 3) console.error("error", e.id, i, err.message); }
    }
    out.games[e.id] = { f: fp, m, c };
    delete ctx.S.analyses[e.id];
    done++;
  }
  fs.writeFileSync(OUT, JSON.stringify(out));
  console.log("tactics.json: " + Object.keys(out.games).length + " games (" + done + " worked out, " + kept + " unchanged), " + moves + " moves tagged, " + errors + " errors, " + ((Date.now() - t0) / 1000).toFixed(1) + " s");
}
main();
