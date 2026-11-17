# Chess review

Personal chess.com game review: https://pjchafloque.github.io/chess-review/

- `index.html` is the whole site.
- `.github/workflows/update.yml` runs every 3 hours. It downloads new chess.com games for `chaflox`,
  reviews them with Stockfish 19 at depth 18, two best lines per position (`scripts/update.py`), and
  saves them in `data/`.
- Moves are graded like chess.com's Game Review: expected points (chance of winning, from Lichess's
  published win% curve) decide Best / Excellent / Good / Inaccuracy / Mistake / Blunder, with extra
  rules for Brilliant, Great, Miss, Book and Forced. The rules are written out above `computeGrade`
  in `index.html`.
- `engine/` holds Stockfish.js 19 Lite (single-threaded WebAssembly, GPLv3), which the page runs in the
  browser for Retry, exploring your own moves, and games the website hasn't reviewed yet.
- Book moves come from `scripts/openings.tsv`: the Lichess opening list
  (https://github.com/lichess-org/chess-openings, CC0) merged with eco.json
  (https://github.com/hayatbiralem/eco.json, MIT).
