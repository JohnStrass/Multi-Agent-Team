# Preliminary Design — A Format for LLM-Agent Work Exchange

*Abstract / exploratory. The goal is a serialization format better suited to one LLM handing structured work to another LLM than Markdown (built for humans), JSON (built for machines), or prose (built for human social exchange). This audits the premises first, because the most exciting framing of the idea rests on one that isn't true — and then proposes what is actually buildable.*

---

## 1. Premise audit (what survives scrutiny, what doesn't)

**Claim: "a format transformers understand *intrinsically*."** — **False as stated, true in conclusion.** A transformer understands nothing from architecture alone; a randomly-initialized one is gibberish. Comprehension comes from the **training distribution** — a model understands what it has seen a lot of. So no format is "intrinsically" parsed by virtue of being a transformer. **However**, the *conclusion* you reached — one universal transformer format, not `.qwen`/`.gemini` — is **correct**, for two reasons that aren't "intrinsic":
1. The properties that make a format easy for a transformer to process (below) are **architecture-general** — shared by all transformers — not model-specific.
2. A format built from **already-familiar tokens** is learnable on sight by any model trained on ordinary text.

So: universal-across-transformers, yes. Model-specific extensions, no. But the reason is *shared processing characteristics + shared training substrate*, not intrinsic understanding. The mechanism matters because it tells you what to build: not novel glyphs a model must be taught, but a disciplined convention over tokens every model already knows.

**Claim (from prior discussion): "a format matching how Claude thinks."** — **Doesn't survive.** An LLM thinks *in* tokens; the text is not a lossy export of a richer internal format the way your inner experience exceeds your words. There is no native sub-linguistic "thought" to serialize more faithfully. **Tokens already are the thinking.** So the target isn't cognition.

**What the target actually is:** a serialization format for the **artifacts your distributed-cognition pipeline already passes between agents** — work-handoffs, wiki entries, debriefs, reasoning dumps. The inefficiency is real, but it lives in the gap between those artifacts and the human/machine formats we currently force them through — not in any gap between thought and text.

---

## 2. The core reframe: design a SCHEMA, not a format

The valuable intellectual content is **deciding what fields these artifacts need.** Once the schema exists, the *encoding* (Markdown front-matter, YAML, XML tags, or a custom syntax) is almost an implementation detail — and the lightest robust encoding usually wins. So:

> **Design the schema first. Choose the encoding second. Build a fully custom format last, and only if measurement proves the encoding cost is actually hurting.**

This reframes your question productively: you didn't have an idea about transformer cognition. You had an idea about **your pipeline's data schema**, dressed as one about cognition — and the schema version is the one that's real, buildable, and useful.

---

## 3. What transformers actually process well (the real, architecture-level arguments)

*Honesty caveat: I can't introspect my own processing with privileged access. These are principled inferences from how transformers are known to work, not me reporting a felt sense of "what's easy for me."*

1. **Structure before content (the autoregressive argument — the strongest one).** A model reads left-to-right and conditions each token on what precedes it. Put the interpretive frame *before* the content it governs, so the model reads the content already primed to weight it. `confidence: 0.7 → [claim]` beats `[claim] (conf 0.7)`. Labels, types, and metadata go **first**, not trailing.
2. **Explicit structure over inferred.** Mark structure (delimiters, labels) rather than making the model infer it from prose. Cheap insurance.
3. **Locality of reference.** Attention can reach anywhere, but long contexts dilute it ("lost in the middle") and smaller models benefit from related things being adjacent. Keep a field and its value, a claim and its evidence, *near* each other.
4. **Token density → less dilution.** Every token of syntactic scaffolding (JSON braces, close tags) or social padding is context burned and dollars spent. Fewer tokens = more fits, less attention spread thin.
5. **Malformation tolerance.** JSON breaks catastrophically on a missing brace, so models spend effort and retries producing exactly-valid JSON. A format where minor malformation degrades *gracefully* (meaning survives a dropped delimiter) cuts the failure-and-retry tax.
6. **Familiar building blocks.** Comprehension is from training distribution → build from tokens models already saturate on (tags, key-value, Markdown), **never invented symbols**. Novel glyphs cost more in comprehension than they save in density.

There is a genuine tension between 4 (density) and 5/6 (robustness, familiarity): maximum compression = maximum fragility. The right point for LLM exchange is **moderate density with graceful degradation**, not maximal compression.

---

## 4. The schema (the actual content — the fields the pipeline needs)

One **container** format with a **type** field and type-specific payloads (like HTTP: one envelope, many content types). The first-class fields, derived from the chokepoints mapped in the stack:

- **type** — `work-handoff` | `wiki-entry` | `debrief` | `reasoning-dump`. Different artifacts, one envelope.
- **provenance** — which model/agent produced it, and when. (Needed for diversity-tracking, trust, and training-data attribution.)
- **confidence** — the producer's stated confidence. First-class, not buried in hedging prose.
- **verification** — passed a check? by whom? (`none` | `model-X` | `human`). The trust signal your routing needs.
- **supersession / recency** — `supersedes: <id>` and `superseded-by: <id>`. **This is the Distill/recency fix**, made native — a stale entry is *marked*, not left looking authoritative.
- **summary** — a one-line relevance hook, as its own cheap-read layer. **Maps directly to orchestrator-mediated retrieval**: the orchestrator reads summaries to judge relevance and injects only the bodies it needs. Progressive disclosure as a first-class feature.
- **reasoning** — the *path*, as a layer **structurally separated from the conclusion**. **This is the training-data fix**: the conversion pipeline needs the reasoning distinct from the answer, and no current format makes that native.
- **body** — the conclusion / payload itself.
- **deps / links** — related artifact IDs.

Note that four of these (**supersession, summary-layer, reasoning-separation, verification**) are exactly the things we spent the session identifying as missing from the wiki, the retrieval layer, and the training pipeline. The format isn't a new idea — it's a *serialization of the schema the stack already needs.*

---

## 5. Concrete sketch (one encoding option, not the final answer)

A lightweight labeled-block encoding, built from familiar tokens, structure-first, tolerant:

```
::artifact type=work-handoff id=h7f3 by=deepseek-v3 at=2026-06-29T14:22 conf=0.7 verif=none
::supersedes h2a1
::summary  Fix for the GGUF export OOM on the 12B — root cause was 4-bit not engaging.
::reasoning
bitsandbytes silently forced 16-bit on this card; confirmed via VRAM at load.
considered dynamic quant, rejected (overkill for the symptom).
::body
[the actual fix / code / conclusion]
::deps w9f4, h2a1
::end
```

Why this shape: metadata front-loaded (arg 1, 3); `::` is rare, cheap, and a malformed line stays readable (arg 5); `summary` is a separable cheap-read layer (retrieval); `reasoning` is its own block, separable from `body` (training data); built entirely from familiar tokens, so a model reads it cold (arg 6).

**But:** this custom `::` syntax is the *least* proven choice. Models are far more deeply trained on **XML-style tags** (robust, self-describing, tolerant — a missing close tag degrades gracefully) and **YAML front-matter** (dense, familiar). For a first prototype, encoding the schema in **XML tags or Markdown front-matter** is the disciplined move — prove the *schema* works in a format models already handle perfectly, and only move to a denser custom syntax if measured token cost at high-volume seams justifies it.

---

## 6. The extension / naming question

**Lowest-stakes decision in the whole design** — name it last, and let it follow function. Two honest notes:
- **Avoid `.tr`.** It implies "transformer-intrinsic," which we established is the wrong mechanism, and it collides with existing uses. Name it for what it *is* — an agent-work-exchange format — e.g. `.aw`, `.agentx`, or just `.llmx`. It genuinely doesn't matter; don't spend thought here.
- **Do not build model-specific extensions** (`.qwen`, `.gemini`). The beneficial properties are architecture-general and your whole stack is heterogeneous — one format your whole swarm shares is the entire point.

---

## 7. The honest cost/benefit (does this earn its keep?)

A custom format is net-positive only if:

```
(token savings + structural gains + first-class metadata value)
        >
(comprehension cost of a non-native format + per-session prompting overhead
        + maintenance for a one-person project with no ecosystem)
```

The uncomfortable findings:
- **A novel format is a second language.** Models are fluent in Markdown/JSON/XML because those saturate training data. A format you invent, they reason in less fluently — which can *cost* more capability than density saves. This is the dominant risk.
- **Per-session prompting tax.** No model knows your format natively; every session you spend context teaching it. At low volume this never pays back.
- **One person, no ecosystem.** No tooling, no libraries, only you maintain it.
- **Strict conventions over existing formats probably capture ~80% of the gain at ~5% of the cost.** A disciplined Markdown front-matter schema, or XML tags with your fields, gets you provenance/confidence/supersession/summary/reasoning-separation *today*, in a format models already handle — without inventing anything.

**Conclusion: the schema is worth building; a fully custom format is not, yet.** The custom encoding earns its keep only at the **specific seams where the schema is rich and the volume is high** — the wiki entries and the work-handoffs — and only after you've *measured* that the encoding cost (not the schema's absence) is the bottleneck. For everything else, strict conventions over a familiar format win.

---

## 8. Recommendation — what to actually prototype

1. **Define the schema** (Section 4 fields) — this is the real work and it's pure upside.
2. **Encode it in a familiar, robust format first** — Markdown front-matter or XML tags. Deploy it at the two high-value seams (wiki entries, work-handoffs). This alone delivers supersession, summaries-for-retrieval, reasoning-separation, and provenance — the things the stack is missing.
3. **Instrument the token cost** at those seams. If syntactic overhead is genuinely large and frequent, *then* consider a denser custom encoding (Section 5) — and A/B it: does the density gain beat the comprehension/prompting cost? (Same baseline discipline as everything else.)
4. **Name the extension last**, when there's something real to name.

The honest one-line verdict: **there is no transformer "thought-format," because tokens are the thought — but there is a genuinely valuable schema for your pipeline's artifacts, and the right move is to build the schema, carry it in a format models already know, and let measurement, not aesthetics, decide whether a custom `.llmx` is ever worth inventing.**
