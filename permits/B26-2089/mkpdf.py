#!/usr/bin/env python3
"""Minimal dependency-free PDF writer (standard-14 fonts, wrapped text)."""
import zlib

W_REG = {32:278,33:278,34:355,35:556,36:556,37:889,38:667,39:191,40:333,41:333,42:389,43:584,
44:278,45:333,46:278,47:278,58:278,59:278,60:584,61:584,62:584,63:556,64:1015,
91:278,92:278,93:278,94:469,95:556,96:333,123:334,124:260,125:334,126:584}
for c in range(48,58): W_REG[c]=556
for c,w in zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ",[667,667,722,722,667,611,778,722,278,500,667,556,833,722,778,667,778,722,667,611,722,667,944,667,667,611]): W_REG[ord(c)]=w
for c,w in zip("abcdefghijklmnopqrstuvwxyz",[556,556,500,556,556,278,556,556,222,222,500,222,833,556,556,556,556,333,500,278,556,500,722,500,500,500]): W_REG[ord(c)]=w

W_BLD = {32:278,33:333,34:474,35:556,36:556,37:889,38:722,39:238,40:333,41:333,42:389,43:584,
44:278,45:333,46:278,47:278,58:333,59:333,60:584,61:584,62:584,63:611,64:975,
91:333,92:278,93:333,94:584,95:556,96:333,123:389,124:280,125:389,126:584}
for c in range(48,58): W_BLD[c]=556
for c,w in zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ",[722,722,722,722,667,611,778,722,278,556,722,611,833,722,778,667,778,722,667,611,722,667,944,667,667,611]): W_BLD[ord(c)]=w
for c,w in zip("abcdefghijklmnopqrstuvwxyz",[556,611,556,611,556,333,611,611,278,278,556,278,889,611,611,611,611,389,556,333,611,556,778,556,556,500]): W_BLD[ord(c)]=w

PAGE_W, PAGE_H = 612.0, 792.0
ML, MR, MT, MB = 66.0, 66.0, 62.0, 62.0
BODY_W = PAGE_W - ML - MR

def width(txt, size, bold):
    t = W_BLD if bold else W_REG
    return sum(t.get(ord(ch), 556) for ch in txt) * size / 1000.0

def wrap(txt, size, bold, maxw):
    words, lines, cur = txt.split(), [], ""
    for w in words:
        trial = w if not cur else cur + " " + w
        if width(trial, size, bold) <= maxw:
            cur = trial
        else:
            if cur: lines.append(cur)
            cur = w
    if cur: lines.append(cur)
    return lines or [""]

def esc(s):
    return s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")

class Doc:
    def __init__(self):
        self.pages, self.ops, self.y = [], [], PAGE_H - MT
        self.pageno = 1
    def _newpage(self):
        self.pages.append("\n".join(self.ops))
        self.ops, self.y = [], PAGE_H - MT
        self.pageno += 1
    def space(self, h):
        self.y -= h
    def need(self, h):
        if self.y - h < MB: self._newpage()
    def text(self, s, size=10.5, bold=False, indent=0.0, lead=None, after=0.0, maxw=None):
        lead = lead or size * 1.42
        maxw = maxw if maxw is not None else BODY_W - indent
        for ln in wrap(s, size, bold, maxw):
            self.need(lead)
            self.ops.append("BT /%s %.1f Tf %.1f %.1f Td (%s) Tj ET" %
                            ("F2" if bold else "F1", size, ML + indent, self.y - size, esc(ln)))
            self.y -= lead
        self.y -= after
    def bullet(self, mark, s, size=10.5, indent=14.0, gap=16.0, after=2.0, bold_mark=False):
        lead = size * 1.42
        self.need(lead)
        self.ops.append("BT /%s %.1f Tf %.1f %.1f Td (%s) Tj ET" %
                        ("F2" if bold_mark else "F1", size, ML + indent, self.y - size, esc(mark)))
        self.text(s, size=size, indent=indent + gap, after=after)
    def rule(self, pad=6.0, w=0.7):
        self.need(pad * 2)
        self.y -= pad
        self.ops.append("%.2f w %.1f %.1f m %.1f %.1f l S" % (w, ML, self.y, PAGE_W - MR, self.y))
        self.y -= pad
    def heading(self, s, size=11.5, pre=13.0):
        self.need(pre + size * 2.4)
        self.y -= pre
        self.text(s, size=size, bold=True, after=4.0)
    def finish(self, path, footer=""):
        self.pages.append("\n".join(self.ops))
        total = len(self.pages)
        objs, streams = [], []
        for i, content in enumerate(self.pages, 1):
            if footer:
                f = "%s   |   Page %d of %d" % (footer, i, total)
                content += "\nBT /F1 8.0 Tf %.1f %.1f Td (%s) Tj ET" % (ML, MB - 22, esc(f))
            streams.append(zlib.compress(content.encode("latin-1")))
        n_pages = total
        # obj ids: 1 catalog, 2 pages, 3 F1, 4 F2, 5..  page objs, then contents
        page_ids = [5 + i for i in range(n_pages)]
        cont_ids = [5 + n_pages + i for i in range(n_pages)]
        objs.append((1, b"<< /Type /Catalog /Pages 2 0 R >>"))
        kids = " ".join("%d 0 R" % p for p in page_ids)
        objs.append((2, ("<< /Type /Pages /Count %d /Kids [%s] >>" % (n_pages, kids)).encode()))
        objs.append((3, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"))
        objs.append((4, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>"))
        for pid, cid in zip(page_ids, cont_ids):
            objs.append((pid, ("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %.0f %.0f] "
                               "/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents %d 0 R >>"
                               % (PAGE_W, PAGE_H, cid)).encode()))
        for cid, st in zip(cont_ids, streams):
            objs.append((cid, b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(st) + st + b"\nendstream"))
        objs.sort()
        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = {}
        for num, body in objs:
            offsets[num] = len(out)
            out += b"%d 0 obj\n" % num + body + b"\nendobj\n"
        xref = len(out)
        maxn = max(offsets) + 1
        out += b"xref\n0 %d\n0000000000 65535 f \n" % maxn
        for n in range(1, maxn):
            out += b"%010d 00000 n \n" % offsets.get(n, 0)
        out += (b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (maxn, xref))
        open(path, "wb").write(bytes(out))
        return len(out), total
