# Credits and licenses

PChess is built on other people's work. This page lists what it uses, who made it, and under what license.

## Served to visitors

- **Stockfish 19** by the Stockfish developers (https://stockfishchess.org), GPLv3. The browser copy in
  `engine/` is Stockfish.js 19.0.1, the "lite, single-threaded" build by Nathan Rugg for Chess.com,
  unchanged from https://github.com/nmrugg/stockfish.js/releases/tag/v19.0.1, where its source code is.
  Its neural network, nn-61e7af4bb97d, is by Chris Bao. The full GPLv3 text is in `engine/COPYING.txt`.
- **chess.js 0.10.3** by Jeff Hlywa (https://github.com/jhlywa/chess.js), BSD-2-Clause. Served from
  `scripts/vendor/chess.js` with its copyright notice and license at the top of the file.
- **Chess pieces**: the "cburnett" set by Colin M.L. Burnett, from Wikimedia Commons
  (https://commons.wikimedia.org/wiki/Category:SVG_chess_pieces), which he released under the GFDL, BSD
  and GPL licenses. Lichess uses the same set.
- **Inter** typeface by Rasmus Andersson (https://rsms.me/inter/), SIL Open Font License 1.1, loaded from
  Google Fonts. Apple devices show their own system font instead.
- **Background music** in `music/`: Erik Satie's Gymnopédies No. 1 and No. 3 (1888, public domain), from
  the Mutopia Project's public-domain editions typeset by Evin Robertson (https://www.mutopiaproject.org),
  played on the Salamander Grand Piano V2 samples by Alexander Holm, CC BY 3.0
  (https://creativecommons.org/licenses/by/3.0/), taken from https://github.com/Tonejs/audio. The
  recordings were rendered for this site and are CC BY 3.0 too. Details in `music/README.md`.
- The move sounds are synthesized in the page. Lichess's sound files are not used.

## Data

- **Opening names** (`scripts/openings.tsv`): the Lichess opening list
  (https://github.com/lichess-org/chess-openings, CC0) merged with eco.json
  (https://github.com/hayatbiralem/eco.json, MIT, license text below).
- **Lichess puzzles** (`data/lichess/`): sampled from the Lichess puzzle database
  (https://database.lichess.org/#puzzles, CC0).
- **Opponents' moves in the Openings book** (`data/lichess-sample.json`): sampled from the Lichess game
  database (https://database.lichess.org, CC0).
- **Your games**: downloaded from chess.com's public API (https://www.chess.com/news/view/published-data-api).
- The example game is Morphy's "Opera Game" (Paris, 1858).

## Rules and formulas adapted from other projects

- **Lichess puzzle generator and tagger** (https://github.com/ornicar/lichess-puzzler, AGPL-3.0 or later).
  The puzzle rules in `scripts/update.py` follow `generator/generator.py`, and the tactic patterns the coach
  names (fork, pin, skewer, discovered attack, trapped piece and the rest) follow `tagger/cook.py`. Both
  were rewritten for this site, with the changes described in the README and the Puzzles tab.
- **Lichess** (https://github.com/lichess-org/lila, AGPL-3.0 or later): the win% curve used for move
  grades and the per-move accuracy formula.
- **Chessground** (https://github.com/lichess-org/chessground, GPL-3.0 or later): the way pieces are matched
  up so they slide when you step through a game.
- **FSRS-4.5** (https://github.com/open-spaced-repetition/fsrs4anki, MIT, Copyright (c) 2022
  open-spaced-repetition): the default parameters that schedule the Openings flashcards.
- **Chessbook** (https://www.chessbook.com) and **chess.com's Game Review**: the ideas behind the Openings
  book and the move labels. No code from either.
- **Design**: the look follows the Apple design notes in VoltAgent/awesome-design-md
  (https://github.com/VoltAgent/awesome-design-md, `design-md/apple/DESIGN.md`).

## Used only by the update scripts (not served to visitors)

- The official Stockfish 19 Linux binary (https://github.com/official-stockfish/Stockfish, GPLv3), run on
  GitHub Actions for the website's own reviews.
- python-chess by Niklas Fiekas (https://github.com/niklasf/python-chess, GPL-3.0 or later).

## eco.json license

```
MIT License

Copyright (c) 2017 Ömür Yanıkoğlu

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
