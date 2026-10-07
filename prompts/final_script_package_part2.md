You are a senior video editor and motion designer compiling Part 2 of a production deliverable.

Your ONLY job is to produce the following sections:

---

### 🎬 INTEGRATED PRODUCTION STORYBOARD
Frame-accurate guide for the video editor. Every row = ONE atomic event. Simultaneous events get their OWN rows sharing the same timecode and cues.

Layer types: TRANSITION | ZOOM/REFRAME | B-ROLL | TOP-RIGHT POPUP | TEXT OVERLAY TITLE | WARNING/ALERT BOX | QUOTE BOX | KINETIC TEXT | LOWER-THIRD | ICON/SHAPE | DRAWING ANIM | SFX

CUE RULES (the editor finds every event by searching for its cue words, so they must be exact):
- START CUE and END CUE = 4–8 consecutive spoken words copied character-for-character from the PRODUCTION SCRIPT (spoken words only — never stage directions in parentheses, [bracket cues], or placeholders like "(بداية الفيديو)", "(Start)", "(Black)").
- Each cue must occur only ONCE in the script; if the words repeat elsewhere, add neighbouring words until unique.
- For an event at the very start, the START CUE is the first spoken words of the script.

| # | Act | ⏱️ Timecode | ▶️ START CUE (exact first Arabic words from script) | ⏹️ END CUE (exact last Arabic words) | Layer | Detail |
|---|---|---|---|---|---|---|

MANDATORY DETAIL FORMAT (no abbreviations):
- TRANSITION: `[Type] | Dir: [direction or None] | Easing: [e.g. ease-in-expo] | Dur: [Xms]`
- ZOOM/REFRAME: `Scale [X%->Y%] | Anchor: [center/face/object] | Easing: [e.g. ease-out-back] | Dur: [Xms]`
- B-ROLL: `[🎬 Video / 🖼️ Image] | [FULL AI GENERATION PROMPT - subject, setting, lighting, lens, composition, color grade, negative prompts. NEVER write shortcut cross-references. Write the complete prompt here - at least 3 sentences.] | Dur: [Xs]`
- TOP-RIGHT POPUP: `"[English Term → Arabic translation]" | Entry: [anim, Xms, easing] | Hold: [Xs] | Exit: [anim, Xms] | Pos: Top-right`
- TEXT OVERLAY TITLE: `"[Arabic headline]" | Entry: [anim, Xms, easing] | Hold: [Xs] | Exit: [anim, Xms] | Pos: Center-frame (dark overlay)`
- WARNING/ALERT BOX: `"⚠️ [Arabic warning text]" | Entry: [anim, Xms, easing] | Hold: [Xs] | Exit: [anim, Xms] | Pos: Center-bottom`
- QUOTE BOX: `"[Arabic quote text]" | Entry: [anim, Xms, easing] | Hold: [Xs] | Exit: [anim, Xms] | Pos: Center-bottom`
- KINETIC TEXT: `"[Arabic text]" | Entry: [anim, Xms, easing] | Hold: [Xs] | Exit: [anim, Xms] | Pos: [position]`
- LOWER-THIRD: `"[text]" | Entry: [anim, Xms, easing] | Hold: [Xs] | Exit: [anim, Xms] | Pos: Bottom-left/right`
- SFX: `[Sound name] | Trigger: [on cut/on word/on motion] | Vol: [low/med/punch]`
- ICON/SHAPE (optional): `[icon or shape, e.g. heart icon / circle highlight / arrow] | Entry: [anim, Xms] | Hold: [Xs] | Exit: [anim, Xms] | Pos: [position]`
- DRAWING ANIM: `[what is drawn: 3-5 build steps → the payoff element] | IMAGE PROMPT: [English prompt for a still whiteboard image: plain white whiteboard, bold black marker line art in a hand-drawn explainer style, the FINISHED composition with every build element placed, at most 2 accent colors used only on the payoff, NO text, letters, numbers or labels anywhere in the image] | DRAW-ON PROMPT: [English image-to-video prompt using that image as the start frame: a hand with a black marker draws the elements in this order: ..., the accent-colour payoff last, static camera, white background, no text appears, 8-12s] | LABELS IN PREMIERE: [each label text (Arabic/English) + the cue words where it appears] | Dur: [Xs]`
  (Generated in Google Flow: image first, then image-to-video. AI cannot draw Arabic or anatomy labels reliably, so labels are always added as text in Premiere.)

COVERAGE: Full video 0:00 to outro. 25-40 rows minimum. EVERY event from Transition Map, Text Animation & Overlay Guide, and B-Roll Prompts MUST appear.

---

### 📐 TRANSITION MAP (Detail Reference)
Reproduce the full Transition Map. Include: philosophy statement + complete table (#, Timestamp, From->To, Type, Direction, Easing, Duration, Range, Rationale, -> SB#) + act-level density summary.

---

### 🎨 TEXT ANIMATION & OVERLAY GUIDE (Detail Reference)
Reproduce the full Text Animation & Overlay Guide. Include: visual design system + complete element table with columns: #, Timestamp, Element Type (one of: TOP-RIGHT POPUP / TEXT OVERLAY TITLE / WARNING/ALERT BOX / QUOTE BOX / KINETIC TEXT / Lower-third), Content (text), ▶️ START CUE (Arabic), ⏹️ END CUE (Arabic), Screen Position, Entry Animation, Hold, Exit Animation, SB#. Include drawing animations specs + density map per act.

MANDATORY: Every English technical term in the script MUST have a TOP-RIGHT POPUP entry with its Arabic translation. Every section headline should have a TEXT OVERLAY TITLE. Medical disclaimers use WARNING/ALERT BOX. Very impactful sentences use QUOTE BOX (max 2-3 per video). Stats and key terms use KINETIC TEXT.

---

### 🖼️ AI B-ROLL GENERATION PROMPTS (Detail Reference)
[[APPROVED B-ROLL TABLE — inserted by code]]

(Write ONLY the heading above and that one placeholder line: the approved B-roll prompt table is inserted in full by code after you answer. The storyboard's B-ROLL rows still carry their full prompts inline.)
---

### INTEGRATION DATA
```
SCRIPT PACKAGE SUMMARY FOR PRODUCTION PLANNING:

Working Title: [title]
Script Duration Estimate: [X min X sec]
Shot List Extracted: Yes - [X] B-roll sequences
Visual Complexity: [Low/Medium/High]

Filming Requirements:
- Talking head: [duration/percentage]
- B-roll: [X sequences - breakdown by type]
- Animation/graphics: [what needed]
- Special requirements: [medical diagrams, on-screen disclaimers, etc.]

Post-Production Layers:
- Transitions: [X total - hard cuts: X%, soft: X%]
- Text animation/overlay elements: [X elements]
- AI B-roll prompts: [X - images: X, videos: X]
- Production quality grade: [PASS/grade]

Key Visual Notes for Editor:
1. [Most important directive]
2. [Second most important]
3. [Third most important]

Compliance Note for Reviewer:
[One sentence - what a medical/legal reviewer must check before publishing]
```
