# Engine

`stockfish-19-lite-single.js` and `stockfish-19-lite-single.wasm` are Stockfish.js 19.0.1, the
"lite, single-threaded" build by Nathan Rugg for Chess.com, unchanged from
https://github.com/nmrugg/stockfish.js/releases/tag/v19.0.1 (source code there).

- Stockfish 19 with a small network (nn-61e7af4bb97d by Chris Bao), about 1.8 MB.
- Single-threaded WebAssembly, so it runs on GitHub Pages without cross-origin isolation headers.
- License: GPLv3 (https://github.com/nmrugg/stockfish.js/blob/master/Copying.txt).

The page runs it in a Web Worker for games the website hasn't reviewed yet, for Retry and for exploring
your own moves. The website's own reviews (`scripts/update.py` on GitHub Actions) use the full native
Stockfish 19 at depth 18.
