"""
PRISMA Review Agent — simplified architecture diagram.
Style mirrors BioSynthAI simplified_arch.png.
"""

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

FIG_W, FIG_H = 15, 9
fig, ax = plt.subplots(figsize=(FIG_W, FIG_H))
ax.set_xlim(0, FIG_W)
ax.set_ylim(0, FIG_H)
ax.axis("off")
fig.patch.set_facecolor("white")

# ── palette ───────────────────────────────────────────────────────
C_PROT   = "#DBEAFE"; E_PROT   = "#3B82F6"   # blue   — protocol
C_SEARCH = "#D4EDDA"; E_SEARCH = "#5A8A6A"   # green  — search strategy
C_TOOLS  = "#C9D4E8"; E_TOOLS  = "#7B9BBF"   # blue-grey — tools
C_SCR    = "#FDE8CE"; E_SCR    = "#C4884A"   # orange — screening
C_ST1    = "#FFFAF0"; E_ST1    = "#9DBF82"   # light green — stage 1
C_ST2    = "#EEF4FB"; E_ST2    = "#7BA8CC"   # light blue  — stage 2
C_EE     = "#FEE2E2"; E_EE     = "#DC2626"   # red-ish — evidence
C_ROB    = "#FCE7F3"; E_ROB    = "#DB2777"   # pink — risk of bias
C_SYN    = "#FDFAD0"; E_SYN    = "#B8A830"   # yellow — synthesis subgraph
C_RES    = "#F0FDF4"; E_RES    = "#166534"   # green  — result
DARK     = "#2C2C2C"
MID      = "#555555"
ARROW_C  = "#555555"


# ── helpers ───────────────────────────────────────────────────────
def rbox(x, y, w, h, fc, ec, lw=1.8, radius=0.18, zo=3):
    ax.add_patch(FancyBboxPatch(
        (x, y), w, h,
        boxstyle=f"round,pad=0,rounding_size={radius}",
        facecolor=fc, edgecolor=ec, linewidth=lw, zorder=zo,
    ))

def lbl(x, y, text, fs=10, fw="bold", color=DARK,
        ha="center", va="center", zo=5):
    ax.text(x, y, text, fontsize=fs, fontweight=fw, color=color,
            ha=ha, va=va, zorder=zo, linespacing=1.4)

def divider(x, y, w, color="#CBD5E1"):
    ax.plot([x, x + w], [y, y], color=color, lw=0.9, zorder=5)

def arr(x1, y1, x2, y2, lw=1.6, color=ARROW_C, hw=0.18, hl=0.22, rad=0.0):
    ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
        arrowprops=dict(
            arrowstyle=f"-|>,head_width={hw},head_length={hl}",
            color=color, lw=lw,
            connectionstyle=f"arc3,rad={rad}",
        ), zorder=4)


# ══════════════════════════════════════════════════════════════════
# TITLE
# ══════════════════════════════════════════════════════════════════
ax.text(FIG_W / 2, 8.65,
        "PRISMA Review Agent — Simplified Architecture",
        fontsize=15, fontweight="bold", color=DARK,
        ha="center", va="center", zorder=6)

# ══════════════════════════════════════════════════════════════════
# 1. REVIEW PROTOCOL
# ══════════════════════════════════════════════════════════════════
P_X, P_Y, P_W, P_H = 0.15, 3.6, 1.65, 1.8
rbox(P_X, P_Y, P_W, P_H, C_PROT, E_PROT)
lbl(P_X + P_W / 2, P_Y + P_H / 2,
    "Review\nProtocol\n(PICO)", fs=9.5, fw="bold", color="#1E3A8A")

# ══════════════════════════════════════════════════════════════════
# 2. SEARCH STRATEGY AGENT
# ══════════════════════════════════════════════════════════════════
SSA_X, SSA_Y, SSA_W, SSA_H = 2.1, 3.6, 2.1, 1.8
rbox(SSA_X, SSA_Y, SSA_W, SSA_H, C_SEARCH, E_SEARCH)
lbl(SSA_X + SSA_W / 2, SSA_Y + SSA_H / 2,
    "Search\nStrategy\nAgent", fs=9.5, fw="bold", color="#2D5A3D")

# ══════════════════════════════════════════════════════════════════
# 3. TOOLS  (PubMed + BioRxiv)
# ══════════════════════════════════════════════════════════════════
T_X, T_Y, T_W, T_H = 4.5, 0.9, 2.1, 7.2
rbox(T_X, T_Y, T_W, T_H, C_TOOLS, E_TOOLS, lw=2.0)
ax.text(T_X + T_W / 2, T_Y + T_H + 0.22, "Tools",
        fontsize=12, fontweight="bold", color=DARK,
        ha="center", va="center", zorder=6)

tools = ["PubMed", "BioRxiv"]
line_h = (T_H - 0.4) / len(tools)
for i, t in enumerate(tools):
    ty = T_Y + T_H - 0.3 - i * line_h - line_h / 2
    ax.text(T_X + 0.28, ty, "•", fontsize=13, color="#4A6FA5",
            ha="center", va="center", zorder=6)
    ax.text(T_X + 0.50, ty, t, fontsize=10.5, color=DARK,
            ha="left", va="center", zorder=6)

# ══════════════════════════════════════════════════════════════════
# 4. TWO-STAGE SCREENING AGENT  (upper right of tools)
# ══════════════════════════════════════════════════════════════════
SCR_X, SCR_Y, SCR_W, SCR_H = 6.9, 4.3, 3.1, 3.9
rbox(SCR_X, SCR_Y, SCR_W, SCR_H, C_SCR, E_SCR, lw=1.8)

lbl(SCR_X + SCR_W / 2, SCR_Y + SCR_H - 0.28,
    "Screening Agent", fs=9.5, fw="bold", color="#7A4010")
lbl(SCR_X + SCR_W / 2, SCR_Y + SCR_H - 0.60,
    "Two-stage screening", fs=8.5, fw="normal", color="#7A4010")
divider(SCR_X + 0.15, SCR_Y + SCR_H - 0.78, SCR_W - 0.30, E_SCR)

# Stage 1 inner box
S1_X = SCR_X + 0.15;  S1_Y = SCR_Y + SCR_H - 2.15
S1_W = SCR_W - 0.30;  S1_H = 1.22
rbox(S1_X, S1_Y, S1_W, S1_H, C_ST1, E_ST1, lw=1.3, radius=0.12)
lbl(S1_X + S1_W / 2, S1_Y + S1_H - 0.27,
    "Stage 1 — Title / Abstract", fs=8.8, fw="bold", color="#2E6B2E")
lbl(S1_X + S1_W / 2, S1_Y + S1_H - 0.60,
    "Inclusive bias", fs=8.2, fw="normal", color="#3D7A3D")
lbl(S1_X + S1_W / 2, S1_Y + S1_H - 0.92,
    "Batches of 15 articles", fs=8.2, fw="normal", color=MID)

# Stage 2 inner box
S2_X = SCR_X + 0.15;  S2_Y = SCR_Y + 0.22
S2_W = SCR_W - 0.30;  S2_H = 1.22
rbox(S2_X, S2_Y, S2_W, S2_H, C_ST2, E_ST2, lw=1.3, radius=0.12)
lbl(S2_X + S2_W / 2, S2_Y + S2_H - 0.27,
    "Stage 2 — Full-text", fs=8.8, fw="bold", color="#1A4E7A")
lbl(S2_X + S2_W / 2, S2_Y + S2_H - 0.60,
    "Strict bias", fs=8.2, fw="normal", color="#1E5A8A")
lbl(S2_X + S2_W / 2, S2_Y + S2_H - 0.92,
    "Batches of 10 articles", fs=8.2, fw="normal", color=MID)

# ══════════════════════════════════════════════════════════════════
# 5. EVIDENCE EXTRACTION AGENT  (lower, below screening)
# ══════════════════════════════════════════════════════════════════
EE_X, EE_Y, EE_W, EE_H = 6.9, 0.35, 1.45, 3.5
rbox(EE_X, EE_Y, EE_W, EE_H, C_EE, E_EE, lw=1.8)
lbl(EE_X + EE_W / 2, EE_Y + EE_H - 0.30,
    "Evidence\nExtraction\nAgent", fs=8.8, fw="bold", color="#991B1B")
divider(EE_X + 0.1, EE_Y + EE_H - 0.98, EE_W - 0.2, E_EE)
lbl(EE_X + EE_W / 2, EE_Y + EE_H - 1.22,
    "Batches of 5", fs=8.2, fw="normal", color=MID)
lbl(EE_X + EE_W / 2, EE_Y + EE_H - 1.52,
    "2–5 spans", fs=8.2, fw="normal", color=MID)
lbl(EE_X + EE_W / 2, EE_Y + EE_H - 1.82,
    "per article", fs=8.2, fw="normal", color=MID)
lbl(EE_X + EE_W / 2, EE_Y + EE_H - 2.20,
    "Relevance scored\n& deduplicated", fs=8.0, fw="normal", color=MID)

# ══════════════════════════════════════════════════════════════════
# 6. RISK OF BIAS AGENT  (lower, next to evidence extraction)
# ══════════════════════════════════════════════════════════════════
ROB_X, ROB_Y, ROB_W, ROB_H = 8.6, 0.35, 1.45, 3.5
rbox(ROB_X, ROB_Y, ROB_W, ROB_H, C_ROB, E_ROB, lw=1.8)
lbl(ROB_X + ROB_W / 2, ROB_Y + ROB_H - 0.30,
    "Risk of Bias\nAgent", fs=8.8, fw="bold", color="#831843")
divider(ROB_X + 0.1, ROB_Y + ROB_H - 0.82, ROB_W - 0.2, E_ROB)
lbl(ROB_X + ROB_W / 2, ROB_Y + ROB_H - 1.08,
    "Per article", fs=8.2, fw="normal", color=MID)
lbl(ROB_X + ROB_W / 2, ROB_Y + ROB_H - 1.40,
    "Domain-level\njudgments", fs=8.2, fw="normal", color=MID)
lbl(ROB_X + ROB_W / 2, ROB_Y + ROB_H - 1.95,
    "Overall\njudgment", fs=8.2, fw="normal", color=MID)
lbl(ROB_X + ROB_W / 2, ROB_Y + ROB_H - 2.50,
    "Cochrane RoB /\nNewcastle-Ottawa", fs=7.8, fw="normal", color=MID)

# ══════════════════════════════════════════════════════════════════
# 7. PARALLEL SYNTHESIS SUBGRAPH
# ══════════════════════════════════════════════════════════════════
SYN_X, SYN_Y, SYN_W, SYN_H = 10.35, 0.35, 3.25, 7.7
rbox(SYN_X, SYN_Y, SYN_W, SYN_H, C_SYN, E_SYN, lw=2.0, radius=0.22)
ax.text(SYN_X + SYN_W / 2, SYN_Y + SYN_H + 0.22,
        "Parallel Synthesis",
        fontsize=12, fontweight="bold", color=DARK,
        ha="center", va="center", zorder=6)

agents = [
    ("Synthesis Agent",
     "#6B5A10", "#FFFCE8", "#B8A830",
     "first 25 articles (collection order)\ntop 20 evidence spans · PMID citations"),
    ("Bias Summary Agent",
     "#92400E", "#FFFBEB", "#D97706",
     "Cross-study quality assessment"),
    ("GRADE Agent",
     "#064E3B", "#ECFDF5", "#059669",
     "Certainty of evidence\nPer outcome (up to 3)"),
    ("Limitations Agent",
     "#4C1D95", "#F5F3FF", "#7C3AED",
     "Scope · selection bias\nheterogeneity"),
]

n = len(agents)
inner_h = (SYN_H - 0.5) / n - 0.12
for i, (name, tc, fc, ec, desc) in enumerate(agents):
    bx = SYN_X + 0.18
    by = SYN_Y + SYN_H - 0.35 - (i + 1) * (inner_h + 0.12)
    bw = SYN_W - 0.36
    rbox(bx, by, bw, inner_h, fc, ec, lw=1.2, radius=0.12)
    lbl(bx + bw / 2, by + inner_h - 0.30, name,
        fs=8.8, fw="bold", color=tc)
    divider(bx + 0.1, by + inner_h - 0.50, bw - 0.2, ec)
    lbl(bx + bw / 2, by + inner_h / 2 - 0.05, desc,
        fs=8.0, fw="normal", color=MID)

# ══════════════════════════════════════════════════════════════════
# 8. PRISMA REVIEW RESULT
# ══════════════════════════════════════════════════════════════════
RES_X, RES_Y, RES_W, RES_H = 13.85, 3.3, 0.95, 2.6
rbox(RES_X, RES_Y, RES_W, RES_H, C_RES, E_RES, lw=1.8)
lbl(RES_X + RES_W / 2, RES_Y + RES_H / 2,
    "PRISMA\nReview\nResult", fs=8.5, fw="bold", color="#166534")

# ══════════════════════════════════════════════════════════════════
# ARROWS
# ══════════════════════════════════════════════════════════════════
# Protocol → Search Strategy Agent
arr(P_X + P_W, P_Y + P_H / 2,
    SSA_X,    SSA_Y + SSA_H / 2)

# Search Strategy Agent → Tools
arr(SSA_X + SSA_W, SSA_Y + SSA_H / 2,
    T_X,           T_Y + T_H / 2)

# Tools → Screening (upper path)
arr(T_X + T_W, T_Y + T_H * 0.72,
    SCR_X,     SCR_Y + SCR_H * 0.72)

# Tools → Evidence Extraction (lower path)
arr(T_X + T_W, T_Y + T_H * 0.22,
    EE_X,      EE_Y + EE_H * 0.65, rad=0.05)

# Screening → Evidence Extraction
arr(SCR_X + SCR_W * 0.25, SCR_Y,
    EE_X + EE_W * 0.55,   EE_Y + EE_H, rad=0.0)

# Screening → Risk of Bias
arr(SCR_X + SCR_W * 0.70, SCR_Y,
    ROB_X + ROB_W * 0.50,  ROB_Y + ROB_H, rad=0.0)

# Evidence Extraction → Parallel Synthesis
arr(EE_X + EE_W,  EE_Y + EE_H * 0.55,
    SYN_X,         SYN_Y + SYN_H * 0.22)

# Risk of Bias → Parallel Synthesis
arr(ROB_X + ROB_W, ROB_Y + ROB_H * 0.55,
    SYN_X,          SYN_Y + SYN_H * 0.30, rad=-0.05)

# Screening → Parallel Synthesis (evidence spans used in synthesis)
arr(SCR_X + SCR_W, SCR_Y + SCR_H * 0.55,
    SYN_X,          SYN_Y + SYN_H * 0.72, rad=-0.1)

# Parallel Synthesis → PRISMA Result
arr(SYN_X + SYN_W, SYN_Y + SYN_H * 0.55,
    RES_X,          RES_Y + RES_H / 2)

# ══════════════════════════════════════════════════════════════════
# SAVE
# ══════════════════════════════════════════════════════════════════
out = ("/Users/tekrajchhetri/Documents/research_codes_papers_writing"
       "/synthscholar/simplified_arch.png")
plt.tight_layout()
fig.savefig(out, dpi=180, bbox_inches="tight", facecolor="white")
print(f"Saved: {out}")
