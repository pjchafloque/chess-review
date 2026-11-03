# Chess review

Personal chess.com game review: https://pjchafloque.github.io/chess-review/

- `index.html` is the whole site.
- `.github/workflows/update.yml` runs every 3 hours. It downloads new chess.com games for `chaflox`,
  reviews them with Stockfish (`scripts/update.py`) and saves them in `data/`.
- Opening book lines (Caro-Kann and Queen's Pawn) come from the Lichess opening database
  (https://github.com/lichess-org/chess-openings, CC0).
