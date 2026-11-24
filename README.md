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
  shown as "Mate in N". A second set, Defend, has the positions where your opponent threatened
  something and exactly one move held (plain recaptures don't count). The rules are written out above
  `puzzle_start` in `scripts/update.py`.
- The Puzzles tab's third set, Lichess, serves puzzles from the Lichess puzzle database
  (https://database.lichess.org/#puzzles, CC0) in the rating range you pick, untimed (Practice, with a stopwatch)
  or as 1, 3 or 5-minute runs that keep your best score for each clock and range.
  `.github/workflows/lichess-puzzles.yml` samples about 1,000 well-tested puzzles per 100 rating points every
  month (`scripts/lichess_puzzles.py`) into `data/lichess/`; the page loads only the bands you pick.
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
- Explanations: the coach explains every inaccuracy, mistake and blunder in words worked out from the
  Stockfish lines and checked on the board (what your move allowed, what the better move does, and the
  pattern when there is one: fork, pin, skewer, discovered attack, hanging or trapped piece, removing
  the defender, back-rank or smothered mate, promotion). The pattern definitions follow Lichess's
  puzzle tagger (https://github.com/ornicar/lichess-puzzler, `tagger/cook.py`). Code: the
  "move explanations" section of `index.html`.
- Sounds: the move sounds are synthesized in the page (no files). The background music in `music/` is
  Erik Satie's Gymnopédie No. 1 and No. 3 (1888, public domain), from the Mutopia Project's
  public-domain editions typeset by Evin Robertson, played on the Salamander Grand Piano samples by
  Alexander Holm (CC BY 3.0, https://creativecommons.org/licenses/by/3.0/) and rendered by
  `scripts/music/`. See `music/README.md`.
- Opening a game shows a summary first (result, both accuracies, the move where it turned). Start review
  goes to the last book move and the coach's Next key moment button takes it from there, like chess.com.
- The page checks chess.com for new games itself whenever you open it or come back to it, adds them
  with a dot in the game list, opens the newest one if you haven't picked a game, and reviews it with
  Stockfish in the browser until the 3-hourly update brings the deeper review and its puzzles.
- Moving pieces: tap a piece and then a square, or press and drag. Pieces slide when you step through a
  game (not with Reduce Motion on), and a pawn reaching the last rank asks which piece it becomes.
- Insights: wins you let slip (games where you were +6 or better and didn't win, each with Play it out:
  that position against Stockfish at the strength you pick), where your blunders happen by move number,
  your score with each opening, and the tactics you miss and walk into most. The tactics come from
  `data/tactics.json`, which `scripts/tactics.js` builds after each update with the page's own
  explanation code (chess.js 0.10.3, BSD-2-Clause, is vendored in `scripts/vendor/` for it).
