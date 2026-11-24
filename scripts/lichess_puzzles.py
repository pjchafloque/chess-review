"""Sample the Lichess puzzle database for the Puzzles tab's Lichess set.

The Lichess puzzle database (https://database.lichess.org/#puzzles, CC0) lists every Lichess puzzle with
its rating. This keeps a random sample of well-tested puzzles in each 100-point rating band, so the page
can serve puzzles in the range you pick without asking Lichess for them one at a time.

Input: the database CSV (PuzzleId,FEN,Moves,Rating,RatingDeviation,Popularity,NbPlays,Themes,GameUrl,
OpeningTags) on stdin, or --file. FEN is the position before the opponent's move; Moves starts with that
move, then the solution (your moves and the replies, ending with your move).

Output: <out>/p<band>.json for every band (p1200 = ratings 1200-1299; the lowest band also holds everything
under it and the highest everything above it), each {"v": 1, "band": 1200, "month": "2026-11",
"p": [[id, fen, moves, rating, themes], ...]} sorted by rating, and <out>/index.json with the counts.

Kept: rating deviation under 100 (the rating has settled), popularity 80 or more (players voted it up,
which weeds out broken puzzles) and 200+ plays. Reservoir sampling seeded with the month: a rerun in the
same month keeps the same puzzles, next month's run brings new ones.

Usage: curl -fsSL https://database.lichess.org/lichess_db_puzzle.csv.zst | zstd -dc |
         python scripts/lichess_puzzles.py --out data/lichess
"""
import argparse
import csv
import io
import json
import os
import random
import sys
import time

LOW, HIGH = 400, 2900   # lowest and highest band


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--file", default="")              # a local CSV instead of stdin (for testing)
    ap.add_argument("--per-band", type=int, default=1000)
    ap.add_argument("--max-rd", type=int, default=100)
    ap.add_argument("--min-popularity", type=int, default=80)
    ap.add_argument("--min-plays", type=int, default=200)
    ap.add_argument("--min-rows", type=int, default=1_000_000)   # fewer means the download broke off
    ap.add_argument("--month", default=time.strftime("%Y-%m", time.gmtime()))
    a = ap.parse_args()

    src = open(a.file, newline="", encoding="utf-8") if a.file else io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8", newline="")
    rng = random.Random("lichess-puzzles " + a.month)
    keep, seen, rows = {}, {}, 0
    t0 = time.time()
    for r in csv.reader(src):
        if not r or r[0] == "PuzzleId":
            continue
        rows += 1
        try:
            pid, fen, moves, rating, rd, pop, plays, themes = r[0], r[1], r[2], int(r[3]), int(r[4]), int(r[5]), int(r[6]), r[7]
        except (IndexError, ValueError):
            continue
        if rd >= a.max_rd or pop < a.min_popularity or plays < a.min_plays or len(moves.split()) < 2:
            continue
        band = min(HIGH, max(LOW, rating // 100 * 100))
        n = seen[band] = seen.get(band, 0) + 1
        res = keep.setdefault(band, [])
        item = [pid, fen, moves, rating, themes]
        if len(res) < a.per_band:
            res.append(item)
        else:
            j = rng.randrange(n)
            if j < a.per_band:
                res[j] = item
        if rows % 1_000_000 == 0:
            print(f"{rows:,} rows, {sum(seen.values()):,} kept so far, {time.time() - t0:.0f} s", flush=True)

    if rows < a.min_rows:
        sys.exit(f"Only {rows:,} rows read: the download probably broke off. Nothing written.")

    os.makedirs(a.out, exist_ok=True)
    for f in os.listdir(a.out):
        if f.startswith("p") and f.endswith(".json"):
            os.remove(os.path.join(a.out, f))
    bands = {}
    for band in range(LOW, HIGH + 1, 100):
        res = sorted(keep.get(band, []), key=lambda x: (x[3], x[0]))
        bands[str(band)] = {"n": len(res), "pool": seen.get(band, 0)}
        with open(os.path.join(a.out, f"p{band}.json"), "w", encoding="utf-8") as fh:
            json.dump({"v": 1, "band": band, "month": a.month, "p": res}, fh, separators=(",", ":"))
    with open(os.path.join(a.out, "index.json"), "w", encoding="utf-8") as fh:
        json.dump({"v": 1, "month": a.month, "rows": rows, "low": LOW, "high": HIGH, "perBand": a.per_band,
                   "filters": {"maxRd": a.max_rd, "minPopularity": a.min_popularity, "minPlays": a.min_plays},
                   "bands": bands}, fh, separators=(",", ":"))
    print(f"{rows:,} puzzles read, {sum(seen.values()):,} passed the filters, "
          f"{sum(b['n'] for b in bands.values()):,} kept in {len(bands)} bands ({time.time() - t0:.0f} s)")
    for band, b in bands.items():
        print(f"  {band}: {b['n']} of {b['pool']:,}")


if __name__ == "__main__":
    main()
