"""Build the openings book that the Openings tab trains (data/repertoire.json), the way Chessbook does.

The book is a graph of positions, one per position (transpositions share a node):

  * At your turn the book holds one move, your repertoire move. Your move counts as 100%.
  * At your opponent's turn it holds the replies people actually play, with how often they play them:
    Lichess rapid games between players rated about 1300-1800 (scripts/lichess_sample.py), blended
    with the moves your own chess.com opponents played (data/games.json).
  * A position's incidence is the product of the opponent probabilities on the way to it, summed over
    transpositions: "you reach this position in about 1 of every N games of this opening". The book
    keeps every position down to 1 in MIN_GAMES (500); the page cuts it at the coverage goal you pick.

Which move is yours, in this order (the first one that Stockfish 19 rates within TOL pawns of its best):
  1. the move you usually play there (your games, recent games count double),
  2. the move this book already had there (so a rebuild doesn't swap a move you've learned),
  3. the move most players at your level choose (Lichess sample),
  4. Stockfish's best move.
Other moves within TOL of the best are listed as "ok": sound, but not your book move (a soft fail).

Usage: STOCKFISH=path python scripts/repertoire.py --sample lichess-sample.json [--min-games 500]
"""
import argparse
import csv
import io
import json
import math
import os
import re
import sys
import time
from collections import Counter, defaultdict

import chess
import chess.engine

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data")
OUT_FILE = os.environ.get("BOOK_OUT", os.path.join(DATA, "repertoire.json"))
CACHE_FILE = os.environ.get("BOOK_CACHE", os.path.join(DATA, "repertoire-evals.json"))
NOTES_FILE = os.path.join(HERE, "repertoire-notes.tsv")
BOOK_FILE = os.path.join(HERE, "openings.tsv")
USER = os.environ.get("CHESS_USER", "chaflox").lower()

SIDES = {
    "b": {"title": "Caro-Kann", "color": chess.BLACK, "start": ["e4", "c6"]},
    "w": {"title": "1.d4", "color": chess.WHITE, "start": ["d4"]},
}
TOL = 35            # centipawns: a move this close to Stockfish's best is sound
DEPTH = int(os.environ.get("BOOK_DEPTH", "18"))   # Stockfish 19, MultiPV 4
MULTIPV = 4
MAX_PLY = 30        # never deeper than move 15
ALPHA = 0.4         # how much your own opponents count next to Lichess (at most)
PUNISH_GAIN = 100   # an opponent reply that hands you a pawn or more...
PUNISH_MIN = 80     # ...and leaves you at +0.8 or better is marked as a mistake to punish
GOALS = [50, 100, 200, 500]


def epd(board):
    return " ".join(board.fen().split(" ")[:4])


def fnv(s):
    """Same 32-bit FNV-1a hash (base 36) as the page."""
    h = 2166136261
    raw = s.encode("utf-16-le")
    for i in range(0, len(raw), 2):
        h ^= raw[i] | (raw[i + 1] << 8)
        h = (h * 16777619) & 0xFFFFFFFF
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    out = ""
    while h:
        h, r = divmod(h, 36)
        out = digits[r] + out
    return out or "0"


def card_id(e, san):
    return fnv(e + " " + san.rstrip("+#"))


# ---------- theory names (scripts/openings.tsv) ----------
def load_book():
    book = {}
    with open(BOOK_FILE, encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            b = chess.Board()
            try:
                for tok in row["pgn"].split():
                    if not tok[0].isdigit():
                        b.push_san(tok)
                        book.setdefault(epd(b), "")
            except ValueError:
                continue
            book[epd(b)] = row["name"]
    return book


# ---------- Lichess sample: move sequences -> counts per position ----------
def lichess_stats(sample, side):
    s = sample[side]
    b0 = chess.Board()
    for san in SIDES[side]["start"]:
        b0.push_san(san)
    root = epd(b0)
    stats = defaultdict(Counter)
    seq_epd = {"": (root, b0.fen())}
    keys = sorted(s["counts"], key=lambda k: k.count(" "))
    for k in keys:
        parent, _, san = k.rpartition(" ")
        if parent not in seq_epd:
            continue
        pe, pfen = seq_epd[parent]
        b = chess.Board(pfen)
        try:
            mv = b.parse_san(san)
        except ValueError:
            continue
        stats[pe][b.san(mv)] += s["counts"][k]
        b.push(mv)
        seq_epd[k] = (epd(b), b.fen())
    return root, stats


# ---------- your games ----------
def tokens(pgn):
    mv = pgn.split("\n\n", 1)[1] if "\n\n" in pgn else pgn
    mv = re.sub(r"\{[^}]*\}", " ", mv)
    mv = re.sub(r"\d+\.(\.\.)?", " ", mv)
    return [t for t in mv.split() if t not in ("1-0", "0-1", "1/2-1/2", "*")]


class Moves(defaultdict):
    """Your own moves per position: weighted counts (recent games double) plus raw counts in .raw."""
    def __init__(self):
        super().__init__(Counter)
        self.raw = defaultdict(Counter)


def my_games(side):
    """Opponent replies (G) and your own moves (H, recent games count double) per position."""
    games = json.load(open(os.path.join(DATA, "games.json")))["games"]
    start = SIDES[side]["start"]
    G, H, seen = defaultdict(Counter), Moves(), Counter()
    dated = []
    for g in games:
        p = g["pgn"]
        h = dict(re.findall(r'\[(\w+) "([^"]*)"\]', p))
        if h.get("FEN"):
            continue
        me = "w" if h.get("White", "").lower() == USER else "b"
        if me != side:
            continue
        t = tokens(p)
        if t[:len(start)] != start:
            continue
        dated.append((h.get("UTCDate", ""), t))
    if not dated:
        return G, H, seen
    last = max(d for d, _ in dated)
    ly, lm, ld = (int(x) for x in last.split("."))
    for d, t in dated:
        y, m, dd = (int(x) for x in d.split("."))
        recent = (ly - y) * 365 + (lm - m) * 30 + (ld - dd) <= 60
        b = chess.Board()
        for i, san in enumerate(t[:MAX_PLY]):
            e = epd(b)
            seen[e] += 1
            try:
                mv = b.parse_san(san)
            except ValueError:
                break
            s = b.san(mv)
            if b.turn == SIDES[side]["color"]:
                H[e][s] += 2 if recent else 1
                H.raw[e][s] += 1
            else:
                G[e][s] += 1
            b.push(mv)
    return G, H, seen


# ---------- Stockfish ----------
class Engine:
    def __init__(self, path, cache):
        self.cache = cache
        self.path = path
        self.eng = None
        self.calls = 0

    def open(self):
        if not self.eng:
            self.eng = chess.engine.SimpleEngine.popen_uci(self.path)
            self.eng.configure({"Threads": max(1, os.cpu_count() or 1), "Hash": 256})
        return self.eng

    @staticmethod
    def cp(score, turn):
        s = score.pov(turn)
        if s.is_mate():
            m = s.mate()
            return 10000 - abs(m) if m > 0 else -10000 + abs(m)
        return s.score()

    def lines(self, board):
        """{san: centipawns from the side to move} for the top MULTIPV moves."""
        e = epd(board)
        hit = self.cache.get(e)
        if hit and hit.get("d", 0) >= DEPTH and "pv" in hit:
            return hit["pv"]
        infos = self.open().analyse(board, chess.engine.Limit(depth=DEPTH), multipv=MULTIPV)
        self.calls += 1
        out = {}
        for info in infos:
            if "pv" in info and info["pv"]:
                out[board.san(info["pv"][0])] = self.cp(info["score"], board.turn)
        self.cache[e] = {"d": DEPTH, "pv": out, "x": (hit or {}).get("x", {})}
        if self.calls % 40 == 0:
            self.save()
        return out

    def save(self):
        tmp = CACHE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.cache, f, separators=(",", ":"), sort_keys=True)
        os.replace(tmp, CACHE_FILE)

    def move(self, board, san):
        """Centipawns (side to move) after playing san, searched on its own when not in the top lines."""
        pv = self.lines(board)
        if san in pv:
            return pv[san]
        e = epd(board)
        x = self.cache[e].setdefault("x", {})
        if san in x:
            return x[san]
        mv = board.parse_san(san)
        info = self.open().analyse(board, chess.engine.Limit(depth=DEPTH), root_moves=[mv])
        self.calls += 1
        x[san] = self.cp(info["score"], board.turn)
        return x[san]

    def close(self):
        if self.eng:
            self.eng.quit()


# ---------- build ----------
def build_side(side, sample, eng, book, prev_ids, min_inc, notes, log):
    color = SIDES[side]["color"]
    root_epd, L = lichess_stats(sample, side)
    G, H, seen = my_games(side)
    b0 = chess.Board()
    for san in SIDES[side]["start"]:
        b0.push_san(san)

    nodes = {}      # epd -> node
    order = []

    def get(board, ply):
        e = epd(board)
        n = nodes.get(e)
        if not n:
            n = nodes[e] = {"epd": e, "fen": board.fen(), "ply": ply, "inc": 0.0, "par": [], "mine": board.turn == color}
            order.append(n)
        return n

    root = get(b0, len(SIDES[side]["start"]))
    root["inc"] = 1.0
    root_lines = eng.lines(b0)
    if root_lines:
        root["evalBefore"] = -max(root_lines.values())
    ply = root["ply"]
    stats = Counter()
    while True:
        level = [n for n in order if n["ply"] == ply and n["inc"] >= min_inc]
        if not level:
            break
        level.sort(key=lambda n: -n["inc"])
        for n in level:
            board = chess.Board(n["fen"])
            if n["mine"]:
                choose(n, board, eng, L, H, book, prev_ids, stats)
                if not n.get("move"):
                    continue
                child_board = board.copy()
                child_board.push_san(n["move"])
                if ply + 1 >= MAX_PLY or child_board.is_game_over():
                    continue
                c = get(child_board, ply + 1)
                c["inc"] += n["inc"]
                c["par"].append((n["epd"], n["move"], n["inc"]))
                c.setdefault("evalBefore", n["evalBefore"])
                n["child"] = c["epd"]
            else:
                dist, nl, ng = blend(L.get(n["epd"], Counter()), G.get(n["epd"], Counter()))
                n["nl"], n["ng"] = nl, ng
                if not dist:
                    continue
                reps = []
                for san, p in sorted(dist.items(), key=lambda kv: -kv[1]):
                    ci = n["inc"] * p
                    faced = G.get(n["epd"], Counter()).get(san, 0)
                    if ci < min_inc / 4:
                        continue
                    child_board = board.copy()
                    child_board.push_san(san)
                    if child_board.is_game_over():
                        continue
                    c = get(child_board, ply + 1)
                    c["inc"] += ci
                    c["par"].append((n["epd"], san, ci))
                    reps.append([san, p, faced, c["epd"]])
                n["reps"] = reps
        ply += 1
        if ply > MAX_PLY:
            break
        log("  %s ply %d: %d positions in the book so far, %d engine searches" % (side, ply, sum(1 for x in order if x["inc"] >= min_inc), eng.calls))

    keep = [n for n in order if n["inc"] >= min_inc]
    keep_set = {n["epd"] for n in keep}
    for n in keep:
        n["seen"] = seen.get(n["epd"], 0)
    # Opponent mistakes: the reply hands you a pawn or more (from your move's eval to your best after it).
    for n in keep:
        if n["mine"] or "reps" not in n:
            continue
        before = n.get("evalBefore")
        for r in n["reps"]:
            c = nodes.get(r[3])
            if before is None or not c or c["epd"] not in keep_set or c.get("best") is None:
                r.append(0)
                continue
            gain = c["best"] - before
            r.append(1 if gain >= PUNISH_GAIN and c["best"] >= PUNISH_MIN and before < 400 else 0)
    # Names: the position's own name, else the name it was reached with.
    names_out = []
    for n in sorted(keep, key=lambda n: n["ply"]):
        nm = book.get(n["epd"], "")
        if not nm:
            best = max((p for p in n["par"] if p[0] in nodes), key=lambda p: p[2], default=None)
            nm = nodes[best[0]].get("name", "") if best else ""
        n["name"] = nm
    return keep, nodes, root, stats


def blend(lc, gc):
    nl, ng = sum(lc.values()), sum(gc.values())
    if nl < 15 and ng < 3:
        return {}, nl, ng
    if nl >= 30:
        wg = ALPHA * ng / (ng + 10) if ng else 0
    else:
        wg = ng / (ng + nl) if ng + nl else 0
    out = Counter()
    for san, c in lc.items():
        out[san] += (1 - wg) * c / nl
    for san, c in gc.items():
        out[san] += wg * c / ng
    return out, nl, ng


def choose(n, board, eng, L, H, book, prev_ids, stats):
    e = n["epd"]
    pv = eng.lines(board)
    if not pv:
        return
    best_san = max(pv, key=pv.get)
    best = pv[best_san]
    n["best"] = best

    def ok(san):
        try:
            return best - eng.move(board, san) <= TOL
        except ValueError:
            return False

    choice, src = None, None
    # 1. the move you usually play
    usual = None
    hm = H.get(e, Counter())
    if hm:
        top, w = hm.most_common(1)[0]
        if H.raw[e][top] >= 2 and w >= 0.4 * sum(hm.values()):
            usual = top
    if usual and ok(usual):
        choice, src = usual, "usual"
    # 2. the move this book already had here (so a rebuild doesn't swap what you've learned)
    if not choice:
        for san in list(pv) + [s for s in H.get(e, {})] + [s for s in L.get(e, {})]:
            if card_id(e, san) in prev_ids and ok(san):
                choice, src = san, prev_ids[card_id(e, san)]
                break
    # 3. what most players at your level choose (only moves they really play)
    lm = L.get(e, Counter())
    if not choice and sum(lm.values()) >= 30:
        for san, c in lm.most_common(4):
            if c >= 10 and ok(san):
                choice, src = san, "popular"
                break
    # 4. Stockfish
    if not choice:
        choice, src = best_san, "engine"
    stats[src] += 1
    n["move"], n["src"] = choice, src
    n["evalBefore"] = eng.move(board, choice)          # your eval after your move (centipawns, your side)
    n["ok"] = [s for s, v in pv.items() if s != choice and best - v <= TOL]
    if usual and usual != choice:
        n["usual"] = [usual, max(0, best - eng.move(board, usual))]
    n["k"] = card_id(e, choice)
    n["theory"] = 1 if e in book else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", required=True)
    ap.add_argument("--min-games", type=int, default=500)
    ap.add_argument("--sides", default="b,w")
    a = ap.parse_args()
    t0 = time.time()
    log = lambda s: print("%5.0fs %s" % (time.time() - t0, s), flush=True)
    sample = json.load(open(a.sample))
    book = load_book()
    cache = json.load(open(CACHE_FILE)) if os.path.exists(CACHE_FILE) else {}
    prev = json.load(open(OUT_FILE)) if os.path.exists(OUT_FILE) else {}
    prev_ids = {}       # card id -> where that move came from last time
    for sd in ("b", "w"):
        for n in (prev.get(sd) or {}).get("nodes", []):
            if n.get("k"):
                prev_ids[n["k"]] = n.get("src") if n.get("src") in ("popular", "engine") else "popular"
    eng = Engine(os.environ.get("STOCKFISH", "stockfish"), cache)
    min_inc = 1.0 / a.min_games
    out = {"v": 1, "built": time.strftime("%Y-%m-%d"), "goals": GOALS, "minGames": a.min_games,
           "sample": dict(sample["meta"], games={sd: sample[sd]["root"] for sd in ("b", "w")}), "names": []}
    name_ix = {}
    notes = load_notes()
    try:
        for side in [s for s in a.sides.split(",") if s]:
            log("building " + side)
            keep, nodes, root, stats = build_side(side, sample, eng, book, prev_ids, min_inc, notes, log)
            out[side] = encode(side, keep, nodes, root, out["names"], name_ix, notes)
            log("  %s: %d positions, %d of your moves (%s)" % (side, len(keep), sum(1 for n in keep if n["mine"]), dict(stats)))
            with open(CACHE_FILE, "w") as f:
                json.dump(cache, f, separators=(",", ":"), sort_keys=True)
    finally:
        eng.close()
        with open(CACHE_FILE, "w") as f:
            json.dump(cache, f, separators=(",", ":"), sort_keys=True)
    for side in ("b", "w"):
        if side not in out and side in prev:
            out[side] = prev[side]
    with open(OUT_FILE, "w") as f:
        json.dump(out, f, separators=(",", ":"))
    log("wrote %s (%d KB)" % (OUT_FILE, os.path.getsize(OUT_FILE) // 1024))


def load_notes():
    """scripts/repertoire-notes.tsv: side, moves from the start (your move last), the idea in one line."""
    notes = {}
    if not os.path.exists(NOTES_FILE):
        return notes
    with open(NOTES_FILE, encoding="utf-8") as f:
        for row in csv.reader(f, delimiter="\t"):
            if len(row) < 3 or row[0].startswith("#"):
                continue
            side, moves, text = row[0].strip(), row[1].split(), row[2].strip()
            b = chess.Board()
            try:
                for san in moves[:-1]:
                    b.push_san(san)
                san = b.san(b.parse_san(moves[-1]))
            except (ValueError, IndexError):
                print("note: can't play", row[1], file=sys.stderr)
                continue
            notes[(side, epd(b), san)] = text
    return notes


def encode(side, keep, nodes, root, names, name_ix, notes):
    """Nodes as a compact list; index 0 is the start (your opponent to move after the first moves)."""
    keep = sorted(keep, key=lambda n: (n["ply"], -n["inc"]))
    keep.remove(root)
    keep.insert(0, root)
    ix = {n["epd"]: i for i, n in enumerate(keep)}

    def nm(s):
        if s not in name_ix:
            name_ix[s] = len(names)
            names.append(s)
        return name_ix[s]

    out = []
    used_notes = 0
    for n in keep:
        o = {"p": round(n["inc"], 6), "n": nm(n.get("name", ""))}
        par = [p for p in n["par"] if p[0] in ix]
        if par:
            best = max(par, key=lambda p: p[2])
            o["pa"] = [ix[best[0]], best[1]]
        if n["mine"]:
            if not n.get("move"):
                o["end"] = 1
            else:
                o.update({"m": n["move"], "k": n["k"], "src": n["src"], "e": n.get("evalBefore", 0)})
                if n.get("ok"):
                    o["ok"] = n["ok"]
                if n.get("usual"):
                    o["u"] = n["usual"]
                if n.get("child") in ix:
                    o["c"] = ix[n["child"]]
                if n.get("theory"):
                    o["t"] = 1
                note = notes.get((side, n["epd"], n["move"]))
                if note:
                    o["note"] = note
                    used_notes += 1
        else:
            o["r"] = [[r[0], round(r[1], 4), r[2], ix[r[3]], r[4] if len(r) > 4 else 0] for r in n.get("reps", []) if r[3] in ix]
        if n.get("nl") is not None:
            o["nl"] = n["nl"]
        if n.get("seen"):
            o["g"] = n["seen"]
        out.append(o)
    missing = [k for k in notes if k[0] == side and not any(nn.get("epd") == k[1] and nn.get("move") == k[2] for nn in keep)]
    if missing:
        print("notes not in the book (%s): %d" % (side, len(missing)), file=sys.stderr)
    b0 = chess.Board()
    for san in SIDES[side]["start"]:
        b0.push_san(san)
    return {"title": SIDES[side]["title"], "start": SIDES[side]["start"], "nodes": out, "notes": used_notes}


if __name__ == "__main__":
    main()
