"""Download new chess.com games and review them with Stockfish.

Runs on GitHub Actions (see .github/workflows/update.yml). Writes:
  data/games.json     every game as PGN
  data/analysis.json  Stockfish evaluation of every position, same format the page uses

Each run downloads new games first, then reviews games (new ones first, then games reviewed by an
older version of this script, newest first) until MAX_MINUTES is used up. Whatever is left gets
picked up on the next run.

analysis.json, one entry per game (version 2):
  v   analysis version: 2 = Stockfish 19, the engine's two best moves in every position
  d   search depth
  e   one entry per position, always from White's side:
        ["c", cp, best, second]    centipawns; second is the engine's second line as ["c"|"m", value, move]
        ["m", mate, best, second]  mate in N (positive: White mates)
        ["x", winner]              checkmate on the board (1: White won, -1: Black won)
        ["d"]                      stalemate or a dead draw
      (second is left out when there is only one legal move)
  b   book plies: how many moves from the start follow known opening theory
  bn  {ply: opening name} for the book plies where the name changes
  p   puzzle lines (Stockfish's line before each of the user's mistakes), r refutation lines
  q   punish lines (after each opponent mistake in the opening), pz / qz their versions
"""
import csv
import io
import json
import math
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.request

import chess
import chess.engine
import chess.pgn

USER = os.environ.get("CHESS_USER", "chaflox").strip().lower()
DEPTH = int(os.environ.get("DEPTH", "18"))
MULTIPV = 2
MAX_MINUTES = float(os.environ.get("MAX_MINUTES", "40"))
PUZZLE_DEPTH = int(os.environ.get("PUZZLE_DEPTH", "20"))
ANALYSIS_VERSION = 2
PUZZLE_VERSION = 3     # 3: puzzles for every move that lost 0.10 expected points or more
PUNISH_VERSION = 1
PUNISH_PLIES = 24      # opponent mistakes in the first 12 moves
DATA = "data"
GAMES_FILE = os.path.join(DATA, "games.json")
ANALYSIS_FILE = os.path.join(DATA, "analysis.json")
BOOK_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "openings.tsv")
UA = "chess-review/1.0 (personal game review; github.com/pjchafloque/chess-review)"
START = time.time()


def fnv(s: str) -> str:
    """Same 32-bit FNV-1a hash as the page, so game ids match."""
    h = 2166136261
    raw = s.encode("utf-16-le")
    for i in range(0, len(raw), 2):
        h ^= raw[i] | (raw[i + 1] << 8)
        h = (h * 16777619) & 0xFFFFFFFF
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    if h == 0:
        return "0"
    out = ""
    while h:
        h, r = divmod(h, 36)
        out = digits[r] + out
    return out


def game_id(pgn: str) -> str:
    m = re.search(r'^\[Link "([^"]*)"\]', pgn, re.M)
    return "l" + fnv(m.group(1)) if m else "p" + fnv(pgn)


def get_json(url: str):
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 3:
                time.sleep(5 * (attempt + 1))
                continue
            raise


def load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def save(path, obj):
    os.makedirs(DATA, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, separators=(",", ":"), ensure_ascii=False)
    os.replace(tmp, path)


def end_key(pgn: str) -> str:
    d = re.search(r'^\[(?:UTCDate|Date) "([^"]*)"\]', pgn, re.M)
    t = re.search(r'^\[(?:UTCTime|StartTime) "([^"]*)"\]', pgn, re.M)
    return (d.group(1) if d else "").replace(".", "") + (t.group(1) if t else "").replace(":", "")


def fetch_games(store):
    known = {g["id"] for g in store["games"]}
    newest = max((g["key"] for g in store["games"]), default="")
    from_month = newest[:6]
    archives = get_json(f"https://api.chess.com/pub/player/{USER}/games/archives").get("archives", [])
    added = 0
    for url in archives:
        m = re.search(r"(\d{4})/(\d{2})$", url)
        if not m or (m.group(1) + m.group(2)) < from_month:
            continue
        month = get_json(url)
        for g in month.get("games", []):
            pgn = g.get("pgn")
            if not pgn or g.get("rules", "chess") != "chess":
                continue
            gid = game_id(pgn)
            if gid in known:
                continue
            known.add(gid)
            store["games"].append({"id": gid, "key": end_key(pgn), "pgn": pgn})
            added += 1
        time.sleep(0.5)
    store["games"].sort(key=lambda g: g["key"], reverse=True)
    store["user"] = USER
    store["updated"] = int(time.time())
    print(f"{added} new games, {len(store['games'])} total")
    return added


def engine_path():
    for p in (os.environ.get("STOCKFISH"), shutil.which("stockfish"), "/usr/games/stockfish", "/usr/bin/stockfish"):
        if p and os.path.exists(p):
            return p
    raise SystemExit("Stockfish not found")


# ---------- opening book ----------
# Every position on a named line of the Lichess opening list (CC0) or eco.json (MIT), merged into
# scripts/openings.tsv. A game is "in book" for as long as each move reaches one of these positions,
# so transpositions count too.
_BOOK = None


def epd(board) -> str:
    return " ".join(board.fen().split(" ")[:4])


def book():
    global _BOOK
    if _BOOK is None:
        _BOOK = {}
        with open(BOOK_FILE, encoding="utf-8") as f:
            for row in csv.DictReader(f, delimiter="\t"):
                b = chess.Board()
                try:
                    for tok in row["pgn"].split():
                        if not tok[0].isdigit():
                            b.push_san(tok)
                            _BOOK.setdefault(epd(b), "")
                except ValueError:
                    continue
                _BOOK[epd(b)] = row["name"]
    return _BOOK


def book_info(pgn: str):
    """How many plies from the start are book moves, and the opening's name where it changes."""
    game = chess.pgn.read_game(io.StringIO(pgn))
    if game.headers.get("FEN"):
        return 0, {}
    bk, board, plies, names, last = book(), game.board(), 0, {}, ""
    for move in game.mainline_moves():
        board.push(move)
        k = epd(board)
        if k not in bk:
            break
        plies += 1
        if bk[k] and bk[k] != last:
            names[str(plies)] = last = bk[k]
    return plies, names


# ---------- expected points ----------
# Chance of winning (draws count half) from Stockfish's evaluation, with Lichess's published curve.
# The page uses the same numbers to grade moves.
K = 0.00368208


def ep_white(e) -> float:
    if e[0] == "c":
        return 1 / (1 + math.exp(-K * max(-4000, min(4000, e[1]))))
    if e[0] in ("m", "x"):
        return 1.0 if e[1] > 0 else 0.0
    return 0.5


def num(e):
    """Pawns from White's side, capped at +/-10, mate counts as 10 (same as the page)."""
    if e[0] == "c":
        return max(-10.0, min(10.0, e[1] / 100))
    if e[0] == "m":
        return 10.0 if e[1] > 0 else -10.0
    if e[0] == "x":
        return 10.0 * e[1]
    return 0.0


def entry(infos) -> list:
    """One stored position from Stockfish's lines: best line first, then the second line."""
    out = None
    for info in infos:
        pv, sc = info.get("pv"), info.get("score")
        if not pv or sc is None:
            continue
        w = sc.white()
        e = ["m", w.mate()] if w.is_mate() else ["c", w.score()]
        if out is None:
            out = e + [pv[0].uci()]
        else:
            out.append(e + [pv[0].uci()])
            break
    return out or ["c", 0, None]


def review(pgn: str, engine, gid: str) -> list:
    game = chess.pgn.read_game(io.StringIO(pgn))
    board = game.board()
    boards = [board.copy()]
    for move in game.mainline_moves():
        board.push(move)
        boards.append(board.copy())
    evals = []
    for b in boards:
        if b.is_checkmate():
            evals.append(["x", -1 if b.turn == chess.WHITE else 1])
        elif b.is_stalemate() or b.is_insufficient_material():
            evals.append(["d"])
        else:
            infos = engine.analyse(b, chess.engine.Limit(depth=DEPTH), multipv=MULTIPV, game=gid)
            evals.append(entry(infos))
    return evals


def user_side(game):
    white = game.headers.get("White", "").lower()
    black = game.headers.get("Black", "").lower()
    return chess.WHITE if white == USER else chess.BLACK if black == USER else None


def puzzle_lines(pgn: str, evals: list, engine) -> tuple:
    """For each of the user's moves that lost 0.10 expected points or more (a mistake, miss or
    blunder on the page), store Stockfish's main line from the position before it (the puzzle) and
    from the position after the game move (what the opponent could do about it), both searched
    deeper. The page decides how much to use."""
    game = chess.pgn.read_game(io.StringIO(pgn))
    me = user_side(game)
    if me is None:
        return {}, {}
    board = game.board()
    out, refs = {}, {}
    for i, move in enumerate(game.mainline_moves()):
        if board.turn == me and i + 1 < len(evals):
            mine = (lambda e: ep_white(e)) if me == chess.WHITE else (lambda e: 1 - ep_white(e))
            lost = mine(evals[i]) - mine(evals[i + 1])
            best = evals[i][2] if len(evals[i]) > 2 else None
            if lost >= 0.10 and best and best != move.uci():
                info = engine.analyse(board, chess.engine.Limit(depth=PUZZLE_DEPTH))
                pv = [m.uci() for m in (info.get("pv") or [])[:12]]
                if pv:
                    out[str(i)] = pv
                board.push(move)
                if not board.is_game_over():
                    info = engine.analyse(board, chess.engine.Limit(depth=PUZZLE_DEPTH))
                    ref = [m.uci() for m in (info.get("pv") or [])[:10]]
                    if ref:
                        refs[str(i)] = ref
                board.pop()
        board.push(move)
    return out, refs


def punish_lines(pgn: str, evals: list, engine) -> dict:
    """For each opponent move in the opening that lost a pawn or more and left the user clearly
    better (+0.8), store Stockfish's main line from the position after it, searched deeper:
    the "punish it" drills on the Openings page. Keyed by the ply of the user's reply."""
    game = chess.pgn.read_game(io.StringIO(pgn))
    me = user_side(game)
    if me is None:
        return {}
    s = 1 if me == chess.WHITE else -1
    board = game.board()
    out = {}
    for i, move in enumerate(game.mainline_moves()):
        if i >= PUNISH_PLIES or i + 1 >= len(evals):
            break
        mover = board.turn
        board.push(move)
        if mover == me or board.is_game_over():
            continue
        before, after = s * num(evals[i]), s * num(evals[i + 1])
        if after - before >= 1 and after >= 0.8 and before < 4:
            info = engine.analyse(board, chess.engine.Limit(depth=PUZZLE_DEPTH))
            pv = [m.uci() for m in (info.get("pv") or [])[:12]]
            if pv:
                out[str(i + 1)] = pv
    return out


def stale(a) -> bool:
    """Reviewed by an older version of this script (Stockfish 16, one line per position)."""
    return (a or {}).get("v", 1) < ANALYSIS_VERSION


def run_reviews(store, analysis, shard=0, shards=1, checkpoint=None):
    """Review games and add puzzle lines. With shards > 1, this machine only takes every
    shards-th job. Returns the ids it changed. checkpoint(changed) is called after every job,
    so a machine that gets stopped keeps what it finished."""
    by_id = {g["id"]: g for g in store["games"]}
    order = [g["id"] for g in store["games"]]          # newest first
    new = [gid for gid in order if gid not in analysis]
    old = [gid for gid in order if gid in analysis and stale(analysis[gid])]
    fresh = [gid for gid in order if gid in analysis and not stale(analysis[gid]) and analysis[gid].get("e")]
    need_lines = [gid for gid in fresh if analysis[gid].get("pz") != PUZZLE_VERSION]
    need_punish = [gid for gid in fresh if analysis[gid].get("qz") != PUNISH_VERSION and gid not in need_lines]
    work = [("review", gid) for gid in new] + [("lines", gid) for gid in need_lines] + \
           [("punish", gid) for gid in need_punish] + [("review", gid) for gid in old]
    work = work[shard::shards]
    print(f"Shard {shard + 1}/{shards}: {len(work)} jobs ({len(new)} new games, {len(old)} to re-review, "
          f"{len(need_lines)} missing puzzle lines, {len(need_punish)} missing punish lines overall)")
    changed = set()
    if not work:
        return changed
    engine = chess.engine.SimpleEngine.popen_uci(engine_path())
    engine.configure({"Threads": os.cpu_count() or 2, "Hash": 512})
    print("Engine:", engine.id.get("name", "?"), "| depth", DEPTH, "| threads", os.cpu_count())

    def add_lines(gid):
        a = analysis[gid]
        try:
            a["p"], a["r"] = puzzle_lines(by_id[gid]["pgn"], a["e"], engine)
        except Exception as e:
            print("No puzzle lines for", gid, e)
            a["p"], a["r"] = {}, {}
        a["pz"] = PUZZLE_VERSION
        add_punish(gid)

    def add_punish(gid):
        a = analysis[gid]
        try:
            a["q"] = punish_lines(by_id[gid]["pgn"], a["e"], engine)
        except Exception as e:
            print("No punish lines for", gid, e)
            a["q"] = {}
        a["qz"] = PUNISH_VERSION

    done = 0
    try:
        for kind, gid in work:
            if time.time() - START > MAX_MINUTES * 60:
                break
            if kind == "review":
                pgn = by_id[gid]["pgn"]
                try:
                    a = {"v": ANALYSIS_VERSION, "d": DEPTH, "e": review(pgn, engine, gid)}
                    try:
                        a["b"], a["bn"] = book_info(pgn)
                    except Exception as e:
                        print("No book info for", gid, e)
                    analysis[gid] = a
                    add_lines(gid)
                except Exception as e:
                    print("Skipped", gid, e)
                    if gid not in analysis:
                        analysis[gid] = {"v": ANALYSIS_VERSION, "d": DEPTH, "e": None}
                    else:
                        continue
            elif kind == "punish":
                add_punish(gid)
            else:
                add_lines(gid)
            changed.add(gid)
            done += 1
            if checkpoint:
                checkpoint(changed)
            if done % 10 == 0:
                print(f"  {done}/{len(work)} done ({(time.time() - START) / 60:.1f} min)")
    finally:
        engine.quit()
    print(f"Shard {shard + 1}/{shards}: finished {done} of {len(work)}")
    return changed


def main():
    mode = os.environ.get("MODE", "all")
    store = load(GAMES_FILE, {"user": USER, "games": []})
    analysis = load(ANALYSIS_FILE, {})

    if mode in ("all", "fetch"):
        try:
            fetch_games(store)
        except Exception as e:  # keep reviewing even if chess.com is down
            print("Could not download games:", e)
        save(GAMES_FILE, store)
        if mode == "fetch":
            return

    if mode == "review":
        # One of several machines working in parallel; writes only what it changed.
        shard, shards = int(os.environ["SHARD"]), int(os.environ["SHARDS"])
        out = os.environ.get("OUT", f"part-{shard}.json")

        def write_part(changed):
            tmp = out + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({gid: analysis[gid] for gid in changed}, f, separators=(",", ":"))
            os.replace(tmp, out)

        write_part(run_reviews(store, analysis, shard, shards, checkpoint=write_part))
        return

    if mode == "merge":
        parts = sys.argv[1:]
        n = 0
        for path in parts:
            part = load(path, {})
            analysis.update(part)
            n += len(part)
        save(ANALYSIS_FILE, analysis)
        games = store["games"]
        reviewed = sum(1 for g in games if (analysis.get(g["id"]) or {}).get("e"))
        current = sum(1 for g in games if (analysis.get(g["id"]) or {}).get("e") and not stale(analysis[g["id"]]))
        print(f"Merged {n} results from {len(parts)} machines; {reviewed}/{len(games)} games reviewed, "
              f"{current} with Stockfish 19")
        return

    run_reviews(store, analysis)
    save(ANALYSIS_FILE, analysis)


if __name__ == "__main__":
    main()
