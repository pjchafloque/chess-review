"""Download new chess.com games and review them with Stockfish.

Runs on GitHub Actions (see .github/workflows/update.yml). Writes:
  data/games.json     every game as PGN
  data/analysis.json  Stockfish evaluation of every position, same format the page uses

Each run downloads new games first, then reviews unreviewed games (newest first)
until MAX_MINUTES is used up. Whatever is left gets picked up on the next run.
"""
import io
import json
import os
import re
import shutil
import time
import urllib.error
import urllib.request

import chess
import chess.engine
import chess.pgn

USER = os.environ.get("CHESS_USER", "chaflox").strip().lower()
DEPTH = int(os.environ.get("DEPTH", "14"))
MAX_MINUTES = float(os.environ.get("MAX_MINUTES", "40"))
PUZZLE_DEPTH = int(os.environ.get("PUZZLE_DEPTH", "18"))
PUZZLE_VERSION = 1
DATA = "data"
GAMES_FILE = os.path.join(DATA, "games.json")
ANALYSIS_FILE = os.path.join(DATA, "analysis.json")
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
    for p in (shutil.which("stockfish"), "/usr/games/stockfish", "/usr/bin/stockfish"):
        if p and os.path.exists(p):
            return p
    raise SystemExit("Stockfish not found")


def review(pgn: str, engine) -> list:
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
            info = engine.analyse(b, chess.engine.Limit(depth=DEPTH))
            score = info["score"].white()
            pv = info.get("pv") or []
            best = pv[0].uci() if pv else None
            if score.is_mate():
                evals.append(["m", score.mate(), best])
            else:
                evals.append(["c", score.score(), best])
    return evals


def num(e):
    """Pawns from White's side, capped at +/-10, mate counts as 10 (same as the page)."""
    if e[0] == "c":
        return max(-10.0, min(10.0, e[1] / 100))
    if e[0] == "m":
        return 10.0 if e[1] > 0 else -10.0
    if e[0] == "x":
        return 10.0 * e[1]
    return 0.0


def puzzle_lines(pgn: str, evals: list, engine) -> dict:
    """For each of the user's mistakes and blunders, store Stockfish's main line from the
    position before it, searched deeper. The page decides how much of it the puzzle uses."""
    game = chess.pgn.read_game(io.StringIO(pgn))
    white = game.headers.get("White", "").lower()
    black = game.headers.get("Black", "").lower()
    me = chess.WHITE if white == USER else chess.BLACK if black == USER else None
    if me is None:
        return {}
    board = game.board()
    out = {}
    for i, move in enumerate(game.mainline_moves()):
        if board.turn == me and i + 1 < len(evals):
            s = 1 if me == chess.WHITE else -1
            before, after = s * num(evals[i]), s * num(evals[i + 1])
            decided = (before >= 4 and after >= 4) or (before <= -4 and after <= -4)
            best = evals[i][2] if len(evals[i]) > 2 else None
            if before - after >= 1 and not decided and best and best != move.uci():
                info = engine.analyse(board, chess.engine.Limit(depth=PUZZLE_DEPTH))
                pv = [m.uci() for m in (info.get("pv") or [])[:12]]
                if pv:
                    out[str(i)] = pv
        board.push(move)
    return out


def main():
    store = load(GAMES_FILE, {"user": USER, "games": []})
    analysis = load(ANALYSIS_FILE, {})
    try:
        fetch_games(store)
    except Exception as e:  # keep reviewing even if chess.com is down
        print("Could not download games:", e)
    save(GAMES_FILE, store)

    todo = [g for g in store["games"] if g["id"] not in analysis]
    by_id = {g["id"]: g for g in store["games"]}
    need_lines = [gid for gid, a in analysis.items() if a.get("e") and a.get("pz") != PUZZLE_VERSION and gid in by_id]
    print(f"{len(todo)} games waiting for review, {len(need_lines)} reviewed games need puzzle lines")
    if not todo and not need_lines:
        return
    engine = chess.engine.SimpleEngine.popen_uci(engine_path())
    engine.configure({"Threads": os.cpu_count() or 2, "Hash": 256})
    done = lines = 0

    def add_lines(gid):
        a = analysis[gid]
        try:
            a["p"] = puzzle_lines(by_id[gid]["pgn"], a["e"], engine)
        except Exception as e:
            print("No puzzle lines for", gid, e)
            a["p"] = {}
        a["pz"] = PUZZLE_VERSION

    def out_of_time():
        return time.time() - START > MAX_MINUTES * 60

    try:
        # Games reviewed before puzzle lines existed come first; they are quick.
        for gid in need_lines:
            if out_of_time():
                break
            add_lines(gid)
            lines += 1
            if lines % 20 == 0:
                save(ANALYSIS_FILE, analysis)
        for g in todo:
            if out_of_time():
                break
            try:
                analysis[g["id"]] = {"d": DEPTH, "e": review(g["pgn"], engine)}
                add_lines(g["id"])
            except Exception as e:
                print("Skipped", g["id"], e)
                analysis[g["id"]] = {"d": DEPTH, "e": None}
            done += 1
            if done % 10 == 0:
                save(ANALYSIS_FILE, analysis)
    finally:
        engine.quit()
        save(ANALYSIS_FILE, analysis)
    print(f"Added puzzle lines to {lines} games; reviewed {done} new games; {len(todo) - done} left")


if __name__ == "__main__":
    main()
