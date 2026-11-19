"""Renders a parsed piece (lyparse.py JSON) with the Salamander Grand Piano samples (Alexander Holm,
CC BY 3.0), a synthetic hall reverb and a gentle human touch. Writes a 44.1 kHz stereo WAV.

  python3 lyparse.py gymnopedie_1.ly g1.json && python3 render.py g1.json g1.wav 72 38
  python3 lyparse.py gymnopedie_3.ly g3.json && python3 render.py g3.json g3.wav 66
  ffmpeg -i g1.wav -codec:a libmp3lame -q:a 5 ../../music/gymnopedie-1.mp3   (g3: add -af volume=-1.5dB)

Arguments: piece JSON, output WAV, tempo (quarter notes a minute), bars that get a small breath before
the repeat. The samples are the .ogg files in github.com/Tonejs/audio, folder salamander (set
SALAMANDER to that folder). The .ly files are the Mutopia Project's public-domain editions
(github.com/MutopiaProject/MutopiaProject, ftp/SatieE/gymnopedie_1 and gymnopedie_3)."""
import os, sys, json, subprocess, numpy as np
from scipy.signal import resample_poly, lfilter, fftconvolve, butter, sosfilt
SR = 44100
SAMPLES = os.environ.get('SALAMANDER', '/home/claude/ext/toneaudio/salamander').rstrip('/') + '/'
NAMES = {0: 'C', 3: 'Ds', 6: 'Fs', 9: 'A'}
rng = np.random.default_rng(7)

def load(midi):
    name = NAMES[midi % 12] + str(midi // 12 - 1)
    raw = subprocess.run(['ffmpeg', '-loglevel', 'error', '-i', SAMPLES + name + '.ogg', '-f', 'f32le', '-ac', '2', '-ar', str(SR), '-'], capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.float32).reshape(-1, 2).astype(np.float64)

cache = {}
def sample_for(m):
    base = min((b for b in range(21, 109) if b % 3 == 0), key=lambda b: (abs(b - m), b))
    shift = m - base
    key = (base, shift)
    if key not in cache:
        x = cache.get((base, 0))
        if x is None: x = cache[(base, 0)] = load(base)
        if shift == 1: y = resample_poly(x, 84, 89, axis=0)
        elif shift == -1: y = resample_poly(x, 89, 84, axis=0)
        else: y = x
        cache[key] = y
    return cache[key]

def hall(seconds=3.2, rt60=2.3, seed=3):
    r = np.random.default_rng(seed); n = int(seconds * SR); t = np.arange(n) / SR
    ir = np.zeros((n, 2))
    bands = [((None, 400), rt60 * 1.15), ((400, 2500), rt60), ((2500, 8000), rt60 * 0.6), ((8000, None), rt60 * 0.35)]
    for ch in range(2):
        noise = r.standard_normal(n)
        for (lo, hi), rt in bands:
            if lo is None: sos = butter(4, hi, 'low', fs=SR, output='sos')
            elif hi is None: sos = butter(4, lo, 'high', fs=SR, output='sos')
            else: sos = butter(4, [lo, hi], 'band', fs=SR, output='sos')
            ir[:, ch] += sosfilt(sos, noise) * np.exp(-6.91 * t / rt)
        # soft onset so the tail swells in instead of cracking on
        ir[:, ch] *= np.minimum(1, t / 0.035) ** 1.5
    pre = int(0.016 * SR); ir = np.vstack([np.zeros((pre, 2)), ir])
    for d, g in [(0.007, .5), (0.013, .35), (0.021, .3), (0.029, .22), (0.041, .16)]:
        k = int(d * SR); ir[k, 0] += g * r.choice([-1, 1]); ir[k + int(0.0013 * SR), 1] += g * r.choice([-1, 1])
    return ir / np.sqrt((ir ** 2).sum() / 2)

def render(piece, out, bpm, end_rit_bars=3, rit_points=()):
    v = json.load(open(piece))
    total_beats = max(x['end'] for x in v.values())
    bars = int(round(total_beats / 3))
    # Tempo map: gentle drift per bar, breaths at phrase ends, a slower last few bars.
    spb = []
    drift = 0.0
    for b in range(bars):
        drift = 0.6 * drift + 0.4 * rng.normal(0, 0.012)
        f = 1 + drift
        if b >= bars - end_rit_bars: f *= 1 + 0.18 * ((b - (bars - end_rit_bars) + 1) / end_rit_bars) ** 1.4
        for rb in rit_points:
            if b in (rb - 1, rb): f *= 1.06 if b == rb - 1 else 1.12
        spb.append(60.0 / bpm * f)
    starts = np.concatenate([[0], np.cumsum([3 * s for s in spb])])
    def time_of(beat):
        b = min(int(beat // 3), bars - 1); return 0.4 + starts[b] + (beat - 3 * b) * spb[b]
    length = time_of(total_beats) + 6.0
    mix = np.zeros((int(length * SR) + SR, 2))
    lead = {'top': -0.012, 'middle': 0.008, 'bottom': 0.0}
    base_vel = {'top': 60, 'middle': 41, 'bottom': 52}
    for voice, x in v.items():
        for e in x['events']:
            m = e['m']; s_beat = e['s']; end_beat = e['s'] + e['d']
            # pedal: changed on each downbeat, so every note rings to the next bar line at least
            bar_end = np.ceil(end_beat / 3 - 1e-9) * 3
            t0 = time_of(s_beat) + lead[voice] + rng.normal(0, 0.006)
            if voice == 'middle': t0 += (m % 7) * 0.004            # a slight roll in the chords
            t1 = time_of(min(bar_end, total_beats)) + 0.06
            vel = base_vel[voice] + rng.normal(0, 2.5)
            if voice == 'top': vel += 0.35 * (m - 74)               # higher melody notes sing a little more
            if voice == 'bottom' and m < 40: vel -= 3
            g = 10 ** ((vel - 100) / 40)
            smp = sample_for(m)
            rel = 0.45 if voice == 'bottom' else 0.35
            n = int((t1 - t0 + rel) * SR); n = min(n, len(smp))
            seg = smp[:n].copy()
            k1 = int((t1 - t0) * SR)
            if k1 < n:
                rr = np.arange(n - k1) / SR
                seg[k1:] *= np.exp(-rr / (rel / 4.6))[:, None]
            # softer notes are darker
            fc = 1800 + 55 * vel
            a = np.exp(-2 * np.pi * fc / SR)
            seg = lfilter([1 - a], [1, -a], seg, axis=0)
            i0 = int(t0 * SR)
            mix[i0:i0 + n] += g * seg
    ir = hall()
    wet = np.stack([fftconvolve(mix[:, c], ir[:, c])[:len(mix)] for c in range(2)], 1)
    outp = 0.88 * mix + 0.42 * wet * (np.sqrt((mix ** 2).mean()) / max(1e-9, np.sqrt((wet ** 2).mean())))
    # trim the end: let the hall fade, then 1 s of quiet
    last = time_of(total_beats)
    cut = int((last + 4.5) * SR); outp = outp[:cut]
    fade = int(3.0 * SR); outp[-fade:] *= np.linspace(1, 0, fade)[:, None] ** 2
    outp /= np.abs(outp).max() / 0.89
    import scipy.io.wavfile as w
    w.write(out, SR, (outp * 32767).astype(np.int16))
    print(out, f"{len(outp)/SR:.1f}s", 'rms dBFS', f"{20*np.log10(np.sqrt((outp**2).mean())):.1f}")

if __name__ == '__main__':
    render(sys.argv[1], sys.argv[2], float(sys.argv[3]), rit_points=tuple(int(x) for x in sys.argv[4:]))
