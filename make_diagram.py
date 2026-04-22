"""
PRISMA Review Agent — simplified flow architecture diagram.
Left-to-right: User -> Search Agent -> Tools -> Screening -> Extraction -> Parallel Synthesis -> Output
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

fig = plt.figure(figsize=(30, 24))
ax  = fig.add_axes([0, 0, 1, 1])
ax.set_xlim(0, 30)
ax.set_ylim(-6, 18)
ax.axis("off")
fig.patch.set_facecolor("#F8FAFD")

BG      = "#F8FAFD"
C_USER  = "#EDE9FE"; E_USER  = "#6D28D9"
C_A1    = "#FEF9C3"; E_A1    = "#B45309"
C_TOOL  = "#DCFCE7"; E_TOOL  = "#15803D"
C_DED   = "#F1F5F9"; E_DED   = "#475569"
C_SCRN  = "#E0F2FE"; E_SCRN  = "#0369A1"
C_FT    = "#F0FDF4"; E_FT    = "#166534"
C_EV    = "#FFF7ED"; E_EV    = "#C2410C"
C_PAR   = "#F5F3FF"; E_PAR   = "#7C3AED"
C_OUT   = "#FFF1F2"; E_OUT   = "#BE123C"
C_CACHE = "#FFFBEB"; E_CACHE = "#D97706"
DARK    = "#1E293B"
MID     = "#475569"
LGRAY   = "#94A3B8"

def box(x, y, w, h, fc, ec, lw=2.0, r=0.3, zo=3, alpha=1.0):
    p = FancyBboxPatch((x, y), w, h,
        boxstyle=f"round,pad=0,rounding_size={r}",
        facecolor=fc, edgecolor=ec, linewidth=lw, zorder=zo, alpha=alpha)
    ax.add_patch(p)

def txt(x, y, s, fs=9, fw="normal", color=DARK, ha="center", va="center", zo=6):
    ax.text(x, y, s, fontsize=fs, fontweight=fw, color=color,
            ha=ha, va=va, zorder=zo, multialignment=ha if ha!="center" else "center")

def div(x, y, w, color="#CBD5E1"):
    ax.plot([x, x+w], [y, y], color=color, lw=0.9, zorder=5)

def arr(x1, y1, x2, y2, color=MID, lw=2.0, rad=0.0, hw=0.32, hl=0.45):
    ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
        arrowprops=dict(arrowstyle=f"-|>,head_width={hw},head_length={hl}",
                        color=color, lw=lw,
                        connectionstyle=f"arc3,rad={rad}"), zorder=5)

# ── Title ─────────────────────────────────────────────────────────
box(0.1, 17.1, 29.8, 0.78, "#0F2942", "#0F2942", r=0.2)
txt(15, 17.56, "PRISMA Review Agent  —  System Architecture & Data Flow",
    fs=19, fw="bold", color="white")
txt(15, 17.18,
    "ReviewProtocol -> Search Agent -> PubMed + bioRxiv Tools -> "
    "Dedup -> Two-Stage Screening -> Evidence + RoB + Data -> "
    "Parallel Synthesis -> PRISMA Report",
    fs=9.5, color="#93C5FD")

# ── Step badges ───────────────────────────────────────────────────
steps = [
    (1.2,  2.2,  "STEP 1\nUser / Protocol"),
    (3.8,  2.8,  "STEP 2\nSearch Strategy"),
    (7.0,  4.2,  "STEP 3\nData Collection"),
    (11.5, 2.0,  "STEP 4\nDedup"),
    (13.5, 4.6,  "STEP 5 & 6\nTwo-Stage Screening"),
    (19.0, 5.8,  "STEP 7 & 8\nExtraction + RoB"),
    (24.5, 4.8,  "STEP 9\nParallel Synthesis"),
    (28.2, 2.2,  "STEP 10\nOutput"),
]
for cx, w, label in steps:
    box(cx-w/2, 16.18, w, 0.72, "#E2E8F0", "#94A3B8", lw=1.0, r=0.22, zo=2)
    txt(cx, 16.54, label, fs=8.5, fw="bold", color="#334155")

# ══════════════════════════════════════════════════════════════════
# 1. USER / REVIEW PROTOCOL
# ══════════════════════════════════════════════════════════════════
box(0.15, 9.5, 2.3, 6.2, C_USER, E_USER, lw=2.2)
txt(1.3, 15.2, "Researcher", fs=10.5, fw="bold", color=E_USER)
div(0.28, 14.88, 2.04, "#C4B5FD")
txt(1.3, 14.5, "ReviewProtocol", fs=9.5, fw="bold", color=DARK)
fields = ["title / question","objective","pico_population",
          "pico_intervention","pico_comparison","pico_outcome",
          "inclusion_criteria","exclusion_criteria",
          "databases","rob_tool","date_range"]
for i, f in enumerate(fields):
    txt(1.3, 14.1 - i*0.37, f, fs=8, color=MID)

txt(1.3, 9.88, "CLI or Python API", fs=8, color=E_USER, fw="bold")
arr(2.45, 12.6, 2.6, 12.6, E_USER, lw=2.2, hw=0.3, hl=0.42)
txt(2.52, 12.95, "protocol", fs=8, color=E_USER, fw="bold")

# ══════════════════════════════════════════════════════════════════
# 2. SEARCH STRATEGY AGENT
# ══════════════════════════════════════════════════════════════════
box(2.6, 10.5, 2.7, 4.4, C_A1, E_A1, lw=2.5)
txt(3.95, 14.5, "Agent 1", fs=9, fw="bold", color="#92400E")
txt(3.95, 14.1, "search_strategy", fs=9.5, fw="bold", color=E_A1)
txt(3.95, 13.72, "_agent", fs=9.5, fw="bold", color=E_A1)
div(2.75, 13.45, 2.4, "#FCD34D")
txt(3.95, 13.15, "Output: SearchStrategy", fs=8.5, fw="bold", color=DARK)
for i, f in enumerate(["2-5 pubmed_queries","2-3 biorxiv_queries",
                         "mesh_terms[]","rationale"]):
    txt(3.95, 12.78 - i*0.37, f, fs=8.5, color=MID)
txt(3.95, 10.85, "Protocol injected\nas dynamic context", fs=8, color=E_A1, fw="bold")

arr(5.3, 12.5, 5.5, 12.5, E_A1, lw=2.5, hw=0.3, hl=0.42)
txt(5.4, 12.85, "queries", fs=8, color=E_A1, fw="bold")

# ══════════════════════════════════════════════════════════════════
# 3. DATA COLLECTION TOOLS PANEL
# ══════════════════════════════════════════════════════════════════
box(5.5, 1.2, 5.2, 14.6, C_TOOL, E_TOOL, lw=2.0, r=0.4, alpha=0.35)
txt(8.1, 15.55, "Data Collection Tools  (clients.py)", fs=11, fw="bold", color=E_TOOL)
txt(8.1, 15.18, "HTTP clients called by pipeline  |  results cached in SQLite (72h TTL)", fs=8.5, color="#166534")
div(5.65, 14.95, 4.9, "#86EFAC")

tools_grp = [
    ("PubMed Search Tool",
     ["esearch.fcgi -> PMID list",
      "efetch.fcgi?retmode=xml -> Article[]",
      "Regex parse XML tags",
      "Batch: 50 PMIDs per call",
      "Rate: 0.35s between requests"]),
    ("PMC Full-text Tool",
     ["efetch.fcgi?db=pmc -> full XML",
      "Extract <body> tag -> strip tags",
      "Max 12,000 chars per article",
      "Max 10 per call"]),
    ("bioRxiv Tool",
     ["biorxiv REST API -> JSON",
      "Last N days (default 180)",
      "Word-overlap filter (>=2 words)",
      "Cursor pagination, 30/page"]),
    ("Related Articles Tool",
     ["elink?LinkName=neighbor_score",
      "Seeds: top 8 PubMed PMIDs",
      "Max 15 related per depth",
      "Depth 1..related_depth"]),
    ("Citation Hops Tool",
     ["elink neighbor_score (backward)",
      "elink pubmed_pubmed_citedin (forward)",
      "Seeds: top 5 PubMed PMIDs",
      "Hop 1..max_hops"]),
]

TW=4.85; TH_base=[2.05,1.85,1.85,1.85,1.85]
TY0=14.7; TX=5.62; gap=0.12
cy=TY0
for i,(name,details) in enumerate(tools_grp):
    th=TH_base[i]
    box(TX, cy-th, TW, th, "white", E_TOOL, lw=1.2, r=0.18)
    txt(TX+TW/2, cy-0.28, name, fs=9.5, fw="bold", color=E_TOOL)
    for j,d in enumerate(details):
        ax.text(TX+0.18, cy-0.62-j*0.28, d, fontsize=8, color=MID,
                ha="left", va="center", zorder=7)
    cy -= th+gap

box(5.68, 0.2, 4.8, 0.72, C_CACHE, E_CACHE, lw=1.4, r=0.18)
txt(8.08, 0.56, "SQLite Cache  (72h TTL  |  5 namespaces)", fs=9, fw="bold", color=E_CACHE)

arr(10.7, 10.5, 11.1, 10.5, E_TOOL, lw=2.5, hw=0.3, hl=0.42)
txt(10.9, 10.88, "Article[]\n(deduplicated)", fs=8.5, fw="bold", color=E_TOOL)

# ══════════════════════════════════════════════════════════════════
# 4. DEDUPLICATION
# ══════════════════════════════════════════════════════════════════
box(11.1, 9.6, 2.1, 2.0, C_DED, E_DED, lw=2.0)
txt(12.15, 11.25, "Deduplication", fs=9.5, fw="bold", color=E_DED)
div(11.25, 10.98, 1.8, "#94A3B8")
txt(12.15, 10.7, "Key: DOI (lower)", fs=8, color=MID)
txt(12.15, 10.42, "Fallback: PMID", fs=8, color=MID)
txt(12.15, 10.13, "Keep first; drop dups", fs=8, color=MID)
txt(12.15, 9.82, "flow.duplicates_removed", fs=7.5, color=LGRAY)

arr(13.2, 10.6, 13.45, 10.6, E_DED, lw=2.2, hw=0.28, hl=0.38)

# ══════════════════════════════════════════════════════════════════
# 5. TWO-STAGE SCREENING PANEL
# ══════════════════════════════════════════════════════════════════
box(13.45, 4.3, 5.3, 11.4, C_SCRN, E_SCRN, lw=2.0, r=0.4, alpha=0.4)
txt(16.1, 15.45, "Two-Stage Screening  (Agent 2 — screening_agent)", fs=11, fw="bold", color=E_SCRN)
txt(16.1, 15.1, "Same agent, opposite bias policies  |  Batch processing  |  Auto-include on error", fs=8.5, color="#0C4A6E")
div(13.6, 14.85, 5.0, "#7DD3FC")

# Stage A
box(13.6, 11.2, 4.95, 3.35, "white", E_SCRN, lw=1.5, r=0.22)
txt(16.07, 14.2, "Stage A: Title/Abstract Screen", fs=10, fw="bold", color=E_SCRN)
div(13.75, 13.88, 4.65, "#BAE6FD")
ta_lines = ["Batch size: 15 articles","Bias: INCLUSIVE (when in doubt, include)",
            "Auto-include entire batch on error","Output: ScreeningBatchResult",
            "Fields: decision (include/exclude),","  reason, relevance_score 0-1",
            "PRISMA rationale: maximise recall","  at abstract screening stage"]
for i,l in enumerate(ta_lines):
    ax.text(13.88,13.56-i*0.29,l,fontsize=8,color=MID,ha="left",va="center",zorder=7)

# Arrow between stages
arr(16.07, 11.2, 16.07, 10.9, E_SCRN, lw=2.0, hw=0.25, hl=0.35)
txt(16.5, 11.05, "ta_included[]", fs=7.5, color=E_SCRN, fw="bold")

# Full-text fetch (inside screening panel)
box(13.6, 9.3, 4.95, 1.65, C_FT, E_FT, lw=1.5, r=0.2)
txt(16.07, 10.62, "PMC Full-text Fetch  (Tool)", fs=9.5, fw="bold", color=E_FT)
div(13.75, 10.32, 4.65, "#86EFAC")
txt(16.07, 10.0, "Filter ta_included by pmc_id", fs=8.5, color=MID)
txt(16.07, 9.7, "efetch?db=pmc -> XML -> strip -> 12k chars", fs=8.5, color=MID)
txt(16.07, 9.42, "Populate article.full_text", fs=8.5, color=MID)
arr(16.07, 9.3, 16.07, 9.05, E_FT, lw=1.8, hw=0.22, hl=0.3)

# Stage B
box(13.6, 4.6, 4.95, 4.2, "white", E_SCRN, lw=1.5, r=0.22)
txt(16.07, 8.45, "Stage B: Full-text Eligibility", fs=10, fw="bold", color=E_SCRN)
div(13.75, 8.12, 4.65, "#BAE6FD")
ft_lines = ["Batch size: 10 articles","Bias: STRICT (PRISMA eligibility criteria)",
            "Auto-include if no full-text available","Output: ScreeningBatchResult",
            "Exclusion reasons logged -> flow.excluded_reasons{}",
            "PRISMA rationale: full assessment","  before synthesis"]
for i,l in enumerate(ft_lines):
    ax.text(13.88,7.82-i*0.3,l,fontsize=8,color=MID,ha="left",va="center",zorder=7)
txt(16.07, 4.88, "PRISMA Flow Counts tracked\nthroughout both stages", fs=8, color=E_SCRN, fw="bold")

arr(18.75, 7.0, 19.0, 7.0, E_SCRN, lw=2.5, hw=0.3, hl=0.42)
txt(18.87, 7.38, "ft_included[]", fs=8.5, fw="bold", color=E_SCRN)

# ══════════════════════════════════════════════════════════════════
# 6. EXTRACTION + RoB
# ══════════════════════════════════════════════════════════════════
box(19.0, 3.5, 5.0, 11.1, C_EV, E_EV, lw=2.0, r=0.4, alpha=0.4)
txt(21.5, 14.35, "Article-Level Analysis", fs=11, fw="bold", color=E_EV)
txt(21.5, 13.98, "Per-article or batched  |  Agents 3, 4, 9", fs=8.5, color="#9A3412")
div(19.15, 13.72, 4.7, "#FDBA74")

# Evidence extraction
box(19.2, 10.75, 4.6, 2.75, "white", E_EV, lw=1.5, r=0.22)
txt(21.5, 13.12, "Agent 9: evidence_extraction_agent", fs=9.5, fw="bold", color=E_EV)
div(19.35, 12.8, 4.3, "#FED7AA")
ev_lines = ["Batch: 5 articles per LLM call","2-5 EvidenceSpan per article",
            "relevance_score 0-1","claim label + is_quantitative flag",
            "Dedup: Jaccard >= 0.7 -> remove","Cap: top 30 spans retained"]
for i,l in enumerate(ev_lines):
    ax.text(19.38,12.5-i*0.3,l,fontsize=8,color=MID,ha="left",va="center",zorder=7)

arr(21.5,10.75,21.5,10.5,E_EV,lw=1.8,hw=0.22,hl=0.3)

# Data extraction (optional)
box(19.2, 8.5, 4.6, 1.75, "white", E_EV, lw=1.5, r=0.22)
txt(21.5, 9.88, "Agent 4: data_extraction_agent", fs=9.5, fw="bold", color=E_EV)
div(19.35, 9.55, 4.3, "#FED7AA")
txt(21.5, 9.28, "Per-article (optional via --extract-data)", fs=8.5, color=MID)
txt(21.5, 9.0, "Output: StudyDataExtraction", fs=8.5, color=MID)
txt(21.5, 8.72, "study_design, sample_size, population,", fs=8, color=LGRAY)
txt(21.5, 8.56, "intervention, outcomes, effect_measures", fs=8, color=LGRAY)

arr(21.5,8.5,21.5,8.28,E_EV,lw=1.8,hw=0.22,hl=0.3)

# Risk of Bias
box(19.2, 5.7, 4.6, 2.38, "white", E_EV, lw=1.5, r=0.22)
txt(21.5, 7.7, "Agent 3: rob_agent", fs=9.5, fw="bold", color=E_EV)
div(19.35, 7.38, 4.3, "#FED7AA")
rob_lines = ["Per-article risk of bias assessment",
             "11 RoB tools supported (RoB 2, ROBINS-I,",
             "  NOS, QUADAS-2, CASP, JBI, Jadad...)",
             "Per-domain judgments: LOW/SOME/HIGH",
             "Output: RiskOfBiasResult"]
for i,l in enumerate(rob_lines):
    ax.text(19.38,7.08-i*0.28,l,fontsize=8,color=MID,ha="left",va="center",zorder=7)
txt(21.5, 5.9, "Tool + domains injected\nfrom ReviewProtocol.rob_tool", fs=8, color=E_EV, fw="bold")

arr(24.0, 7.0, 24.2, 7.0, E_EV, lw=2.5, hw=0.3, hl=0.42)
txt(24.1, 7.38, "evidence[]\narticles[]\nwith RoB", fs=8, fw="bold", color=E_EV)

# ══════════════════════════════════════════════════════════════════
# 7. PARALLEL SYNTHESIS
# ══════════════════════════════════════════════════════════════════
box(24.2, 2.8, 4.8, 11.8, C_PAR, E_PAR, lw=2.5, r=0.4, alpha=0.45)
txt(26.6, 14.35, "Parallel Synthesis", fs=11, fw="bold", color=E_PAR)
txt(26.6, 13.98, "asyncio.gather()  |  Agents 5, 6, 7, 8", fs=8.5, color="#4C1D95")
div(24.35, 13.72, 4.5, "#DDD6FE")

par_agents = [
    ("Agent 5: synthesis_agent",
     ["Input: included articles (top 25)",
      "  + evidence spans (top 20)",
      "  + PRISMA flow summary text",
      "Output: Markdown narrative",
      "Cite: (Author et al., Year; PMID:XXX)",
      "Thematic organisation","Note contradictions"]),
    ("Agent 7: bias_summary_agent",
     ["Input: included article list",
      "Output: str (bias summary)",
      "Overall quality across studies",
      "Common limitations","Publication bias","Heterogeneity"]),
    ("Agent 6: grade_agent  (x3 outcomes)",
     ["Per outcome assessment",
      "Output: GRADEAssessment",
      "5 domains: RoB, inconsistency,",
      "  indirectness, imprecision,","  publication bias",
      "Overall: HIGH/MODERATE/LOW/VERY_LOW"]),
    ("Agent 8: limitations_agent",
     ["Input: flow summary + article list",
      "Output: str (limitations section)",
      "Search limitations","Selection bias risk",
      "AI-assisted screening caveat",
      "Heterogeneity note"]),
]

PH_base=[3.1,2.55,2.9,2.55]; py=13.55; px=24.38; PW=4.45
for i,(name,lines) in enumerate(par_agents):
    ph=PH_base[i]
    box(px,py-ph,PW,ph,"white",E_PAR,lw=1.3,r=0.2)
    txt(px+PW/2,py-0.28,name,fs=9,fw="bold",color=E_PAR)
    div(px+0.1,py-0.52,PW-0.2,"#DDD6FE")
    for j,l in enumerate(lines):
        ax.text(px+0.2,py-0.78-j*0.28,l,fontsize=7.8,color=MID,
                ha="left",va="center",zorder=7)
    py-=ph+0.12

arr(29.0,8.0,29.2,8.0,E_PAR,lw=2.5,hw=0.3,hl=0.42)
txt(29.1,8.4,"result",fs=8.5,fw="bold",color=E_PAR)

# ══════════════════════════════════════════════════════════════════
# 8. OUTPUT
# ══════════════════════════════════════════════════════════════════
box(29.2,5.8,0.68,5.5,C_OUT,E_OUT,lw=2.2,r=0.25)
box(29.2, 4.5, 0.55, 8.5, C_OUT, E_OUT, lw=1.5, r=0.2)
ax.text(29.47, 8.75, "OUTPUT", fontsize=10, fontweight="bold",
        color=E_OUT, ha="center", va="center",
        rotation=90, zorder=6)

# Output boxes stacked
for i,(fmt,color,desc) in enumerate([
    ("Markdown","#0369A1","PRISMA 2020 report\nwith all sections"),
    ("JSON",    "#D97706","Full serialized\nPRISMAReviewResult"),
    ("BibTeX",  "#15803D","@article entries\nfor all included studies"),
]):
    fy=10.5-i*2.4
    box(29.22,fy-2.1,0.5,2.0,"white",color,lw=1.5,r=0.15)
    ax.text(29.47,fy-0.7,fmt,fontsize=8,fontweight="bold",color=color,
            ha="center",va="center",rotation=90,zorder=7)
    ax.text(29.47,fy-1.6,desc,fontsize=6.5,color=MID,
            ha="center",va="center",rotation=90,zorder=7)

# ══════════════════════════════════════════════════════════════════
# STEP DESCRIPTION STRIP
# ══════════════════════════════════════════════════════════════════
box(0.08, -5.95, 29.84, 5.82, "#EFF6FF", "#334155", lw=1.5, r=0.25, zo=2)
box(0.08, -0.62, 29.84, 0.50, "#1E293B", "#1E293B", r=0.2, zo=3)
txt(15, -0.37, "Step-by-Step Notes  —  Which component calls each step (agent vs. pipeline vs. client)",
    fs=11, fw="bold", color="white")

desc_blocks = [
    (0.15, 2.30, "Step 1 — ReviewProtocol",
     ["User or CLI builds the",
      "ReviewProtocol object.",
      "Fields: title, PICO,",
      "inclusion / exclusion,",
      "databases, rob_tool,",
      "date_range.",
      "",
      "CALLER: User / CLI",
      "No agent. No client."]),
    (2.60, 2.70, "Step 2 — Search Strategy",
     ["Pipeline calls Agent 1",
      "(search_strategy_agent).",
      "Protocol injected as",
      "dynamic context via",
      "@agent.system_prompt.",
      "Returns: pubmed_queries,",
      "biorxiv_queries, mesh_terms.",
      "",
      "CALLER: Pipeline",
      "  -> Agent 1 (LLM)"]),
    (5.50, 5.20, "Steps 3-5 — Data Collection",
     ["Pipeline calls PubMedClient",
      "and BioRxivClient DIRECTLY.",
      "No agent is involved.",
      "PubMed: esearch.fcgi (PMIDs)",
      "  + efetch.fcgi?retmode=xml",
      "bioRxiv: REST JSON API",
      "Related: elink neighbor_score",
      "Hops: elink fwd + backward",
      "",
      "CALLER: Pipeline -> clients",
      "  directly (no agent)"]),
    (11.10, 2.10, "Step 6 — Dedup",
     ["Pipeline deduplicates",
      "in pure Python.",
      "Key: doi.lower().",
      "Fallback: pmid.",
      "First-seen wins.",
      "flow.duplicates_removed",
      "incremented.",
      "",
      "CALLER: Pipeline",
      "(no agent, no client)"]),
    (13.45, 5.30, "Steps 7-9 — Two-Stage Screening",
     ["Pipeline calls Agent 2",
      "(screening_agent) TWICE.",
      "Stage A (T/A): batch 15,",
      "  INCLUSIVE bias.",
      "Between stages: pipeline",
      "  calls PubMedClient",
      "  .fetch_full_text() directly",
      "  for PMC articles.",
      "Stage B (FT): batch 10,",
      "  STRICT bias.",
      "",
      "CALLER: Pipeline -> Agent 2",
      "+ PubMedClient (PMC, no agent)"]),
    (19.00, 5.00, "Steps 10-12 — Extraction + RoB",
     ["Pipeline calls 3 LLM agents",
      "sequentially. No heuristics.",
      "Agent 9: evidence_extraction",
      "  LLM-only, batch 5 articles.",
      "Agent 4: data_extraction",
      "  per-article (optional flag).",
      "Agent 3: rob_agent",
      "  per-article, uses rob_tool",
      "  from ReviewProtocol.",
      "",
      "CALLER: Pipeline",
      "  -> Agents 9, 4, 3 (LLM)"]),
    (24.20, 5.68, "Steps 13-15 — Synthesis",
     ["Step 13: Pipeline calls Agent 5",
      "  (synthesis_agent) sequentially.",
      "Step 14: asyncio.gather()",
      "  fires 3 agents in parallel:",
      "  Agent 7: bias_summary_agent",
      "  Agent 8: limitations_agent",
      "  Agent 6: grade_agent (x3)",
      "",
      "Step 15: Results merged into",
      "  PRISMAReviewResult.",
      "",
      "CALLER: Pipeline -> Agent 5",
      "  then asyncio.gather(6,7,8)"]),
]

for dx, dw, header, lines in desc_blocks:
    bw = dw - 0.08
    # Box background
    box(dx, -5.82, bw, 5.08, "white", "#64748B", lw=1.0, r=0.18, zo=3)
    # Header bar
    box(dx, -0.74, bw, 0.44, "#1E3A5F", "#1E3A5F", r=0.18, zo=4)
    txt(dx + bw/2, -0.52, header, fs=9, fw="bold", color="white", zo=6)
    for i, line in enumerate(lines):
        if not line:
            continue
        if line.startswith("CALLER"):
            lc = "#0369A1"; lfw = "bold"
        else:
            lc = MID; lfw = "normal"
        ax.text(dx + 0.13, -0.98 - i*0.35, line,
                fontsize=8, color=lc, fontweight=lfw,
                ha="left", va="center", zorder=6)

# ── Legend ────────────────────────────────────────────────────────
legend=[
    (C_USER,E_USER,"User / ReviewProtocol"),
    (C_A1,E_A1,"Agent 1: Search Strategy"),
    (C_TOOL,E_TOOL,"Data Collection Tools"),
    (C_DED,E_DED,"Deduplication"),
    (C_SCRN,E_SCRN,"Screening Agent (2-stage)"),
    (C_FT,E_FT,"Full-text Fetch"),
    (C_EV,E_EV,"Evidence + RoB + Data Agents"),
    (C_PAR,E_PAR,"Parallel Synthesis Agents"),
    (C_OUT,E_OUT,"Output / Export"),
    (C_CACHE,E_CACHE,"SQLite Cache"),
]
for i,(fc,ec,label) in enumerate(legend):
    lx=0.2+i*3.0
    box(lx,0.1,0.28,0.22,fc,ec,lw=1.1,r=0.05)
    txt(lx+0.44,0.21,label,fs=8.5,color=DARK,ha="left")

plt.tight_layout(pad=0)
out="/Users/tekrajchhetri/Documents/research_codes_papers_writing/synthscholar/PRISMA_Agent_Architecture.png"
fig.savefig(out,dpi=155,bbox_inches="tight",facecolor=BG)
print(f"Saved: {out}")
