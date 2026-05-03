# VISUALS.md - Visual Design Language & Metaphors

This document defines the visual design language for SwarmEvolve presentations: color psychology, metaphors, chart types, and animation principles.

---

## Color Psychology

### Team Colors
| Color | Hex | Usage | Psychology |
|-------|-----|-------|------------|
| **Team A Blue** | `#4285f4` | Established champion, static perfection | Trust, stability, corporate, cold |
| **Team B Red** | `#ea4335` | Challenger, adaptive underdog | Energy, passion, danger, action |
| **Breakthrough Green** | `#34a853` | Innovation moments, success | Growth, nature, progress, go |
| **Fragility Gray** | `#888888` | Failures, dead-ends, stagnation | Neutral, death, obsolescence |
| **Highlight Gold** | `#f4b400` | Attention, warnings | Caution, value, "vibe coding" |

### Emotional Color Mapping
- **Defeat**: Red fading to gray
- **Victory**: Blue overwhelmed by red
- **Breakthrough**: Gray → Green
- **Stagnation**: Blue flatline
- **Evolution**: Gradient (gray → blue → red → green)

---

## Visual Metaphors Library

### 1. Fitness Graphs as Landscapes
**Concept**: Fitness over time = altitude on a mountain range

**Visual Elements**:
- **Valleys**: Low fitness periods (Team B -0.8)
- **Plateaus**: Stagnation (Rounds 3-12)
- **Peaks**: Breakthroughs (Round 31 jump to +0.9)
- **Cliffs**: Failures (Team A's 47 rejected mutations)

**Usage**:
- Show fitness line chart overlaid on mountain silhouette
- Label peaks with tactical innovations
- Annotate valleys with "searching for solution"

---

### 2. Tactical Evolution as DNA/Phylogenetic Trees
**Concept**: Code evolution = genetic lineage

**Visual Elements**:
- **DNA Helix**: Code mutations (C++ → C++ with changes)
- **Branches**: Tactical phases (pursuit → coordination → kiting → prediction → formation → zone)
- **Extinct Branches**: Failed mutations (grayed out)
- **Trunk**: Common ancestor (pursuit_v1 baseline)

**Usage**:
- Phylogenetic tree showing 6 tactical phases
- DNA helix graphic with C++ code snippets as "base pairs"
- Mutation markers at breakthrough rounds

---

### 3. Arms Race as Predator-Prey Cycles
**Concept**: Team A vs Team B = predator vs prey population dynamics

**Visual Elements**:
- **Oscillating lines**: Population cycles (lynx vs hare)
- **Phase lag**: Prey rises, then predator rises, then prey crashes
- **Convergence**: Both populations stabilize (Round 41)

**Usage**:
- Overlay fitness graph on predator-prey cycle diagram
- Label: "Team A Kite" → "Team B Predict" → "Team A Can't Adapt" → "Team B Wins"

---

### 4. Formation Spread as Territorial Coverage
**Concept**: 80-unit spacing = wolf pack territory

**Visual Elements**:
- **Voronoi diagram**: Each drone "owns" a zone
- **Heatmap**: Coverage density (60% arena coverage)
- **Animal territory map**: Wolf packs, lion prides, bee hives

**Usage**:
- Show arena before (clustered drones) vs after (spread formation)
- Overlay Voronoi cells on drone positions
- Compare to wolf pack hunting formation

---

### 5. Local Optimum as Mountain Peak
**Concept**: Team A stuck on local peak, can't reach global summit

**Visual Elements**:
- **3D landscape**: Multiple peaks of varying heights
- **Team A**: Trapped on needle peak (high but isolated)
- **Team B**: Exploring valleys, finds true summit
- **Descent path**: Blocked by fitness valley

**Usage**:
- 3D fitness landscape with two peaks
- Team A on narrow spire (fragile)
- Team B on broad plateau (robust)

---

## Chart Design Patterns

### 1. Fitness Over Time (Primary Hero Chart)
**Best For**: Showing the evolutionary journey

**Design**:
```
Team A (Blue)  ──────────────
                              \
                               \  Team B (Red)
Team B (Red)    ─────────────────┘
                ↑              ↑
              Round 13      Round 31
            (Parity)     (Breakthrough)
```

**Enhancements**:
- Shaded area between lines (competitive advantage)
- Annotations at key rounds
- Fragment reveal (show R0-10, then R11-30, then R31-95)

---

### 2. Tactical Staircase (Punctuated Equilibrium)
**Best For**: Showing innovation jumps

**Design**:
```
+1.0 ┤         ┌─────────
     │         │
 0.0 ┤    ┌────┘
     │    │
-0.8 ┤────┘
     └─────────────────→
     R1   R13   R31
```

**Enhancements**:
- Color each step with tactical phase
- Label steps with innovation names
- Annotate rise height (+0.4, +0.2, +0.9)

---

### 3. Code Complexity Scatter
**Best For**: Showing LOC vs fitness relationship

**Design**:
```
Fitness ↑
   +1.0 │       ● Team A (stuck)
        │
   +0.5 │           ● Team B (evolved)
        │
   -0.5 │   ●
        │  ● Team B (early)
        └─────────────────→ LOC
        50   100   150   200
```

**Enhancements**:
- Connect Team B dots with evolution path (arrow)
- Show Team A mutations as gray Xs (failed)
- Size bubbles by round number

---

### 4. Tactic Network Graph
**Best For**: Showing counter-strategies

**Design**:
```
Kiting ──countered by──> Prediction
   ↑                         │
   │                         ↓
Targeting <──countered── Formation
```

**Enhancements**:
- Use NetworkX directed graph
- Color edges: Green = successful counter, Red = failed
- Node size = effectiveness (fitness gain)

---

## Animation Principles

### 1. Progressive Reveal (Fragments)
**Use For**: Building suspense, step-by-step explanations

**Pattern**:
1. Show problem (Team B -0.8)
2. Reveal attempt 1 (Round 3, +0.4 gain)
3. Reveal attempt 2 (Round 13, +0.2 gain)
4. **Reveal breakthrough** (Round 31, +0.9 gain) ← Pause here

**Timing**: 2-3 seconds per fragment

---

### 2. Fade-In for Context
**Use For**: Supporting diagrams, not main message

**Pattern**:
- Main message (text): Instant
- Supporting chart: Fade-in 0.5s
- Background image: Already visible

---

### 3. Highlight → Zoom
**Use For**: Code snippets, calling attention to key lines

**Pattern**:
```cpp
// Gray out context
for (int i = 0; i < num_allies; i++) {
    // Highlight the innovation
    const float min_spacing = 80.0f;  ← ZOOM HERE
    // Gray out rest
}
```

**CSS**:
```css
.highlight-line {
    background: rgba(52, 168, 83, 0.3);
    transform: scale(1.05);
    transition: all 0.3s;
}
```

---

### 4. Morph Transitions
**Use For**: Before → After transformations

**Pattern**:
- Show Round 1 code (66 LOC)
- Morph to Round 31 code (187 LOC)
- Highlight new sections in green

**Implementation**: Use CSS transitions or SVG morphing

---

## Typography Hierarchy

### Slide Title (h2)
- **Font Size**: 3rem (48px)
- **Weight**: Bold (700)
- **Color**: White
- **Purpose**: The "What" (e.g., "The Red Queen Effect")

### Slide Subtitle (h3)
- **Font Size**: 2rem (32px)
- **Weight**: Regular (400)
- **Color**: Light gray (#e0e0e0)
- **Purpose**: The "Why" (e.g., "Reversing an Insurmountable Gap")

### Body Text
- **Font Size**: 1rem (16px)
- **Weight**: Regular (400)
- **Color**: White
- **Purpose**: The "How" (supporting details)

### Metric Numbers
- **Font Size**: 3-4rem (48-64px)
- **Weight**: Bold (700)
- **Color**: Breakthrough Green (#34a853)
- **Purpose**: Highlight key stats

---

## Icon Library

Use these icons for tactical categories:

| Tactic | Icon | Meaning |
|--------|------|---------|
| **Message Coordination** | 📡 | Radio broadcast |
| **Predictive Intercept** | 🎯 | Targeting precision |
| **Formation Spread** | 📐 | Geometric spacing |
| **Zone Control** | 🕸️ | Territorial web |
| **Kiting** | 🏃 | Retreat movement |
| **Pursuit** | 🔍 | Chase/search |

---

## Chart Color Palettes

### Sequential (Fitness Growth)
```
Low   ──────────────────→  High
#888  #4285f4  #34a853  #f4b400
Gray   Blue     Green     Gold
```

### Diverging (Team A vs Team B)
```
Team A  ←──────────→  Team B
#4285f4    #888      #ea4335
Blue       Gray       Red
```

### Categorical (Tactical Phases)
```
Pursuit: #888
Coordination: #4285f4
Kiting: #ea4335
Prediction: #34a853
Formation: #f4b400
Zone: #9c27b0 (purple)
```

---

## Visual Anti-Patterns

### ❌ Chartjunk
**Problem**: 3D charts, excessive gridlines, decorative elements

**Fix**: Flat 2D charts, minimal gridlines, data-ink ratio > 0.5

### ❌ Rainbow Gradients
**Problem**: Using all colors with no semantic meaning

**Fix**: Use Team A/B colors consistently, reserve green for breakthroughs

### ❌ Tiny Text
**Problem**: Font size < 14px on slides

**Fix**: Minimum 16px body text, 48px titles

### ❌ Cluttered Slides
**Problem**: >5 visual elements competing for attention

**Fix**: One hero visual per slide, supporting elements fade to background

---

## Visual Checklist for Each Slide

- [ ] **Color consistency**: Team A = Blue, Team B = Red?
- [ ] **Text minimalism**: <30 words per slide?
- [ ] **Visual hierarchy**: Clear primary focus?
- [ ] **Legibility**: All text readable from 20 feet away?
- [ ] **Metaphor clarity**: Will audience understand the analogy?
- [ ] **Animation purpose**: Does movement enhance or distract?

---

**Remember**: Visuals should **support** the narrative, not replace it. When in doubt, simplify.
