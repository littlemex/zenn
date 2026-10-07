"""gen_cover.py -- book「Amazon EKS Distributed AI ワークショップ」のカバー (1000 x 1400、縦横比 5:7)。

    /usr/bin/python3 gen_cover.py <out.png>

元のカバーの構図（下から Kubernetes、インフラ、分散 AI の 3 層が 1 本の光の軸でつながる）を
残しつつ、黒地に緑のグロー・ノードグリッドで描き直す。ロゴは入れない (ZN-24)。
"""
import math
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageChops

OUT = sys.argv[1] if len(sys.argv) > 1 else "cover.png"
W, H = 1000, 1400
SC = 3  # 高解像度で描いてから縮小する
BG = (6, 9, 8)
GRN = (59, 209, 111)
GRN_HI = (140, 255, 180)
GRN_DK = (18, 64, 34)
CHIP = (14, 22, 18)
EDGE = (40, 70, 52)
WHITE = (245, 245, 248)
MUTED = (150, 160, 155)
EN = "/Library/Fonts/AmazonEmber_Bd.ttf"
EN_RG = "/Library/Fonts/AmazonEmber_Rg.ttf"


def font(path, n):
    try:
        return ImageFont.truetype(path, n)
    except OSError:
        return ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", n)


def P(x, y):
    return (x * SC, y * SC)


img = Image.new("RGB", (W * SC, H * SC), BG)

def bg_dots():
    cell = 70
    for gx in range(0, W + cell, cell):
        for gy in range(0, 1180 + cell, cell):
            if (gx // cell + gy // cell) % 2:
                continue
            d.ellipse([P(gx - 2, gy - 2), P(gx + 2, gy + 2)], fill=(16, 26, 20))

glow = Image.new("RGB", (W * SC, H * SC), (0, 0, 0))
g = ImageDraw.Draw(glow)
d = ImageDraw.Draw(img)

CX = W / 2
bg_dots()

# ------------------------------------------------------------- 中心の光の軸 (下から上へ)
AXIS_TOP, AXIS_BOT = 230, 1120
g.line([P(CX, AXIS_TOP), P(CX, AXIS_BOT)], fill=GRN, width=int(14 * SC))
d.line([P(CX, AXIS_TOP), P(CX, AXIS_BOT)], fill=GRN, width=int(3 * SC))

# ------------------------------------------------------------- 軸の上を流れる矢じり (層のつながりを示す)
for cy in (980, 870, 760, 560, 480, 400):
    w = 16
    d.line([P(CX - w, cy + 10), P(CX, cy - 6)], fill=GRN, width=int(3 * SC))
    d.line([P(CX + w, cy + 10), P(CX, cy - 6)], fill=GRN, width=int(3 * SC))

# ------------------------------------------------------------- 下段: Kubernetes (ハニカム + ホイール)
HEX_Y = 1060
HEX_R = 150


def hexagon(cx, cy, r):
    return [(cx + r * math.cos(math.radians(60 * k - 30)), cy + r * math.sin(math.radians(60 * k - 30)))
            for k in range(6)]


# 外側のハニカム 6 枚
for k in range(6):
    ang = math.radians(60 * k)
    hx = CX + HEX_R * 1.15 * math.cos(ang)
    hy = HEX_Y + HEX_R * 1.15 * math.sin(ang) * 0.72
    pts = hexagon(hx, hy, HEX_R * 0.62)
    d.polygon([P(*p) for p in pts], fill=CHIP, outline=EDGE, width=int(2.5 * SC))
# 中心の光るハニカム
pts = hexagon(CX, HEX_Y, HEX_R)
g.polygon([P(*p) for p in pts], fill=GRN)
d.polygon([P(*p) for p in pts], fill=GRN_DK, outline=GRN, width=int(4 * SC))
# K8s のホイール (ハンドルの付いた輪)
WR = 72
d.ellipse([P(CX - WR, HEX_Y - WR), P(CX + WR, HEX_Y + WR)], outline=WHITE, width=int(9 * SC))
d.ellipse([P(CX - 16, HEX_Y - 16), P(CX + 16, HEX_Y + 16)], fill=WHITE)
for k in range(7):
    ang = math.radians(360 / 7 * k - 90)
    x1, y1 = CX + 30 * math.cos(ang), HEX_Y + 30 * math.sin(ang)
    x2, y2 = CX + WR * math.cos(ang), HEX_Y + WR * math.sin(ang)
    d.line([P(x1, y1), P(x2, y2)], fill=WHITE, width=int(7 * SC))

# ------------------------------------------------------------- 中段: インフラ (チップ + ノードの格子)
MID_Y = 700
CW, CH = 260, 150
# サーバーのスタック (3 段、奥行きを少しずらす)
for i, off in enumerate((26, 13, 0)):
    y = MID_Y - 100 + off * 0.9
    x = CX - CW / 2 - off * 0.9
    lit = i == 2
    d.rounded_rectangle([P(x, y), P(x + CW, y + CH * 0.42)], radius=12,
                        fill=GRN_DK if lit else CHIP, outline=GRN if lit else EDGE, width=int(4 * SC))
    for j in range(6):
        cx = x + 24 + j * 36
        d.rectangle([P(cx, y + 26), P(cx + 20, y + 40)], fill=GRN if lit else EDGE)
# 左右に伸びるノードの格子 (アクセラレータが相互につながる様子)
NY = MID_Y + 70
offsets = [(-300, -30), (-210, 40), (-115, 0), (115, 0), (210, 40), (300, -30)]
for ox, oy in offsets:
    x, y = CX + ox, NY + oy
    g.line([P(CX, NY - 10), P(x, y)], fill=GRN, width=int(3 * SC))
    d.line([P(CX, NY - 10), P(x, y)], fill=(26, 60, 40), width=int(2 * SC))
for ox, oy in offsets:
    x, y = CX + ox, NY + oy
    r = 20
    d.rounded_rectangle([P(x - r, y - r), P(x + r, y + r)], radius=5, fill=GRN_DK, outline=GRN, width=int(3 * SC))
    d.rectangle([P(x - 7, y - 7), P(x + 7, y + 7)], fill=GRN)

# ------------------------------------------------------------- 上段: 分散 AI (つながるノードのクラスタ)
TOP_Y = 340
nodes = [(0, -70), (-110, 10), (110, 10), (-70, 110), (70, 110), (0, 40)]
edges = [(0, 1), (0, 2), (1, 3), (2, 4), (0, 5), (5, 3), (5, 4), (1, 5), (2, 5)]
pts = [(CX + ox, TOP_Y + oy) for ox, oy in nodes]
for a, b in edges:
    g.line([P(*pts[a]), P(*pts[b])], fill=GRN, width=int(4 * SC))
    d.line([P(*pts[a]), P(*pts[b])], fill=(26, 60, 40), width=int(2 * SC))
for i, (x, y) in enumerate(pts):
    r = 30 if i == 0 else 20
    d.ellipse([P(x - r, y - r), P(x + r, y + r)], fill=GRN_DK, outline=GRN, width=int(4 * SC))
    d.ellipse([P(x - r * 0.4, y - r * 0.4), P(x + r * 0.4, y + r * 0.4)], fill=GRN_HI if i == 0 else GRN)
# 軸の最上部からクラスタへの接続
g.line([P(CX, AXIS_TOP), P(*pts[5])], fill=GRN, width=int(5 * SC))
d.line([P(CX, AXIS_TOP), P(*pts[5])], fill=GRN, width=int(3 * SC))

# ------------------------------------------------------------- グローを合成
glow_b = glow.resize((W * SC // 2, H * SC // 2)).filter(ImageFilter.GaussianBlur(18)).resize((W * SC, H * SC))
img = ImageChops.add(img, glow_b)
img = img.resize((W, H), Image.LANCZOS)
d = ImageDraw.Draw(img)

# 下地を少し暗くフェードさせ、タイトル帯を作る
fade = Image.new("L", (W, H), 0)
fd = ImageDraw.Draw(fade)
for yy in range(H):
    v = 0 if yy < 1150 else min(235, int((yy - 1150) * 1.9))
    fd.line([(0, yy), (W, yy)], fill=v)
img = Image.composite(Image.new("RGB", (W, H), BG), img, fade)
d = ImageDraw.Draw(img)

# ------------------------------------------------------------- タイトル
d.text((90, 1178), "EKS Distributed AI", font=font(EN, 70), fill=WHITE)
d.text((94, 1258), "Workshop", font=font(EN, 70), fill=GRN)
d.line([(96, 1348), (380, 1348)], fill=GRN, width=4)
d.text((96, 1358), "Kubernetes -> Infra -> Distributed AI", font=font(EN_RG, 26), fill=MUTED)

img.save(OUT, optimize=True)
print(OUT, img.size)
