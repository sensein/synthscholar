"""
PRISMA Review Agent — simplified architecture diagram.
Two-row layout reflecting the full 18-step pipeline.
"""

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

FIG_W, FIG_H = 20, 11
fig, ax = plt.subplots(figsize=(FIG_W, FIG_H))
ax.set_xlim(0, FIG_W)
ax.set_ylim(0, FIG_H)
ax.axis("off")
fig.patch.set_facecolor("white")

# ── palette ───────────────────────────────────────────────────────
C_PROT   = "#DBEAFE"; E_PROT   = "#3B82F6"
C_SEARCH = "#D4EDDA"; E_SEARCH = "#5A8A6A"
C_TOOLS  = "#C9D4E8"; E_TOOLS  = "#7B9BBF"
C_EXP    = "#E0E7FF"; E_EXP    = "#6366F1"
C_SCR    = "#FDE8CE"; E_SCR    = "#C4884A"
C_ST1    = "#FFFAF0"; E_ST1    = "#9DBF82"
C_ST2    = "#EEF4FB"; E_ST2    = "#7BA8CC"
C_PS     = "#FEF7F0"; E_PS     = "#B85C38"  # per-study cluster
C_EE     = "#FEE2E2"; E_EE     = "#DC2626"
C_DEA    = "#FFE4E6"; E_DEA    = "#E11D48"
C_ROB    = "#FCE7F3"; E_ROB    = "#DB2777"
C_DCA    = "#FFEDD5"; E_DCA    = "#EA580C"
C_CAA    = "#FED7AA"; E_CAA    = "#C2410C"
C_NRA    = "#FEF3C7"; E_NRA    = "#D97706"
C_SV     = "#F0F9FF"; E_SV     = "#0284C7"  # synthesis cluster
C_SYN    = "#FFFCE8"; E_SYN    = "#B8A830"
C_GV     = "#CFFAFE"; E_GV     = "#0891B2"
C_P1     = "#FDFAD0"; E_P1     = "#B8A830"  # parallel #1 cluster
C_BIAS   = "#FFFBEB"; E_BIAS   = "#D97706"
C_GRADE  = "#ECFDF5"; E_GRADE  = "#059669"
C_LIM    = "#F5F3FF"; E_LIM    = "#7C3AED"
C_INTRO  = "#FAE8FF"; E_INTRO  = "#A21CAF"
C_P2     = "#FAF5FF"; E_P2     = "#9333EA"  # parallel #2 cluster
C_CONC   = "#FAE8FF"; E_CONC   = "#A21CAF"
C_ABS    = "#FAE8FF"; E_ABS    = "#A21CAF"
C_QC     = "#E0F2FE"; E_QC     = "#0284C7"
C_RES    = "#F0FDF4"; E_RES    = "#166534"
DARK     = "#2C2C2C"
MID      = "#555555"
ARROW_C  = "#555555"


# ── helpers ───────────────────────────────────────────────────────
def rbox(x, y, w, h, fc, ec, lw=1.6, radius=0.16, zo=3):
    ax.add_patch(FancyBboxPatch(
        (x, y), w, h,
        boxstyle=f"round,pad=0,rounding_size={radius}",
        facecolor=fc, edgecolor=ec, linewidth=lw, zorder=zo,
    ))

def lbl(x, y, text, fs=9, fw="bold", color=DARK,
        ha="center", va="center", zo=5):
    ax.text(x, y, text, fontsize=fs, fontweight=fw, color=color,
            ha=ha, va=va, zorder=zo, linespacing=1.4)

def divider(x, y, w, color="#CBD5E1"):
    ax.plot([x, x + w], [y, y], color=color, lw=0.8, zorder=5)

def arr(x1, y1, x2, y2, lw=1.4, color=ARROW_C, hw=0.16, hl=0.20, rad=0.0):
    ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
        arrowprops=dict(
            arrowstyle=f"-|>,head_width={hw},head_length={hl}",
            color=color, lw=lw,
            connectionstyle=f"arc3,rad={rad}",
        ), zorder=4)


# ══════════════════════════════════════════════════════════════════
# TITLE
# ══════════════════════════════════════════════════════════════════
ax.text(FIG_W / 2, 10.65,
        "PRISMA Review Agent — Simplified Architecture",
        fontsize=16, fontweight="bold", color=DARK,
        ha="center", va="center", zorder=6)

# ══════════════════════════════════════════════════════════════════
# TOP ROW — ACQUISITION PIPELINE
# ══════════════════════════════════════════════════════════════════
TOP_Y_LO = 5.7

# 1. Review Protocol
P_X, P_Y, P_W, P_H = 0.25, 7.0, 1.5, 1.8
rbox(P_X, P_Y, P_W, P_H, C_PROT, E_PROT)
lbl(P_X + P_W / 2, P_Y + P_H / 2,
    "Review\nProtocol\n(PICO)", fs=9.5, fw="bold", color="#1E3A8A")

# 2. Search Strategy Agent
SSA_X, SSA_Y, SSA_W, SSA_H = 2.0, 7.0, 1.8, 1.8
rbox(SSA_X, SSA_Y, SSA_W, SSA_H, C_SEARCH, E_SEARCH)
lbl(SSA_X + SSA_W / 2, SSA_Y + SSA_H / 2,
    "Search\nStrategy\nAgent", fs=9.5, fw="bold", color="#2D5A3D")

# 3. Tools
T_X, T_Y, T_W, T_H = 4.0, 6.0, 1.8, 3.5
rbox(T_X, T_Y, T_W, T_H, C_TOOLS, E_TOOLS, lw=1.8)
ax.text(T_X + T_W / 2, T_Y + T_H + 0.22, "Tools",
        fontsize=11, fontweight="bold", color=DARK,
        ha="center", va="center", zorder=6)
tools = ["PubMed", "BioRxiv"]
for i, t in enumerate(tools):
    ty = T_Y + T_H - 0.6 - i * 1.0
    ax.text(T_X + 0.30, ty, "•", fontsize=13, color="#4A6FA5",
            ha="center", va="center", zorder=6)
    ax.text(T_X + 0.55, ty, t, fontsize=10, color=DARK,
            ha="left", va="center", zorder=6)

# 4. Search Expansion cluster
EX_X, EX_Y, EX_W, EX_H = 6.0, TOP_Y_LO, 2.4, 3.8
rbox(EX_X, EX_Y, EX_W, EX_H, C_EXP, E_EXP, lw=1.8)
ax.text(EX_X + EX_W / 2, EX_Y + EX_H + 0.22, "Search Expansion",
        fontsize=11, fontweight="bold", color=DARK,
        ha="center", va="center", zorder=6)

ex_items = [
    ("Related-articles", "depth-N PubMed"),
    ("Multi-hop citations", "back + forward"),
    ("Deduplication", "DOI / PMID"),
]
ex_inner_h = (EX_H - 0.4) / 3 - 0.10
for i, (head, sub) in enumerate(ex_items):
    bx = EX_X + 0.18
    by = EX_Y + EX_H - 0.20 - (i + 1) * (ex_inner_h + 0.10)
    bw = EX_W - 0.36
    rbox(bx, by, bw, ex_inner_h, "#EEF2FF", E_EXP, lw=1.0, radius=0.10)
    lbl(bx + bw / 2, by + ex_inner_h * 0.65, head,
        fs=8.5, fw="bold", color="#312E81")
    lbl(bx + bw / 2, by + ex_inner_h * 0.30, sub,
        fs=7.8, fw="normal", color=MID)

# 5. Two-stage Screening cluster
SCR_X, SCR_Y, SCR_W, SCR_H = 8.6, TOP_Y_LO, 2.8, 3.8
rbox(SCR_X, SCR_Y, SCR_W, SCR_H, C_SCR, E_SCR, lw=1.8)
ax.text(SCR_X + SCR_W / 2, SCR_Y + SCR_H + 0.22, "Screening Agent",
        fontsize=11, fontweight="bold", color=DARK,
        ha="center", va="center", zorder=6)
lbl(SCR_X + SCR_W / 2, SCR_Y + SCR_H - 0.30,
    "Two-stage screening", fs=8.6, fw="normal", color="#7A4010")
divider(SCR_X + 0.15, SCR_Y + SCR_H - 0.50, SCR_W - 0.30, E_SCR)

S1_X = SCR_X + 0.18; S1_Y = SCR_Y + SCR_H - 1.95
S1_W = SCR_W - 0.36; S1_H = 1.30
rbox(S1_X, S1_Y, S1_W, S1_H, C_ST1, E_ST1, lw=1.2, radius=0.10)
lbl(S1_X + S1_W / 2, S1_Y + S1_H - 0.28,
    "Stage 1 — Title / Abstract", fs=8.5, fw="bold", color="#2E6B2E")
lbl(S1_X + S1_W / 2, S1_Y + S1_H - 0.62,
    "Inclusive bias", fs=8.0, fw="normal", color="#3D7A3D")
lbl(S1_X + S1_W / 2, S1_Y + S1_H - 0.96,
    "Batches of 15", fs=8.0, fw="normal", color=MID)

S2_X = SCR_X + 0.18; S2_Y = SCR_Y + 0.22
S2_W = SCR_W - 0.36; S2_H = 1.30
rbox(S2_X, S2_Y, S2_W, S2_H, C_ST2, E_ST2, lw=1.2, radius=0.10)
lbl(S2_X + S2_W / 2, S2_Y + S2_H - 0.28,
    "Stage 2 — Full-text", fs=8.5, fw="bold", color="#1A4E7A")
lbl(S2_X + S2_W / 2, S2_Y + S2_H - 0.62,
    "Strict bias", fs=8.0, fw="normal", color="#1E5A8A")
lbl(S2_X + S2_W / 2, S2_Y + S2_H - 0.96,
    "Batches of 10", fs=8.0, fw="normal", color=MID)


# ══════════════════════════════════════════════════════════════════
# BOTTOM ROW — ANALYSIS, SYNTHESIS, OUTPUT
# ══════════════════════════════════════════════════════════════════

# 6. Per-Study Agents cluster
PS_X, PS_Y, PS_W, PS_H = 0.25, 0.35, 7.4, 4.5
rbox(PS_X, PS_Y, PS_W, PS_H, C_PS, E_PS, lw=1.8)
ax.text(PS_X + PS_W / 2, PS_Y + PS_H + 0.22, "Per-Study Agents",
        fontsize=11, fontweight="bold", color=DARK,
        ha="center", va="center", zorder=6)

# Left column: EEA, DEA, ROB (parallel, no chain)
L_W = 3.2
ps_left = [
    ("Evidence Extraction", "Batches of 5 · 2–5 spans/article", C_EE, E_EE, "#991B1B"),
    ("Data Extraction", "Per article · structured fields",      C_DEA, E_DEA, "#9F1239"),
    ("Risk of Bias", "Per article · Cochrane / NOS",            C_ROB, E_ROB, "#831843"),
]
inner_h = (PS_H - 0.5) / 3 - 0.12
for i, (head, sub, fc, ec, tc) in enumerate(ps_left):
    bx = PS_X + 0.18
    by = PS_Y + PS_H - 0.30 - (i + 1) * (inner_h + 0.12)
    rbox(bx, by, L_W, inner_h, fc, ec, lw=1.2, radius=0.10)
    lbl(bx + L_W / 2, by + inner_h * 0.66, head,
        fs=8.6, fw="bold", color=tc)
    lbl(bx + L_W / 2, by + inner_h * 0.28, sub,
        fs=7.8, fw="normal", color=MID)

# Right column: DCA → CAA → NRA (chained)
R_X = PS_X + 0.18 + L_W + 0.20
R_W = PS_W - 0.36 - L_W - 0.20
ps_right = [
    ("Data Charting",      "Per article · rubric",        C_DCA, E_DCA, "#9A3412"),
    ("Critical Appraisal", "Per rubric · domain scores",  C_CAA, E_CAA, "#7C2D12"),
    ("Narrative Row",      "Per study · summary row",     C_NRA, E_NRA, "#78350F"),
]
right_box_y = []
for i, (head, sub, fc, ec, tc) in enumerate(ps_right):
    bx = R_X
    by = PS_Y + PS_H - 0.30 - (i + 1) * (inner_h + 0.12)
    rbox(bx, by, R_W, inner_h, fc, ec, lw=1.2, radius=0.10)
    lbl(bx + R_W / 2, by + inner_h * 0.66, head,
        fs=8.6, fw="bold", color=tc)
    lbl(bx + R_W / 2, by + inner_h * 0.28, sub,
        fs=7.8, fw="normal", color=MID)
    right_box_y.append((bx, by, R_W, inner_h))

# Chain arrows DCA → CAA → NRA (vertical, inside right column)
for top, bot in [(0, 1), (1, 2)]:
    bx, by_top, bw, bh = right_box_y[top]
    _,  by_bot, _,  _  = right_box_y[bot]
    arr(bx + bw / 2, by_top, bx + bw / 2, by_bot + bh, lw=1.2)

# 7. Synthesis & Validation cluster
SV_X, SV_Y, SV_W, SV_H = 7.85, 1.0, 2.2, 3.6
rbox(SV_X, SV_Y, SV_W, SV_H, C_SV, E_SV, lw=1.8)
ax.text(SV_X + SV_W / 2, SV_Y + SV_H + 0.22, "Synthesis & Validation",
        fontsize=10.5, fontweight="bold", color=DARK,
        ha="center", va="center", zorder=6)

SY_X = SV_X + 0.15;  SY_Y = SV_Y + SV_H - 1.85
SY_W = SV_W - 0.30;  SY_H = 1.55
rbox(SY_X, SY_Y, SY_W, SY_H, C_SYN, E_SYN, lw=1.2, radius=0.10)
lbl(SY_X + SY_W / 2, SY_Y + SY_H - 0.28,
    "Synthesis Agent", fs=8.6, fw="bold", color="#6B5A10")
divider(SY_X + 0.10, SY_Y + SY_H - 0.48, SY_W - 0.20, E_SYN)
lbl(SY_X + SY_W / 2, SY_Y + SY_H - 0.72,
    "first 25 articles", fs=7.8, fw="normal", color=MID)
lbl(SY_X + SY_W / 2, SY_Y + SY_H - 0.98,
    "top 20 spans", fs=7.8, fw="normal", color=MID)
lbl(SY_X + SY_W / 2, SY_Y + SY_H - 1.24,
    "PMID-grounded", fs=7.8, fw="normal", color=MID)

GV_X = SV_X + 0.15;  GV_Y = SV_Y + 0.20
GV_W = SV_W - 0.30;  GV_H = 1.30
rbox(GV_X, GV_Y, GV_W, GV_H, C_GV, E_GV, lw=1.2, radius=0.10)
lbl(GV_X + GV_W / 2, GV_Y + GV_H - 0.28,
    "Grounding Validation", fs=8.4, fw="bold", color="#155E75")
lbl(GV_X + GV_W / 2, GV_Y + GV_H - 0.62,
    "Claim-level verdicts", fs=7.8, fw="normal", color=MID)
lbl(GV_X + GV_W / 2, GV_Y + GV_H - 0.94,
    "vs. corpus", fs=7.8, fw="normal", color=MID)

# Synthesis → Grounding arrow
arr(SY_X + SY_W / 2, SY_Y, GV_X + GV_W / 2, GV_Y + GV_H, lw=1.2)

# 8. Parallel Cross-Study (gather #1) cluster
P1_X, P1_Y, P1_W, P1_H = 10.25, 0.35, 3.6, 4.5
rbox(P1_X, P1_Y, P1_W, P1_H, C_P1, E_P1, lw=1.8, radius=0.18)
ax.text(P1_X + P1_W / 2, P1_Y + P1_H + 0.22, "Parallel Cross-Study  (gather #1)",
        fontsize=10.5, fontweight="bold", color=DARK,
        ha="center", va="center", zorder=6)

p1_items = [
    ("Bias Summary",  "Cross-study quality",            C_BIAS,  E_BIAS,  "#92400E"),
    ("GRADE",         "Per outcome (up to 3)",          C_GRADE, E_GRADE, "#064E3B"),
    ("Limitations",   "Scope · selection · heterog.",   C_LIM,   E_LIM,   "#4C1D95"),
    ("Introduction",  "Background · rationale",         C_INTRO, E_INTRO, "#701A75"),
]
# 2x2 grid
cell_w = (P1_W - 0.55) / 2
cell_h = (P1_H - 0.55) / 2
for i, (head, sub, fc, ec, tc) in enumerate(p1_items):
    col = i % 2; row = i // 2
    bx = P1_X + 0.18 + col * (cell_w + 0.18)
    by = P1_Y + P1_H - 0.30 - (row + 1) * (cell_h + 0.10)
    rbox(bx, by, cell_w, cell_h, fc, ec, lw=1.1, radius=0.10)
    lbl(bx + cell_w / 2, by + cell_h * 0.65, head,
        fs=8.6, fw="bold", color=tc)
    lbl(bx + cell_w / 2, by + cell_h * 0.30, sub,
        fs=7.6, fw="normal", color=MID)

# 9. Parallel Document Sections (gather #2) cluster
P2_X, P2_Y, P2_W, P2_H = 14.05, 1.0, 2.6, 3.6
rbox(P2_X, P2_Y, P2_W, P2_H, C_P2, E_P2, lw=1.8, radius=0.18)
ax.text(P2_X + P2_W / 2, P2_Y + P2_H + 0.22, "Document Sections  (gather #2)",
        fontsize=10.5, fontweight="bold", color=DARK,
        ha="center", va="center", zorder=6)

p2_items = [
    ("Conclusions",          "uses synthesis + GRADE",  C_CONC, E_CONC, "#701A75"),
    ("Structured Abstract",  "uses synthesis + flow",   C_ABS,  E_ABS,  "#701A75"),
]
p2_cell_h = (P2_H - 0.50) / 2
for i, (head, sub, fc, ec, tc) in enumerate(p2_items):
    bx = P2_X + 0.18
    by = P2_Y + P2_H - 0.30 - (i + 1) * (p2_cell_h + 0.10)
    bw = P2_W - 0.36
    rbox(bx, by, bw, p2_cell_h, fc, ec, lw=1.1, radius=0.10)
    lbl(bx + bw / 2, by + p2_cell_h * 0.65, head,
        fs=8.8, fw="bold", color=tc)
    lbl(bx + bw / 2, by + p2_cell_h * 0.32, sub,
        fs=7.8, fw="normal", color=MID)

# 10. Quality Checklist
QC_X, QC_Y, QC_W, QC_H = 16.85, 1.5, 1.4, 2.6
rbox(QC_X, QC_Y, QC_W, QC_H, C_QC, E_QC, lw=1.6)
lbl(QC_X + QC_W / 2, QC_Y + QC_H / 2 + 0.30,
    "Quality\nChecklist", fs=9, fw="bold", color="#0C4A6E")
lbl(QC_X + QC_W / 2, QC_Y + QC_H / 2 - 0.55,
    "PRISMA-2020\nself-check", fs=7.8, fw="normal", color=MID)

# 11. PRISMA Review Result
RES_X, RES_Y, RES_W, RES_H = 18.45, 1.0, 1.4, 3.6
rbox(RES_X, RES_Y, RES_W, RES_H, C_RES, E_RES, lw=1.8)
lbl(RES_X + RES_W / 2, RES_Y + RES_H / 2,
    "PRISMA\nReview\nResult", fs=9, fw="bold", color="#166534")


# ══════════════════════════════════════════════════════════════════
# ARROWS — top row
# ══════════════════════════════════════════════════════════════════
arr(P_X + P_W,    P_Y + P_H / 2,   SSA_X,            SSA_Y + SSA_H / 2)
arr(SSA_X + SSA_W, SSA_Y + SSA_H / 2, T_X,           T_Y + T_H / 2)
arr(T_X + T_W,    T_Y + T_H / 2,   EX_X,             EX_Y + EX_H * 0.55)
arr(EX_X + EX_W,  EX_Y + EX_H / 2, SCR_X,            SCR_Y + SCR_H / 2)

# Drop arrow: Screening (top row) → Per-Study cluster (bottom row)
arr(SCR_X + SCR_W * 0.4, SCR_Y,
    PS_X + PS_W * 0.5,   PS_Y + PS_H,
    rad=-0.25, lw=1.6, color="#374151")

# ══════════════════════════════════════════════════════════════════
# ARROWS — bottom row
# ══════════════════════════════════════════════════════════════════
arr(PS_X + PS_W, PS_Y + PS_H / 2, SV_X, SV_Y + SV_H / 2)
arr(SV_X + SV_W, SV_Y + SV_H / 2, P1_X, P1_Y + P1_H / 2)
arr(P1_X + P1_W, P1_Y + P1_H / 2, P2_X, P2_Y + P2_H / 2)
arr(P2_X + P2_W, P2_Y + P2_H / 2, QC_X, QC_Y + QC_H / 2)
arr(QC_X + QC_W, QC_Y + QC_H / 2, RES_X, RES_Y + RES_H / 2)


# ══════════════════════════════════════════════════════════════════
# SAVE
# ══════════════════════════════════════════════════════════════════
out = "/Users/tekrajchhetri/Documents/brainypedia_codes_design/prisma-review-agent/simplified_arch.png"
plt.tight_layout()
fig.savefig(out, dpi=180, bbox_inches="tight", facecolor="white")
print(f"Saved: {out}")
