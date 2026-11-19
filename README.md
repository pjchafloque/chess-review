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
- The Puzzles tab shows the positions from your games where exactly one move won and you missed it.
  The updater finds them with Lichess's puzzle-generator rules (https://github.com/ornicar/lichess-puzzler):
  your opponent has just made a mistake, the best move wins material or mates, and each of your moves
  in the solution is the only good one. Unlike Lichess it keeps one-move puzzles, and missed mates are
  shown as "Mate in N". The rules are written out above `puzzle_start` in `scripts/update.py`.
- `engine/` holds Stockfish.js 19 Lite (single-threaded WebAssembly, GPLv3), which the page runs in the
  browser for Retry, exploring your own moves, and games the website hasn't reviewed yet.
- Book moves come from `scripts/openings.tsv`: the Lichess opening list
  (https://github.com/lichess-org/chess-openings, CC0) merged with eco.json
  (https://github.com/hayatbiralem/eco.json, MIT).
- The Openings tab trains `data/repertoire.json`, built by `scripts/repertoire.py` the way Chessbook builds
  a book: one move of yours per position (the move you usually play when Stockfish 19 rates it within
  0.35 pawns of its best, otherwise the move most players at your level choose, otherwise Stockfish's),
  and the replies people actually play, from Lichess rapid games between players rated 1300-1800
  (`scripts/lichess_sample.py`, the Lichess database is CC0) blended with your own opponents' moves.
  It keeps positions you reach at least once in 500 games; the page cuts it at the size you pick.
  Each of your moves is a flashcard scheduled with FSRS-4.5. `.github/workflows/book.yml` rebuilds the
  book every Monday with your newest games (Stockfish results are cached in `data/repertoire-evals.json`).
  The one-line plans shown in the tab are in `scripts/repertoire-notes.tsv`.
