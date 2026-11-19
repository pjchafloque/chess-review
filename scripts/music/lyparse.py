"""Tiny LilyPond reader for the two Mutopia Gymnopédie files (public domain typesettings).
Handles \\relative, notes, chords, rests (r, R, s, pitched \\rest), ties, durations with dots,
\\repeat volta + \\alternative (unfolded), << >> with parallel parts. Everything else is skipped."""
import re, sys, json

STEPS = {'c': 0, 'd': 1, 'e': 2, 'f': 3, 'g': 4, 'a': 5, 'b': 6}
SEMI = [0, 2, 4, 5, 7, 9, 11]

def strip_braced(s, start):
    # s[start] == '{' ; return index after the matching '}'
    depth = 0
    for i in range(start, len(s)):
        if s[i] == '{': depth += 1
        elif s[i] == '}':
            depth -= 1
            if depth == 0: return i + 1
    raise ValueError("unbalanced")

def clean(src):
    src = re.sub(r'%[^\n]*', '', src)
    out, i = [], 0
    while True:
        j = src.find('\\markup', i)
        if j < 0: out.append(src[i:]); break
        out.append(src[i:j]); k = src.find('{', j)
        i = strip_braced(src, k)
    src = ''.join(out)
    src = re.sub(r'\\override\s+\S+(\s+#\'\S+)?\s*=\s*\S+', ' ', src)
    src = re.sub(r'\\bar\s+"[^"]*"', ' ', src)
    src = re.sub(r'\\barNumberCheck\s+#\d+', ' ', src)
    src = re.sub(r'\\change\s+Staff\s*=\s*\w+', ' ', src)
    src = re.sub(r'\\context\s+Voice\s*=\s*"[^"]*"', ' ', src)
    src = re.sub(r'\\context\s+Staff', ' ', src)
    src = re.sub(r'\\(dynamicUp|stemUp|stemDown|slurUp|once)\b', ' ', src)
    src = re.sub(r'\\(ppp|pp|p|mp|mf|f|ff|fff|sfz|sf)\b', ' ', src)
    src = re.sub(r'\\[<>!]', ' ', src)
    src = re.sub(r'[()|^_]', ' ', src)
    return src

def voice_src(src, name):
    m = re.search(r'\n' + name + r'\s*=\s*', src)
    k = src.find('\\relative', m.end())
    b = src.find('{', k)
    head = src[k:b]
    return head, src[b:strip_braced(src, b)]

TOK = re.compile(r"""\\relative|\\repeat|\\alternative|\\rest|volta|<<|>>|[{}<>~]|\d+\.*\*?\d*|[a-gsrR](?:isis|eses|is|es)?[',]*(?:\d+\.*)?(?:\*\d+)?""")

class Parser:
    def __init__(self, text, ref):
        self.toks = [t for t in TOK.findall(text)]
        self.i = 0
        self.last = ref           # (step, octave) absolute diatonic reference
        self.dur = 1.0            # default duration in quarter notes
    def peek(self): return self.toks[self.i] if self.i < len(self.toks) else None
    def take(self): t = self.toks[self.i]; self.i += 1; return t

    def rel(self, name, marks):
        step = STEPS[name[0]]
        ps, po = self.last
        prev = po * 7 + ps
        # candidates: same diatonic step in octaves around prev
        best = None
        for o in range(po - 2, po + 3):
            d = abs(o * 7 + step - prev)
            if d <= 3: best = o
        o = best + marks.count("'") - marks.count(",")
        self.last = (step, o)
        return (step, o)

    @staticmethod
    def midi(name, step, octave):
        alt = 0
        acc = name[1:]
        if acc.startswith('isis'): alt = 2
        elif acc.startswith('eses'): alt = -2
        elif acc.startswith('is'): alt = 1
        elif acc.startswith('es'): alt = -1
        # LilyPond octave 0 = the octave of c (one below middle c)?  c' is middle C (MIDI 60).
        return 48 + 12 * octave + SEMI[step] + alt

    def duration(self, tok):
        m = re.search(r'(\d+)(\.*)(?:\*(\d+))?$', tok)
        if not m: return self.dur
        base = 4.0 / int(m.group(1)); d = base; add = base
        for _ in m.group(2): add /= 2; d += add
        if m.group(3): d *= int(m.group(3))
        self.dur = d if not m.group(3) else base * (1 + sum(0.5 ** (k + 1) for k in range(len(m.group(2)))))
        return d

    def parse_seq(self, close='}'):
        """Returns a list of items: ('n', dur, [midi...], tie) or ('r', dur) or ('par', [parts]) or ('rep', body, alts)."""
        items = []
        while True:
            t = self.peek()
            if t is None or t == close: 
                if t == close: self.take()
                return items
            items.append(self.parse_item())

    def parse_item(self):
        t = self.take()
        if t == '{': return ('seq', self.parse_seq('}'))
        if t == '\\repeat':
            self.take(); self.take()        # volta, count
            body = self.parse_item()
            alts = []
            if self.peek() == '\\alternative':
                self.take(); assert self.take() == '{'
                while self.peek() != '}':
                    alts.append(self.parse_item())
                self.take()
            return ('rep', body, alts)
        if t == '<<':
            parts = []
            while self.peek() != '>>': parts.append(self.parse_item())
            self.take()
            return ('par', parts)
        if t == '<':
            notes = []; first = None
            save = self.last
            while self.peek() != '>':
                n = self.take(); m = re.match(r"([a-g](?:isis|eses|is|es)?)([',]*)", n)
                s, o = self.rel(m.group(1), m.group(2))
                if first is None: first = (s, o)
                notes.append(self.midi(m.group(1), s, o))
            self.take()
            self.last = first
            dt = self.peek(); d = self.duration(self.take()) if dt and re.match(r'^\d', dt) else self.dur
            tie = self.peek() == '~'
            if tie: self.take()
            return ('n', d, notes, tie)
        m = re.match(r"([a-g](?:isis|eses|is|es)?)([',]*)((?:\d+\.*)?(?:\*\d+)?)$", t)
        if m:
            s, o = self.rel(m.group(1), m.group(2))
            d = self.duration(m.group(3)) if m.group(3) else self.dur
            if self.peek() == '\\rest':
                self.take(); return ('r', d)
            tie = self.peek() == '~'
            if tie: self.take()
            return ('n', d, [self.midi(m.group(1), s, o)], tie)
        m = re.match(r"([rsR])((?:\d+\.*)?(?:\*\d+)?)$", t)
        if m:
            d = self.duration(m.group(2)) if m.group(2) else self.dur
            return ('r', d)
        raise ValueError("token " + t)

def unfold(item):
    k = item[0]
    if k == 'seq': return ('seq', [unfold(x) for x in item[1]])
    if k == 'par': return ('par', [unfold(x) for x in item[1]])
    if k == 'rep':
        body, alts = unfold(item[1]), [unfold(a) for a in item[2]]
        if not alts: return ('seq', [body, body])
        return ('seq', [body, alts[0], body, alts[1]])
    return item

def flatten(item, t, out):
    """Adds (start, dur, midi) events; returns the end time. Ties merge into the earlier note."""
    k = item[0]
    if k == 'seq':
        for x in item[1]: t = flatten(x, t, out)
        return t
    if k == 'par':
        ends = [flatten(x, t, out) for x in item[1]]
        return max(ends)
    if k == 'r': return t + item[1]
    if k == 'n':
        d, notes, tie = item[1], item[2], item[3]
        for n in notes:
            # continue a tied note that ends exactly here
            cont = [e for e in out if e['m'] == n and e.get('tie') and abs(e['s'] + e['d'] - t) < 1e-6]
            if cont: cont[-1]['d'] += d; cont[-1]['tie'] = tie
            else: out.append({'s': t, 'd': d, 'm': n, 'tie': tie})
        return t + d
    raise ValueError(k)

def read(path):
    src = open(path, encoding='utf-8').read()
    voices = {}
    for name in ('top', 'middle', 'bottom'):
        head, body = voice_src(src, name)
        m = re.search(r"\\relative\s+([a-g])([',]*)", head)
        ref = (STEPS[m.group(1)], 0 + m.group(2).count("'") - m.group(2).count(","))
        # c (no marks) = the c below middle c: octave 0 here => midi 48. c' = 60.
        p = Parser(clean(body), ref)
        assert p.take() == '{'
        tree = ('seq', p.parse_seq('}'))
        ev = []; end = flatten(unfold(tree), 0.0, ev)
        for e in ev: e.pop('tie', None)
        voices[name] = {'end': end, 'events': ev}
    return voices

if __name__ == '__main__':
    v = read(sys.argv[1])
    names = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
    nm = lambda m: names[m % 12] + str(m // 12 - 1)
    for k, x in v.items():
        evs = sorted(x['events'], key=lambda e: (e['s'], e['m']))
        print(k, 'end beats', x['end'], 'bars', x['end'] / 3, 'notes', len(evs), 'range', nm(min(e['m'] for e in evs)), nm(max(e['m'] for e in evs)))
        print('   ', ' '.join(f"{e['s']/3+1:.2f}:{nm(e['m'])}/{e['d']}" for e in evs[:40]))
    json.dump(v, open(sys.argv[2], 'w')) if len(sys.argv) > 2 else None
