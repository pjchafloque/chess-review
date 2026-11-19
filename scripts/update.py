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
  z   puzzles: {ply: solution} for every position where the user had one clear winning move (Lichess's
      rules, see "puzzles" below), found or missed; the solution is a list of UCI moves, the user's
      moves and the opponent's replies, ending with the user's move. zz its version
  y   defense puzzles, same format: positions where exactly one move held. yz its version
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
# Most searches take a second or two, but a few rook endgames took minutes each at depth 18 and kept three
# machines busy until the 6-hour job timeout on every run (2026-11-18/19). No search runs longer than this.
SEARCH_SECONDS = float(os.environ.get("SEARCH_SECONDS", "30"))
ANALYSIS_VERSION = 2
PUZZLE_VERSION = 3     # 3: puzzles for every move that lost 0.10 expected points or more
PUNISH_VERSION = 1
PUNISH_PLIES = 24      # opponent mistakes in the first 12 moves
COOK_VERSION = 1       # 1: Lichess's puzzle rules (lichess-puzzler generator v50), see "puzzles"
DEFENSE_VERSION = 1    # 1: positions where exactly one move held, see "puzzles"
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


def limit(depth):
    return chess.engine.Limit(depth=depth, time=SEARCH_SECONDS)


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
            infos = engine.analyse(b, limit(DEPTH), multipv=MULTIPV, game=gid)
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
                info = engine.analyse(board, limit(PUZZLE_DEPTH))
                pv = [m.uci() for m in (info.get("pv") or [])[:12]]
                if pv:
                    out[str(i)] = pv
                board.push(move)
                if not board.is_game_over():
                    info = engine.analyse(board, limit(PUZZLE_DEPTH))
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
            info = engine.analyse(board, limit(PUZZLE_DEPTH))
            pv = [m.uci() for m in (info.get("pv") or [])[:12]]
            if pv:
                out[str(i + 1)] = pv
    return out


# ---------- puzzles ----------
# A blunder is a move that throws something away; before most blunders there was no puzzle to solve,
# just several ordinary moves that would have been fine. A puzzle is a position with exactly one move
# that wins: your opponent has just made a mistake, the engine's best move wins material or mates,
# and every other move is much worse. The rules follow Lichess's puzzle generator
# (github.com/ornicar/lichess-puzzler, generator/generator.py, version 50):
#   - Start: the position before one of your moves, with at least two legal moves. You weren't
#     already winning by 3+ pawns before your opponent's last move (unless there's a mate).
#   - Material puzzles: the best move leaves you 2+ pawns up, and your opponent's last move gave away
#     at least 0.6 win chances (Lichess's -1..1 scale; 0.3 expected points). Under +4 it only counts
#     if you're behind in material on the board (you're getting something back). The line goes on
#     for as long as your next move is the only good one and you stay 2+ pawns up, and it ends with
#     your move, never a forced one (only legal move).
#   - "The only good move": its win chances beat the second-best move's by more than 0.7, or it's a
#     mate in one and no move that doesn't mate keeps more than 0.6.
#   - Mate puzzles: the best move mates in under 15 moves (6 of yours at most here), you aren't
#     already ahead in material, and the opponent's replies are the engine's best defense.
# Changed for this site:
#   - One-move puzzles stay (Lichess drops them as too easy; at club level a piece left hanging is
#     exactly what you need to learn to spot), and so do mates in one. A game can have several.
#   - Material puzzles also count when you were already ahead in material (Lichess skips those); the
#     two rules above already make sure you weren't winning before your opponent's mistake.
#   - Mate puzzles say "Mate in N" on the page, so another move that also wins (say, a queen instead
#     of mate in 2) doesn't make them ambiguous. Each of your moves must be the only one that mates
#     that fast; any mate in one is fine. Lichess, which doesn't name the goal, drops all of these.
# Defense puzzles (the Defend set, the user's choice on 2026-11-19): your opponent threatens something
# and exactly one move holds, Lichess's "defensive move" theme. The same "only good move" test as above
# (the best move beats the second-best by more than 0.7 win chances), at every one of your moves in
# the line, for as long as you stay above -2 (4 moves at most). Not when it's a Win puzzle, not when
# the best move mates, and not when the only move is taking back on the square your opponent just
# captured on: that's a reflex, not a puzzle (about half of all only-moves in your games).
MATE_SOON = chess.engine.Mate(15)
DEFENSE_MAX_MOVES = 4
COOK_MAX_MOVES = 6     # your moves; longer material lines stop here, longer mates don't count


def win_chances(score) -> float:
    """Lichess's winning chances, -1..1, for a Score already seen from one side."""
    m = score.mate()
    if m is not None:
        return 1.0 if m > 0 else -1.0
    return 2 / (1 + math.exp(-K * score.score())) - 1


def stored_score(e, side):
    """A stored eval (White's view) as a python-chess Score from `side`'s view."""
    if e[0] == "c":
        sc = chess.engine.Cp(e[1])
    elif e[0] == "m":
        sc = chess.engine.Mate(e[1])
    elif e[0] == "x":
        sc = chess.engine.MateGiven if e[1] > 0 else -chess.engine.MateGiven
    else:
        sc = chess.engine.Cp(0)
    return chess.engine.PovScore(sc, chess.WHITE).pov(side)


PIECES = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9}


def material_diff(board, side) -> int:
    return sum((len(board.pieces(t, side)) - len(board.pieces(t, not side))) * v for t, v in PIECES.items())


def puzzle_start(board, side, prev_e, cur_e):
    """The first test, from the stored evaluations: "mate", "material" or None."""
    if board.legal_moves.count() < 2:
        return None
    score, prev = stored_score(cur_e, side), stored_score(prev_e, side)
    if prev > chess.engine.Cp(300) and score < MATE_SOON:
        return None                      # already winning before the opponent's move
    if score > MATE_SOON:
        return "mate" if material_diff(board, side) <= 0 else None
    if score >= chess.engine.Cp(200) and win_chances(score) > win_chances(prev) + 0.6:
        if score < chess.engine.Cp(400) and material_diff(board, side) > -1:
            return None                  # not clearly winning, and not winning back lost material
        return "material"
    return None


def top_two(engine, board, side, limit):
    infos = engine.analyse(board, limit, multipv=2)
    out = [(i["pv"][0], i["score"].pov(side)) for i in infos if i.get("pv")]
    return out[0], (out[1] if len(out) > 1 else None)


def only_move(engine, board, side, best, second, limit) -> bool:
    """Is the best move the only good one (Lichess's is_valid_attack)?"""
    if second is None:
        return True
    if best[1] == chess.engine.Mate(1):
        if win_chances(second[1]) <= 0.6:
            return True
        if second[1] == chess.engine.Mate(1):
            # several mates in one: fine unless a move that doesn't mate also wins
            mates = 0
            for m in board.legal_moves:
                board.push(m)
                mates += board.is_checkmate()
                board.pop()
            infos = engine.analyse(board, limit, multipv=mates + 1)
            last = infos[-1]["score"].pov(side)
            return not (last < chess.engine.Mate(1) and win_chances(last) > 0.6)
        return False
    return win_chances(best[1]) > win_chances(second[1]) + 0.7


def only_fast_mate(best, second) -> bool:
    """For a puzzle that says "Mate in N": no other move mates as fast (any mate in one is fine)."""
    n = best[1].mate()
    if n is None or n <= 0:
        return False
    if n == 1 or second is None:
        return True
    m = second[1].mate()
    return m is None or m <= 0 or m > n


def cook_mate(engine, board, side, limit, defense, strict, moves=0):
    """The mating line, or None. strict[0] turns False when a step only passes the "Mate in N" test."""
    if board.is_game_over():
        return []
    if board.turn == side:
        if moves >= COOK_MAX_MOVES * 2:
            return None
        best, second = top_two(engine, board, side, limit)
        if best[1] < MATE_SOON or not only_fast_mate(best, second):
            return None
        if strict[0] and not only_move(engine, board, side, best, second, limit):
            strict[0] = False
        move = best[0]
    else:
        move = engine.play(board, defense).move
        if move is None:
            return None
    board.push(move)
    try:
        rest = cook_mate(engine, board, side, limit, defense, strict, moves + 1)
    finally:
        board.pop()
    return None if rest is None else [move] + rest


def cook_material(engine, board, side, limit, moves=0):
    """[(move, has_second)] for as long as your moves stay the only good ones, or None."""
    if board.is_repetition(2):
        return None
    if moves >= COOK_MAX_MOVES * 2 or board.is_game_over():
        return []
    if board.turn == side:
        best, second = top_two(engine, board, side, limit)
        if not only_move(engine, board, side, best, second, limit):
            return []
    else:                                # the opponent's reply: their best move is all we need
        info = engine.analyse(board, limit)
        if not info.get("pv"):
            return None
        best, second = (info["pv"][0], info["score"].pov(side)), None
    if best[1] < chess.engine.Cp(200):
        return None
    board.push(best[0])
    try:
        rest = cook_material(engine, board, side, limit, moves + 1)
    finally:
        board.pop()
    return None if rest is None else [(best[0], second is not None)] + rest


def cook(engine, board, side, kind, limit, defense):
    """(solution, strict): the moves from this position, ending with yours, or (None, False).
    strict: it also passes Lichess's own test (always true for material puzzles)."""
    if kind == "mate":
        strict = [True]
        sol = cook_mate(engine, board, side, limit, defense, strict)
        return ([m.uci() for m in sol], strict[0]) if sol else (None, False)
    sol = end_on_your_move(cook_material(engine, board, side, limit))
    return (sol, True) if sol else (None, False)


def defense_start(board, side, prev_e, cur_e) -> bool:
    """A Defend puzzle may start here (from the stored evaluations): one move holds, every other move
    is at least 0.7 win chances worse, it isn't a Win puzzle and the move isn't a plain recapture."""
    if board.legal_moves.count() < 2 or len(cur_e) < 4 or not cur_e[2] or not cur_e[3]:
        return False
    best, second = stored_score(cur_e, side), stored_score(cur_e[3], side)
    if best.mate() is not None or not win_chances(best) > win_chances(second) + 0.7:
        return False
    if puzzle_start(board, side, prev_e, cur_e):
        return False
    if board.move_stack:
        last = board.pop()
        took = board.is_capture(last)
        board.push(last)
        if took and chess.Move.from_uci(cur_e[2]).to_square == last.to_square:
            return False                 # taking back: a reflex
    return True


def cook_defense(engine, board, side, limit, moves=0):
    """[(move, has_second)] for as long as your moves stay the only ones that hold, or None."""
    if board.is_repetition(2) or moves >= DEFENSE_MAX_MOVES * 2 or board.is_game_over():
        return []
    if board.turn == side:
        best, second = top_two(engine, board, side, limit)
        if not only_move(engine, board, side, best, second, limit):
            return []
    else:
        info = engine.analyse(board, limit)
        if not info.get("pv"):
            return None
        best, second = (info["pv"][0], info["score"].pov(side)), None
    if best[1] < chess.engine.Cp(-200):
        return None                      # it doesn't hold after all
    board.push(best[0])
    try:
        rest = cook_defense(engine, board, side, limit, moves + 1)
    finally:
        board.pop()
    return None if rest is None else [(best[0], second is not None)] + rest


def end_on_your_move(sol):
    """A solution ends with your move, and not with a forced one (only legal move)."""
    while sol and (len(sol) % 2 == 0 or not sol[-1][1]):
        sol = sol[:-1]
    return [m.uci() for m, _ in sol] if sol else None


def find_puzzles(pgn: str, evals: list, engine, win=True, defend=True):
    """({ply: solution} Win puzzles, {ply: solution} Defend puzzles) for the positions before the user's moves."""
    game = chess.pgn.read_game(io.StringIO(pgn))
    me = user_side(game)
    if me is None or game.headers.get("FEN"):
        return {}, {}
    search, defense = limit(PUZZLE_DEPTH), limit(15)
    board = game.board()
    wins, saves = {}, {}
    for i, move in enumerate(game.mainline_moves()):
        if board.turn == me and 0 < i < len(evals) and evals[i] and evals[i - 1] and len(evals[i]) > 2:
            kind = puzzle_start(board, me, evals[i - 1], evals[i])
            if kind and win:
                sol, _ = cook(engine, board.copy(), me, kind, search, defense)
                if sol:
                    wins[str(i)] = sol
            elif not kind and defend and defense_start(board, me, evals[i - 1], evals[i]):
                sol = end_on_your_move(cook_defense(engine, board.copy(), me, search))
                if sol:
                    saves[str(i)] = sol
        board.push(move)
    return wins, saves


def cook_due(a) -> bool:
    return a.get("zz") != COOK_VERSION or a.get("yz") != DEFENSE_VERSION


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
    # one job per game, so two machines never write the same game (the merge keeps one copy of each)
    need_cook = [gid for gid in fresh if cook_due(analysis[gid]) and gid not in need_lines and gid not in need_punish]
    work = [("review", gid) for gid in new] + [("lines", gid) for gid in need_lines] + \
           [("punish", gid) for gid in need_punish] + [("cook", gid) for gid in need_cook] + \
           [("review", gid) for gid in old]
    work = work[shard::shards]
    print(f"Shard {shard + 1}/{shards}: {len(work)} jobs ({len(new)} new games, {len(old)} to re-review, "
          f"{len(need_lines)} missing puzzle lines, {len(need_punish)} missing punish lines, "
          f"{len(need_cook)} to look for puzzles overall)")
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
        add_cook(gid)

    def add_punish(gid):
        a = analysis[gid]
        try:
            a["q"] = punish_lines(by_id[gid]["pgn"], a["e"], engine)
        except Exception as e:
            print("No punish lines for", gid, e)
            a["q"] = {}
        a["qz"] = PUNISH_VERSION

    def add_cook(gid):
        a = analysis[gid]
        win, defend = a.get("zz") != COOK_VERSION, a.get("yz") != DEFENSE_VERSION
        try:
            z, y = find_puzzles(by_id[gid]["pgn"], a["e"], engine, win, defend)
        except Exception as e:
            print("No puzzles for", gid, e)
            z, y = {}, {}
        if win:
            a["z"], a["zz"] = z, COOK_VERSION
        if defend:
            a["y"], a["yz"] = y, DEFENSE_VERSION

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
                if cook_due(analysis[gid]):
                    add_cook(gid)
            elif kind == "cook":
                add_cook(gid)
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
