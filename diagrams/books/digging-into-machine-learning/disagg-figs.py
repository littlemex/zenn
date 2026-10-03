"""Figures for the disaggregated-serving chapters. Each figure is one pptx beside its PNG.

    python3 disagg-figs.py            # every figure
    python3 disagg-figs.py dq-fig1    # figures whose name starts with the argument
"""
import os
import sys

from night import (BG, BLU, BROWN, GRAY, GRN, LIME, MUTED, ORG, PUR, RED, TX, YEL, Board)

HERE = os.path.dirname(os.path.abspath(__file__))
IMAGES = os.path.join(HERE, "..", "..", "..", "images", "books", "digging-into-machine-learning")
FIGS = {}


def fig(name):
    def wrap(fn):
        FIGS[name] = fn
        return fn
    return wrap


@fig("dq-fig1-two-roles")
def two_roles():
    b = Board("読む係と書く係を同じ機械でやるか、分けるか")
    b.panel(40, 90, 430, 360, "統合: 1 台が両方を兼ねる", GRAY, TX)
    x = 70
    for i, (w, kind) in enumerate([(26, "d"), (26, "d"), (110, "p"), (26, "d"), (26, "d"), (110, "p"), (26, "d")]):
        if kind == "p":
            b.block(x, 170, w, 50, "読む", ORG)
        else:
            b.block(x, 170, w, 50, "", BLU)
        x += w + 4
    b.note(60, 240, 390, "青 = 書く 1 歩、橙 = 読む係の計算")
    b.block(90, 300, 330, 56, "橙の間、青は進まない", RED, "1 トークンの間隔が伸びる")

    b.panel(490, 90, 430, 360, "分離: 役割ごとに機械を分ける", LIME)
    b.block(520, 160, 150, 60, "読む係の機械", ORG, "入力をまとめて計算")
    b.block(740, 160, 150, 60, "書く係の機械", BLU, "割り込みがない")
    b.arrow(672, 190, 736, 190)
    b.note(660, 140, 90, "KV を運ぶ", YEL, 13)
    x = 520
    for i in range(12):
        b.block(x, 250, 26, 40, "", BLU)
        x += 31
    b.note(510, 300, 400, "書く 1 歩が等間隔に並ぶ")
    b.block(540, 340, 330, 56, "書く係の相席を 1 台に集める", GRN)
    b.takeaway("分離は割り込みを消し、代わりに運ぶ代金と役割で固定される台数を払う")
    return b


@fig("dq-fig2-split-bill")
def split_bill():
    b = Board("書く係の代金 w_d(B) = A / B + C、A は割り勘、C は各自")
    for px, label, border, a, c, ex in [
        (40, "重み読みが支配する", LIME, 190, 20, "例: 重みが大きく文脈が短い"),
        (490, "KV 読みが支配する", ORG, 40, 170, "例: 文脈が長い"),
    ]:
        b.panel(px, 90, 430, 360, label, border)
        b.note(px + 20, 124, 390, ex, MUTED, 14, "left")
        base = 410
        for i, (bb, xx) in enumerate([(1, px + 90), (4, px + 250)]):
            ha = a / bb
            b.bar(xx, base - c - ha, 90, ha, GRN)
            b.bar(xx, base - c, 90, c, BLU)
            b.note(xx - 20, base + 8, 130, f"相席 B = {bb}", TX, 15)
        b.note(px + 20, 150, 200, "緑 = A / B (割り勘)", GRN, 14, "left")
        b.note(px + 220, 150, 200, "青 = C (自分の KV)", BLU, 14, "left")
    b.takeaway("相席を増やすと重み読みの分が下がる。文脈が長いと C が大きく残る")
    return b


@fig("dq-fig3-ceiling")
def ceiling():
    b = Board("2 段に分ける得の天井 (P + D) / max(P, D)", "統合エンジン 1 台に対する、読む係 1 台と書く係 1 台の比 (L40S、Qwen2.5-1.5B の P と D から計算)")
    b.panel(40, 80, 880, 380, "", GRAY)
    x0, base, top = 140, 400, 120
    scale = (base - top) / 1.0
    b.line(100, base, 880, base, MUTED, 1)
    for v in (1.0, 1.5, 2.0):
        y = base - (v - 1.0) * scale
        b.line(100, y, 880, y, "2A2C32", 1)
        b.note(46, y - 11, 50, f"{v:.1f}", MUTED, 13, "right")
    rows = [("8 対 1", "ふつうの会話", 1.012, ORG), ("1000 対 1", "釣り合いに近い", 1.670, GRN),
            ("8000 対 1", "", 1.084, ORG), ("1 万対 1", "入力 12 万", 1.067, ORG)]
    for i, (lab, sub, v, col) in enumerate(rows):
        x = x0 + i * 185
        h = max(4, (v - 1.0) * scale)
        b.bar(x, base - h, 110, h, col)
        b.note(x - 20, base - h - 30, 150, f"{v:.3f} 倍", TX, 16)
        b.note(x - 30, base + 6, 170, lab, TX, 15)
        if sub:
            b.note(x - 30, base + 28, 170, sub, MUTED, 13)
    b.note(560, 150, 320, "入力 : 出力の比", MUTED, 14, "right")
    b.takeaway("読む時間と書く時間が釣り合うほど天井は高く、どちらかが支配すると 1 に近づく")
    return b



@fig("dt-fig1-three-rulers")
def three_rulers():
    b = Board("同じ応答を 3 つの物差しで測る (約束は TPOT 30 ms)")
    b.panel(40, 82, 880, 120, "1 つの応答の、トークンとトークンの間隔", GRAY, TX)
    x = 64
    for i in range(52):
        tall = i in (9, 23, 40)
        h = 56 if tall else 10
        b.bar(x, 186 - h, 11, h, RED if tall else GRN)
        x += 16
    b.note(640, 112, 260, "赤 = 約 408 ms の詰まり", RED, 14, "right")
    for i, (lab, v, verdict, col) in enumerate([
        ("応答の平均間隔", "32.7 ms", "わずかに超える程度に見える", YEL),
        ("間隔そのものの p90", "12.0 ms", "余裕で合格に見える", GRN),
        ("間隔そのものの p99", "419 ms", "14 倍の違反がやっと見える", RED),
    ]):
        x = 40 + i * 300
        b.panel(x, 222, 280, 230, lab, col)
        b.text(x, 280, 280, 70, v, 40, TX, "center")
        b.note(x + 10, 370, 260, verdict, TX, 15)
        b.note(x + 10, 400, 260, "詰まる間隔は全体の 5.7 %" if i == 1 else "", MUTED, 13)
    b.takeaway("割合は小さいが 1 回が致命的な詰まりは、分位点を 99 % まで上げないと見えない")
    return b


@fig("dt-fig2-percentile-stairs")
def percentile_stairs():
    b = Board("判定の分位点だけを変えて、9 割が約束を守れる最大の到着率を測る")
    b.panel(40, 80, 880, 380, "", GRAY)
    rows = [("p50", 2.160, "1.36"), ("p90", 1.801, "1.63"), ("p95", 1.568, "1.87"), ("p99", 0.640, "4.57"),
            ("p99.9", 0.060, "48.9")]
    base, top = 390, 130
    scale = (base - top) / 3.0
    b.line(90, base, 900, base, "5A5C64", 1)
    for v in (1.0, 2.0, 3.0):
        y = base - v * scale
        b.line(90, y, 900, y, "2A2C32", 1)
        b.note(44, y - 11, 40, f"{v:.0f}", MUTED, 13, "right")
    for i, (lab, u, ratio) in enumerate(rows):
        x = 120 + i * 155
        hu, hs = u * scale, 2.928 * scale
        b.bar(x, base - hu, 52, max(hu, 2), ORG)
        b.bar(x + 58, base - hs, 52, hs, GRN)
        b.note(x - 20, base - hu - 26, 92, f"{u:.2f}", ORG, 14)
        b.note(x - 20, base + 8, 150, lab, TX, 16)
        b.note(x - 40, base + 32, 190, ratio + (" 倍 (向きのみ)" if lab == "p99.9" else " 倍"), YEL, 14)
    b.note(400, 96, 500, "橙 = 統合   緑 = 分離の書く係   黄 = 緑 ÷ 橙 (件/s)", MUTED, 14, "right")
    b.note(545, 274, 120, "ここが崖", RED, 16)
    b.takeaway("統合は p95 と p99 の間で崖になり、分離の書く係は 2.928 件/s のまま")
    return b


@fig("dt-fig3-three-bands")
def three_bands():
    b = Board("TPOT の上限を締めると、勝者は 3 つの帯に分かれる")
    b.panel(40, 80, 880, 380, "", GRAY)
    x0, x1, lo, hi = 90, 870, 18.0, 42.0
    X = lambda v: x0 + (v - lo) / (hi - lo) * (x1 - x0)
    split, uni = 24.3, 32.3
    b.block(X(lo), 150, X(split) - X(lo) - 4, 120, "帯 1", GRAY, "機材の限界より厳しい", 16)
    b.block(X(split), 150, X(uni) - X(split) - 4, 120, "帯 2: 分離が守る", GRN, "TPOT だけの候補", 18)
    b.block(X(uni), 150, X(hi) - X(uni), 120, "帯 3: 両方が守る", BLU, "TTFT の上限で勝敗が決まる", 16)
    b.line(x0, 300, x1, 300, TX, 2)
    for v in (20, 25, 30, 35, 40):
        b.line(X(v), 294, X(v), 306, TX, 2)
        b.note(X(v) - 30, 312, 60, f"{v}", MUTED, 13)
    b.note(x1 - 200, 336, 200, "TPOT の上限 (ms)", MUTED, 14, "right")
    for v, lab, col in [(split, "分離: 平均 TPOT の p90 24.3 ms", GRN), (uni, "統合: 平均 TPOT の p90 32.3 ms", ORG)]:
        b.line(X(v), 140, X(v), 300, col, 2, dashed=True)
        b.note(X(v) - 160, 116, 320, lab, col, 14)
    b.note(70, 390, 820, "到着 3 タスク/s、統合 16 レプリカ 対 分離 6P+10D、TCP", MUTED, 14)
    b.takeaway("帯 2 は TPOT だけの候補で、合否は TTFT と同時に数えて決める")
    return b


@fig("dv-fig1-instruments")
def instruments():
    b = Board("測る道具の欠陥は、測られる側の性質に見える形で現れた")
    items = [
        ("間隔が 35.2 と 5.6 ms", "port-forward が 2 つの\nイベントをまとめて届けた"),
        ("エンジンが崩壊した", "負荷生成器の tick が\n1800 回中 606 回だけ"),
        ("2 レプリカが 1.40 倍", "生成器が律速。\n本当は 1.05〜1.09 倍"),
        ("分離が全軸で負けた", "書く係でも Prefill を\nやり直していた"),
        ("混在負荷の TTFT 137 ms", "長いタスクも 1,024\nトークンで送っていた"),
        ("12 万トークンが全件拒否", "クライアントの 1 行の\n上限 512 KB に当たった"),
    ]
    for i, (sym, cause) in enumerate(items):
        col, row = i % 3, i // 3
        x, y = 40 + col * 300, 86 + row * 190
        b.panel(x, y, 280, 172, "", GRAY)
        b.block(x + 16, y + 16, 248, 46, sym, RED, None, 15)
        b.arrow(x + 140, y + 66, x + 140, y + 92)
        b.block(x + 16, y + 96, 248, 60, cause.replace("\\n", "\n"), GRAY, None, 14)
    b.takeaway("平均や合計が正しいまま中身が壊れる経路は、分位点を測るときに最も危ない")
    return b


@fig("dv-fig2-arrival-ceiling")
def arrival_ceiling():
    b = Board("回線は 1 要求の時間より、分離に載せられる到着率の上限として効く")
    b.panel(40, 80, 420, 380, "低い到着率での TTFT p90", BLU)
    base = 400
    for i, (lab, v, col) in enumerate([("EFA", 1271, GRN), ("TCP", 1782, ORG)]):
        h = v / 2000 * 220
        x = 110 + i * 170
        b.bar(x, base - h, 110, h, col)
        b.note(x - 20, base - h - 28, 150, f"{v:,} ms", TX, 16)
        b.note(x - 20, base + 8, 150, lab, TX, 16)
    b.note(60, 124, 380, "入力 8,192、4B、到着 0.25 タスク/s", MUTED, 14, "left")
    b.panel(500, 80, 420, 380, "TCP で到着率を上げたときの達成率", ORG)
    pts = [("0.62", 100.0), ("0.90", 50.0), ("1.72", 17.6), ("2.74", 6.1)]
    for i, (r, a) in enumerate(pts):
        h = a / 100 * 220
        x = 540 + i * 95
        b.bar(x, base - h, 64, max(h, 2), ORG)
        b.note(x - 16, base - h - 26, 96, f"{a:g} %", TX, 14)
        b.note(x - 16, base + 8, 96, r + " 倍", TX, 14)
    b.note(520, 124, 380, "横軸 = 到着率 / (帯域 / 1 要求の KV)", MUTED, 14, "left")
    y90 = base - 0.9 * 220
    b.line(530, y90, 900, y90, YEL, 1, dashed=True)
    b.note(800, y90 - 22, 100, "目標 90 %", YEL, 13, "right")
    b.takeaway("上限 λmax = W / S。TCP では入力 8,192 で 0.82 呼び出し/s、EFA なら 59")
    return b


@fig("dv-fig3-72x-ladder")
def ladder():
    b = Board("72 倍の差を、経路の段ごとに分解する")
    b.panel(40, 80, 880, 380, "", GRAY)
    rows = [("UCX の TCP、GPU メモリから", 0.44, ORG, ""), ("UCX の TCP、ホストメモリから", 1.11, ORG, "2.5 倍  GPU の中継"),
            ("素の TCP 1 本", 1.11, GRAY, "1.0 倍  UCX は 1 本だけ"), ("素の TCP 16 本", 13.77, BLU, "12.4 倍  接続の本数"),
            ("EFA、GPU メモリから直接", 32.40, GRN, "2.35 倍  線と転送方式")]
    for i, (lab, v, col, why) in enumerate(rows):
        y = 104 + i * 70
        b.note(56, y + 8, 250, lab, TX, 15, "right")
        w = max(4, v / 32.4 * 270)
        b.bar(320, y, w, 40, col)
        b.note(320 + w + 8, y + 8, 110, f"{v:g} GiB/s", TX, 15, "left")
        if why:
            b.note(720, y + 8, 200, why, YEL, 14, "left")
    b.takeaway("2.5 × 12.4 × 2.35 ≒ 73。差の大半は GPU メモリの中継と接続の本数")
    return b


@fig("dv-fig4-repeats")
def repeats():
    b = Board("繰り返しを条件にすると、分離の勝ちマスは 4 から 0 になる")
    ttft = ["1000", "2000", "3000", "5000"]
    tpot = ["10", "15", "20", "30", "100"]
    single = [["U", "D", "D", "D"], ["U", "=", "=", "="], ["U", "=", "=", "D"], ["U", "U", "U", "="], ["U", "U", "U", "U"]]
    rep = [["U", "=", "=", "="], ["U", "U", "=", "="], ["U", "U", "=", "="], ["U", "U", "=", "="], ["U", "U", "U", "U"]]
    colors = {"U": BLU, "D": GRN, "=": GRAY}
    for k, (title, grid, border) in enumerate([("1 回だけ: 分離の勝ち 4 マス", single, LIME),
                                                ("3 回すべて合格が条件: 0 マス", rep, GRAY)]):
        px = 40 + k * 450
        b.panel(px, 80, 430, 380, title, border, TX)
        gx, gy, cw, ch = px + 110, 150, 72, 50
        for j, t in enumerate(ttft):
            b.note(gx + j * (cw + 6) - 6, gy - 26, cw + 12, t, MUTED, 13)
        for i, t in enumerate(tpot):
            b.note(px + 14, gy + i * (ch + 6) + 13, 86, t, MUTED, 13, "right")
            for j in range(4):
                c = grid[i][j]
                b.block(gx + j * (cw + 6), gy + i * (ch + 6), cw, ch, {"U": "統合", "D": "分離", "=": "同じ"}[c], colors[c], None, 13)
        b.note(px + 14, 432, 400, ("到着 0.25 タスク/s は 1 回だけ測定" if k else "横 TTFT 上限、縦 TPOT 上限 (ms)"), MUTED, 13, "left")
    b.takeaway("達成率の目標の近くにある 1 回の結果は、どちら向きの交差の証拠にもならない")
    return b


@fig("dd-fig1-order")
def order():
    b = Board("分離するかどうかは 7 段で決める。上の段が下の段の前提になる")
    steps = [("約束を決める", "上限・分位点・達成率", YEL),
             ("間隔は ICL で測る", "クライアントはクラスタ内", BLU),
             ("実際の負荷の形で流す", "送った総量を確かめる", BLU),
             ("統合を先に最適化する", "1 回の読みを縛る", ORG),
             ("机の上で計算する", "天井、境目、到着率の上限", PUR),
             ("帯 2 を探す", "約束を引数に、比率も振る", GRN),
             ("繰り返しで確かめる", "全回の合格を条件に", GRN)]
    for i, (t, sub, col) in enumerate(steps):
        col_i, row = i % 4, i // 4
        x, y = 40 + col_i * 225, 100 + row * 200
        b.block(x + 10, y, 195, 120, t, col, sub, 16)
        b.badge(x + 22, y + 12, i + 1, TX)
        if i < len(steps) - 1 and col_i < 3:
            b.arrow(x + 207, y + 60, x + 233, y + 60)
    b.line(822, 222, 822, 260, TX, 2)
    b.line(822, 260, 147, 260, TX, 2)
    b.arrow(147, 260, 147, 296)
    b.takeaway("段を飛ばすと、下の段で測った数字が何を意味するか決まらない")
    return b

def main():
    want = sys.argv[1] if len(sys.argv) > 1 else ""
    os.makedirs(IMAGES, exist_ok=True)
    for name, fn in FIGS.items():
        if not name.startswith(want):
            continue
        fn().save(os.path.join(HERE, name + ".pptx"), os.path.join(IMAGES, name + ".png"))
        print(name)


if __name__ == "__main__":
    main()
