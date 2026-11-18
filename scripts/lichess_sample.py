"""Count what Lichess players at the user's level play in his two openings.

Streams the start of a monthly Lichess database dump (https://database.lichess.org, CC0) through
zstd, keeps rated rapid games where both players are in a rating band, and counts every move
sequence after the start of each repertoire:

  b  Black, Caro-Kann: games that start 1.e4 c6 (keys are the moves after 1.e4 c6)
  w  White, 1.d4:      games that start 1.d4     (keys are the moves after 1.d4)

Output (JSON): {"meta": {...}, "b": {"root": games, "counts": {"d4": n, "d4 d5": n, ...}}, "w": {...}}

Nothing here needs a Lichess account: the database files are public downloads. The opening
explorer API would need a token, so it isn't used.

Usage: python scripts/lichess_sample.py --out sample.json [--months 2026-10,2026-09] (default: the last three months)
       [--min-elo 1300 --max-elo 1800 --speeds Rapid --minutes 25 --b-games 40000 --w-games 150000]
"""
import argparse
import json
import re
import subprocess
import sys
import time

COMMENT = re.compile(rb"\{[^}]*\}")
MOVENUM = re.compile(rb"\d+\.(?:\.\.)?")
RESULTS = {b"1-0", b"0-1", b"1/2-1/2", b"*"}


def tokens(movetext: bytes, limit: int):
    t = MOVENUM.sub(b" ", COMMENT.sub(b" ", movetext)).split()
    out = []
    for x in t:
        if x in RESULTS:
            break
        out.append(x.rstrip(b"?!").decode())
        if len(out) >= limit:
            break
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--months", default="")
    ap.add_argument("--min-elo", type=int, default=1300)
    ap.add_argument("--max-elo", type=int, default=1800)
    ap.add_argument("--speeds", default="Rapid")
    ap.add_argument("--minutes", type=float, default=25)
    ap.add_argument("--b-games", type=int, default=40000)
    ap.add_argument("--w-games", type=int, default=150000)
    ap.add_argument("--plies", type=int, default=26)      # moves kept after the root
    ap.add_argument("--file", default="")                 # local .pgn or .pgn.zst instead of downloading (for testing)
    a = ap.parse_args()

    if not a.months:   # the last three finished months (a month's file appears early the next month)
        t = time.gmtime(); y, m = t.tm_year, t.tm_mon
        months = []
        for _ in range(3):
            m -= 1
            if m == 0:
                y, m = y - 1, 12
            months.append("%04d-%02d" % (y, m))
        a.months = ",".join(months)
    speeds = [s.strip().encode() for s in a.speeds.split(",") if s.strip()]
    want = [b"Rated " + s for s in speeds]
    roots = {"b": ["e4", "c6"], "w": ["d4"]}
    goal = {"b": a.b_games, "w": a.w_games}
    counts = {"b": {}, "w": {}}
    root = {"b": 0, "w": 0}
    t0 = time.time()
    seen = kept = 0
    month_used = None

    for month in ([a.file] if a.file else [m.strip() for m in a.months.split(",") if m.strip()]):
        url = "https://database.lichess.org/standard/lichess_db_standard_rated_%s.pgn.zst" % month
        print("Reading", a.file or url, flush=True)
        curl = subprocess.Popen(["cat", a.file] if a.file else ["curl", "-fsSL", "--retry", "3", url], stdout=subprocess.PIPE)
        zstd = subprocess.Popen(["cat"] if a.file.endswith(".pgn") else ["zstd", "-dc"], stdin=curl.stdout, stdout=subprocess.PIPE, bufsize=1 << 20)
        curl.stdout.close()
        ev = welo = belo = None
        got_any = False
        try:
            for line in zstd.stdout:
                got_any = True
                if line[:1] == b"[":
                    if line.startswith(b"[Event "):
                        ev = line; welo = belo = None
                    elif line.startswith(b"[WhiteElo "):
                        try: welo = int(line[11:-3])
                        except ValueError: welo = None
                    elif line.startswith(b"[BlackElo "):
                        try: belo = int(line[11:-3])
                        except ValueError: belo = None
                    continue
                if len(line) < 3:
                    continue
                # the move text of one game
                seen += 1
                if ev is None or welo is None or belo is None:
                    continue
                if not any(w in ev for w in want):
                    continue
                if not (a.min_elo <= welo <= a.max_elo and a.min_elo <= belo <= a.max_elo):
                    continue
                if not line.startswith(b"1. e4") and not line.startswith(b"1. d4"):
                    continue
                t = tokens(line, a.plies + 2)
                for side in ("b", "w"):
                    r = roots[side]
                    if t[:len(r)] != r or root[side] >= goal[side]:
                        continue
                    root[side] += 1; kept += 1
                    c = counts[side]
                    rest = t[len(r):len(r) + a.plies]
                    key = ""
                    for san in rest:
                        key = san if not key else key + " " + san
                        c[key] = c.get(key, 0) + 1
                    # Lossy counting: drop rare long sequences now and then so memory stays small.
                    if root[side] % 20000 == 0:
                        for k in [k for k, v in c.items() if v <= 2 and k.count(" ") >= 7]:
                            del c[k]
                if seen % 500000 == 0:
                    el = time.time() - t0
                    print("%.0fs  %d games read, kept b=%d w=%d, keys b=%d w=%d" % (el, seen, root["b"], root["w"], len(counts["b"]), len(counts["w"])), flush=True)
                if all(root[s] >= goal[s] for s in root) or time.time() - t0 > a.minutes * 60:
                    break
        finally:
            zstd.kill(); curl.kill()
            zstd.wait(); curl.wait()
        if got_any:
            month_used = month_used or month
        if all(root[s] >= goal[s] for s in root) or time.time() - t0 > a.minutes * 60:
            break

    out = {"meta": {"source": "Lichess database (CC0), rated " + a.speeds + " games",
                    "month": month_used, "minElo": a.min_elo, "maxElo": a.max_elo,
                    "gamesRead": seen, "seconds": round(time.time() - t0)}}
    for side in ("b", "w"):
        c = {k: v for k, v in counts[side].items() if v >= 3}
        out[side] = {"root": root[side], "counts": c}
        print(side, "games", root[side], "sequences kept", len(c), flush=True)
    with open(a.out, "w") as f:
        json.dump(out, f, separators=(",", ":"))


if __name__ == "__main__":
    main()
