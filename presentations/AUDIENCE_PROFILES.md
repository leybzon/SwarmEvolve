# AUDIENCE_PROFILES.md - Adaptation Strategies for Different Audiences

This document provides adaptation strategies for presenting SwarmEvolve to different audience types.

---

## Audience Matrix

| Audience | Technical Depth | Time | Key Message | Success Metric |
|----------|----------------|------|-------------|----------------|
| **Researchers (AI/ML)** | Deep | 20-30 min | Methodological rigor, reproducibility | "I want to cite this" |
| **Engineers** | Medium-High | 15-20 min | How it works, performance, code | "I want to build this" |
| **General Tech** | Medium | 10-15 min | Why it matters, impact, demo | "I want to share this" |
| **Executives** | Low | 5-10 min | Cost, speed, ROI | "I want to fund this" |
| **Students** | Variable | 15-20 min | Learning opportunity, accessible | "I want to try this" |
| **Popular Science** | Low | 5-8 min | Story, wonder, accessibility | "I want to tell others" |

---

## 1. AI/ML Researchers

### Background
- Deep RL experts, evolutionary computation specialists
- Familiar with: Fitness landscapes, genetic algorithms, NEAT, AlphaStar
- Skeptical of: LLM hype, toy problems, non-reproducible results

### What They Care About
- ✅ Statistical significance
- ✅ Ablation studies
- ✅ Reproducibility
- ✅ Comparison to baselines
- ✅ Threats to validity
- ❌ Business value
- ❌ Oversimplified metaphors

### Presentation Adaptations
**Add**:
- Slide: Experimental design (seeds, match counts, acceptance criteria)
- Slide: Statistical tests (t-tests, confidence intervals)
- Slide: Ablation study (what if we disabled Formation Spread?)
- Slide: Related work comparison (AlphaStar, OpenAI Five, NEAT)

**Emphasize**:
- Single-seed caveat (M25 used seed=42 only)
- Low match count risk (n=10 creates variance)
- Reproduction command (`python3 scripts/evolve_coevolve.py`)

**Skip**:
- Biological metaphors (they know the theory)
- "Wow" moments (show rigor, not spectacle)

**Code Depth**: Show full functions, discuss loop guards, AST injection

---

## 2. Software Engineers

### Background
- Familiar with: C++, Python, GPU programming, CI/CD
- Interested in: Architecture, performance, scalability
- Practical mindset: "How would I use this?"

### What They Care About
- ✅ How does compilation work?
- ✅ How do you inject loop guards?
- ✅ What's the GPU utilization?
- ✅ Can this scale to 1000s of matches?
- ✅ How do I run it locally?
- ❌ Evolutionary theory details
- ❌ Statistical significance

### Presentation Adaptations
**Add**:
- Slide: Architecture diagram (Python orchestrator → C++ engine → GPU simulation)
- Slide: Safety mechanisms (AST injection code walkthrough)
- Slide: Performance metrics (matches/sec, GPU memory usage)

**Emphasize**:
- Cross-platform support (macOS CPU fallback, Linux GPU)
- Deterministic engine (same seed = same result)
- Open source, single-command reproduction

**Skip**:
- Evolutionary biology deep-dives
- Philosophical implications

**Code Depth**: Show AST injection, compilation flags, OpenACC pragmas

---

## 3. General Tech Audience (Default)

### Background
- Broad tech literacy, some programming knowledge
- Familiar with: AI basics, ChatGPT, general ML concepts
- Diverse motivations: curiosity, career, trends

### What They Care About
- ✅ What is this?
- ✅ Why does it matter?
- ✅ How is it different?
- ✅ Can I try it?
- ❌ Implementation details
- ❌ Statistical rigor

### Presentation Adaptations
**This is the base M25 presentation**

**Emphasize**:
- David vs Goliath narrative
- $10 vs $1M comparison (democratization)
- Red Queen Effect (engaging metaphor)
- Video demo (visual proof)

**Keep**:
- Code comparisons (Round 1 vs Round 31)
- Tactical evolution (6 phases)
- Power law innovation

**Skip**:
- AST injection details
- Statistical tests
- Ablation studies

**Code Depth**: 5-10 line excerpts, highlight key innovations

---

## 4. Executives / Decision-Makers

### Background
- Limited technical depth, high strategic focus
- Familiar with: ROI, competitive advantage, market trends
- Time-constrained: 5-10 minutes max

### What They Care About
- ✅ Cost savings
- ✅ Time to value
- ✅ Competitive differentiation
- ✅ Risk mitigation
- ❌ Technical implementation
- ❌ Code details

### Presentation Adaptations
**Restructure to 5 slides**:
1. **Problem**: Hand-coding is slow, AI hallucinates
2. **Solution**: LLM-guided evolution ($10, 1.5 hours)
3. **Proof**: Team B defeated 100-generation champion
4. **Impact**: Democratizes AI research (vs $1M AlphaStar)
5. **Call to Action**: Pilot program, open source adoption

**Emphasize**:
- Cost: $10 API credits vs $1M compute
- Speed: 1.5 hours vs weeks/months
- Interpretability: Human-readable C++ vs black-box neural networks
- Scalability: Single CPU/GPU vs hundreds of GPUs

**Skip**:
- All code
- Tactical evolution details
- Evolutionary theory

**Metrics Focus**: Dollar savings, time savings, performance gains

---

## 5. Students / Learners

### Background
- Variable technical background (undergrad to PhD)
- Motivated by: Learning, projects, career opportunities
- Resource-constrained: Limited compute, budget

### What They Care About
- ✅ Can I reproduce this?
- ✅ What will I learn?
- ✅ Is there a tutorial?
- ✅ Can I extend it for my project?
- ❌ Business value
- ❌ Production deployment

### Presentation Adaptations
**Add**:
- Slide: Learning roadmap (evolutionary algorithms → LLM prompting → GPU programming)
- Slide: Project ideas (extend to 3D, multi-species, cooperative tasks)
- Slide: Resources (GitHub, papers, Discord/Slack community)

**Emphasize**:
- Open source, permissive license
- Works on laptop (macOS CPU fallback)
- Single-command setup (`python3 scripts/evolve_coevolve.py`)
- Educational value (learn evolution, LLMs, GPU programming)

**Interactive**:
- Live demo: Run a 5-round evolution during talk
- Q&A focus: "What parameters should I tune first?"

**Code Depth**: Walkthrough of key functions, encourage exploration

---

## 6. Popular Science / TED-Style

### Background
- Non-technical general public
- Interested in: Wonder, stories, big ideas
- Attention span: 5-8 minutes

### What They Care About
- ✅ Why is this amazing?
- ✅ What does this mean for the future?
- ✅ Can you tell me a story?
- ❌ How does it work?
- ❌ Technical details

### Presentation Adaptations
**Restructure to story arc**:
1. **Hook**: "What if your AI could learn to beat itself?"
2. **David vs Goliath**: Team B (underdog) vs Team A (champion)
3. **The Journey**: Show fitness reversal graph, call it "the moment everything changed"
4. **The Secret**: Formation Spread (use wolf pack metaphor, no code)
5. **The Insight**: "Adaptability beats perfection"
6. **The Future**: "Imagine every AI learning this way"

**Emphasize**:
- Emotional arc (rooting for Team B)
- Biological metaphors (Red Queen, predator-prey, wolf packs)
- Human implications (what does this mean for AI?)

**Skip**:
- All code
- All statistics
- All technical architecture

**Visuals**: Videos, large graphs, minimal text

---

## Adaptation Decision Tree

```
                    Start Here
                        │
                ┌───────┴───────┐
        Technical?          Non-Technical?
            │                   │
    ┌───────┴─────┐       ┌─────┴─────┐
Research?    Eng?     Decision?    Wonder?
   │          │          │            │
Rigor    Architecture   ROI       Story
```

---

## Quick Adaptation Guide

### If audience is **skeptical**:
- Add: Statistical tests, ablation studies, threats to validity
- Show: Reproduction command, GitHub link, commit SHA

### If audience is **time-constrained**:
- Cut to 5 slides: Problem → Solution → Proof → Impact → Action
- Focus on: $10 vs $1M, 1.5 hours, fitness reversal

### If audience is **non-technical**:
- Remove: All code, architecture diagrams
- Add: Biological metaphors, videos, story arc

### If audience is **hands-on**:
- Add: Live demo, GitHub walkthrough, Q&A
- Provide: Setup instructions, Discord link, project ideas

---

## Feedback Signals

### Good Signs:
- ✅ Leaning forward
- ✅ Taking notes
- ✅ Asking clarifying questions
- ✅ "How can I try this?"

### Warning Signs:
- ⚠️ Checking phones
- ⚠️ Glazed expressions
- ⚠️ No questions at end
- ⚠️ "I didn't understand X"

**Adaptation Mid-Talk**:
- If losing technical audience → Add more rigor, skip metaphors
- If losing general audience → Add story, skip code
- If running long → Skip to conclusion slide

---

**Remember**: You can't please everyone. Choose your primary audience and optimize for them. Secondary audiences can adapt or ask questions.
