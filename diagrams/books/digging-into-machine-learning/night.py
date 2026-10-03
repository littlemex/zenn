"""night.py: the night-panel figure format as native PowerPoint shapes, one 16:9 slide per figure.

Black board, a white title at the top left, dark rounded panels whose border colour names the group,
saturated blocks for the things inside, white arrows for order, and one white takeaway sentence under
a rule at the bottom. Coordinates are points on a 960 x 540 slide, so a font size is also in points.
"""
import os
import subprocess

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Pt
from lxml import etree

W, H = 960, 540

BG = "000000"
PANEL = "14171D"
TX = "F5F5F8"
MUTED = "96969E"
RULE = "3C3E44"
YEL = "FBD332"
GRN = "24A046"
LIME = "76B900"
BLU = "2896F0"
RED = "DC2658"
PUR = "9664DC"
GRAY = "50505A"
ORG = "EC7211"
BROWN = "3A2A1A"

FONT = "Hiragino Sans"
SOFFICE = "/opt/homebrew/bin/soffice"


def _rgb(h):
    return RGBColor.from_string(h)


def ink(fill):
    """Black text on light fills (yellow, lime), white on the others."""
    r, g, b = (int(fill[i:i + 2], 16) for i in (0, 2, 4))
    return BG if r + g + b > 500 else TX


class Board:
    def __init__(self, title, kicker=None):
        self.prs = Presentation()
        self.prs.slide_width, self.prs.slide_height = Pt(W), Pt(H)
        self.s = self.prs.slides.add_slide(self.prs.slide_layouts[6])
        bg = self.s.background.fill
        bg.solid()
        bg.fore_color.rgb = _rgb(BG)
        if kicker:
            self.text(40, 26, 880, 20, kicker, 13, MUTED)
        self.text(40, 46 if kicker else 34, 880, 34, title, 22, TX)

    def text(self, x, y, w, h, s, size=15, color=TX, align="left", anchor="middle"):
        tb = self.s.shapes.add_textbox(Pt(x), Pt(y), Pt(w), Pt(h))
        tf = tb.text_frame
        tf.word_wrap = True
        tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
        tf.vertical_anchor = {"top": MSO_ANCHOR.TOP, "middle": MSO_ANCHOR.MIDDLE,
                              "bottom": MSO_ANCHOR.BOTTOM}[anchor]
        self._fill(tf, s, size, color, align)
        return tb

    def _fill(self, tf, s, size, color, align):
        for i, line in enumerate(s.split("\n")):
            p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            p.alignment = {"left": PP_ALIGN.LEFT, "center": PP_ALIGN.CENTER,
                           "right": PP_ALIGN.RIGHT}[align]
            r = p.add_run()
            r.text = line
            f = r.font
            f.size = Pt(size)
            f.color.rgb = _rgb(color)
            f.name = FONT
            f.bold = True
            rpr = r._r.get_or_add_rPr()
            for tag in ("a:ea", "a:cs"):
                el = rpr.find(qn(tag))
                if el is None:
                    el = etree.SubElement(rpr, qn(tag))
                el.set("typeface", FONT)

    def _box(self, x, y, w, h, fill, line=None, lw=2, radius=0.08):
        shp = self.s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Pt(x), Pt(y), Pt(w), Pt(h))
        shp.adjustments[0] = radius
        shp.shadow.inherit = False
        if fill:
            shp.fill.solid()
            shp.fill.fore_color.rgb = _rgb(fill)
        else:
            shp.fill.background()
        if line:
            shp.line.color.rgb = _rgb(line)
            shp.line.width = Pt(lw)
        else:
            shp.line.fill.background()
        return shp

    def panel(self, x, y, w, h, label="", border=LIME, label_color=None):
        """A dark group with a coloured border; the label names the group in the border colour."""
        self._box(x, y, w, h, PANEL, border, 2, radius=min(0.06, 10 / min(w, h)))
        if label:
            self.text(x + 14, y + 10, w - 28, 24, label, 16, label_color or border)

    def block(self, x, y, w, h, label, fill, sub=None, size=15):
        """A saturated block with an optional smaller second line."""
        shp = self._box(x, y, w, h, fill, radius=min(0.2, 8 / min(w, h)))
        tf = shp.text_frame
        tf.word_wrap = True
        tf.margin_left = tf.margin_right = Pt(4)
        tf.margin_top = tf.margin_bottom = Pt(0)
        tf.vertical_anchor = MSO_ANCHOR.MIDDLE
        self._fill(tf, label + ("\n" + sub if sub else ""), size, ink(fill), "center")
        if sub:
            tf.paragraphs[-1].runs[0].font.size = Pt(size - 3)
        return shp

    def bar(self, x, y, w, h, fill):
        """A plain filled rectangle for charts."""
        shp = self.s.shapes.add_shape(MSO_SHAPE.RECTANGLE, Pt(x), Pt(y), Pt(w), Pt(h))
        shp.shadow.inherit = False
        shp.fill.solid()
        shp.fill.fore_color.rgb = _rgb(fill)
        shp.line.fill.background()
        return shp

    def line(self, x1, y1, x2, y2, color=TX, width=2, dashed=False, head=False):
        c = self.s.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Pt(x1), Pt(y1), Pt(x2), Pt(y2))
        c.line.color.rgb = _rgb(color)
        c.line.width = Pt(width)
        ln = c.line._get_or_add_ln()
        if dashed:
            d = etree.SubElement(ln, qn("a:prstDash"))
            d.set("val", "dash")
        if head:
            t = etree.SubElement(ln, qn("a:tailEnd"))
            t.set("type", "triangle")
            t.set("w", "med")
            t.set("len", "med")
        return c

    def arrow(self, x1, y1, x2, y2, color=TX, width=2, dashed=False):
        return self.line(x1, y1, x2, y2, color, width, dashed, head=True)

    def badge(self, x, y, n, fill=YEL, d=24):
        shp = self.s.shapes.add_shape(MSO_SHAPE.OVAL, Pt(x - d / 2), Pt(y - d / 2), Pt(d), Pt(d))
        shp.shadow.inherit = False
        shp.fill.solid()
        shp.fill.fore_color.rgb = _rgb(fill)
        shp.line.fill.background()
        tf = shp.text_frame
        tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
        tf.vertical_anchor = MSO_ANCHOR.MIDDLE
        self._fill(tf, str(n), 13, ink(fill), "center")

    def note(self, x, y, w, s, color=MUTED, size=14, align="center", h=22):
        return self.text(x, y, w, h, s, size, color, align)

    def takeaway(self, s):
        """The one sentence the figure exists to say, under a rule at the bottom."""
        self.line(40, 478, W - 40, 478, RULE, 1)
        self.text(40, 490, W - 80, 32, s, 18, TX)

    def save(self, pptx_path, png_path):
        self.prs.save(pptx_path)
        out = os.path.dirname(png_path)
        subprocess.run([SOFFICE, "--headless", "--convert-to", "pdf", "--outdir", out, pptx_path],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300)
        pdf = os.path.join(out, os.path.splitext(os.path.basename(pptx_path))[0] + ".pdf")
        stem = os.path.splitext(png_path)[0]
        subprocess.run(["pdftoppm", "-png", "-r", "144", "-singlefile", pdf, stem], check=True)
        os.remove(pdf)
