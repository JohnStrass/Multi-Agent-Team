# LLM Work-Exchange Format — v0 Spec, Findings & Implementation Guide

*Written by Claude Code (Opus 4.8) on 2026-06-29, from the seat that ran the experiments.
Audience: another LLM (incl. the Linux Claude building the coding training set) that needs to
implement, emit, parse, or fine-tune on this format. Companion to
`llm-format-preliminary-design.md` (the design audit) — this is the part that was tested.*

---

## 0. What this is, in one breath
A lightweight, structure-first serialization for **one LLM agent handing structured work to
another** — handoffs, wiki entries, debriefs, reasoning dumps. It is a **schema** (the fields
that matter) carried in a **dense, familiar-token encoding** (the `::` line format below). It
is NOT a new file type and NOT "transformer-intrinsic" — it's a disciplined convention over
tokens every model already knows.

**The lossless-master principle (the organizing idea):** an agent emits the *schema-complete*
artifact (all fields, including the reasoning path) as the **master**. Flattened views for a
specific consumer — clean prose for a human, terse JSON for a tool, a one-line summary for a
retrieval layer — are **derived from the master on demand, never authored instead of it.**
(Think FLAC → MP3: master once, derive the lossy copies. You can always drop fields later;
you can never recover the reasoning path you didn't capture.)

---

## 1. What was found (the measured verdict — don't overstate it)
Tested across model families: qwen2.5-coder-14b (local), DeepSeek, Kimi (cloud), and
gemma3:1b / llama3.2:1b (tiny). Same artifact, custom `::` encoding vs plain XML.

| Test | Result |
|---|---|
| **Read cold** (no format explanation) | 100% field-extraction, all 3 capable families, both encodings |
| **Write cold** (reproduce from 1 example) | 100% structurally valid + correct, all 3 families, both encodings |
| **Malformation** (delimiter deleted) | meaning survived in both — semantic content carried the boundary |
| **Token density** | custom `::` ≈ **17% fewer tokens** than XML for identical content |
| **Weak (1B) models** | errors on **both** encodings, ~equally — and the errors were **schema-clarity**, not syntax |

**Precise claims (and the borders):**
- Against **prose/markdown** (how agents hand off today): **less lossy** — it has slots for
  confidence, provenance, and the reasoning/conclusion split that prose drops.
- Against **JSON/XML**: **equally lossless**, but **~17% cheaper** and **degrades gracefully**
  instead of shattering on a typo.
- For **capable models (14B+)**: read + write + damage-tolerant + cheaper. Net win at the seams.
- For **1B models**: unreliable in ANY format → keep tiny models out of the rich-artifact loop,
  or feed them a stripped schema. The fix is clearer field names, not a different encoding.
- **Still unmeasured (the deciding number):** the *misread rate at volume*. 100% on a few dozen
  reads is not 100% at a million; if the true misread rate is >~1–2%, retries could eat the 17%.
  Measure that before betting big.

---

## 2. The schema (the fields — this is the real content)
One **envelope**, a **type**, type-specific payload. Fields, in canonical (structure-first) order:

| field | meaning | notes |
|---|---|---|
| `type` | `work-handoff` \| `wiki-entry` \| `debrief` \| `reasoning-dump` | one envelope, many kinds |
| `id` | short unique id for this artifact | for supersession + deps |
| `from` | producing model/agent | **renamed from `by`** — the 1B test confused `by`/`verif` |
| `at` | ISO-8601 timestamp | |
| `conf` | producer's confidence, `0..1` | first-class, not buried in hedging prose |
| `verified` | `no` \| `<verifier>` (e.g. `claude`) | **explicit `no`**, not `none`, for clarity |
| `supersedes` | id this replaces (optional) | the recency/Distill fix, made native |
| `summary` | one-line relevance hook | the cheap-read layer for orchestrator retrieval |
| `reasoning` | the **path** — diagnosis, options weighed | kept STRUCTURALLY SEPARATE from body |
| `body` | the conclusion / code / payload | the answer itself |
| `deps` | related artifact ids (optional) | |

The four load-bearing ones — `supersedes`, `summary`, `reasoning` (separated), `verified` —
are exactly the things ad-hoc formats lose. Reasoning-separation is the **training-data fix**:
a converter can teach a model the *path*, not just the answer.

---

## 3. The `::` encoding (precise rules — implementable cold)
```
::artifact type=work-handoff id=h7f3 from=deepseek-v3 at=2026-06-29T14:22 conf=0.7 verified=no
::supersedes h2a1
::summary Fix for the GGUF export OOM on the 12B — root cause was 4-bit not engaging.
::reasoning
bitsandbytes silently forced 16-bit on this card; confirmed via VRAM at load.
considered dynamic quant, rejected (overkill for the symptom).
::body
Set load_in_4bit=True explicitly and pin bnb_4bit_compute_dtype; verified VRAM dropped at load.
::deps w9f4, h2a1
::end
```

**Rules:**
1. A line beginning with `::` (at column 0) starts a field. `::` anywhere else is literal text.
2. The first line is `::artifact` followed by space-separated `key=value` header attributes
   (values have no spaces; if a value must contain spaces, wrap in single quotes).
3. Single-value fields (`::supersedes`, `::summary`, `::deps`) put their value on the same line.
4. Block fields (`::reasoning`, `::body`) put content on the FOLLOWING lines, until the next
   `::` line. Preserve the content verbatim.
5. End with `::end`.
6. **Ordering is metadata → summary → reasoning → body → deps** (structure-first: the frame is
   read before the content it governs — the autoregressive advantage).

---

## 4. How another LLM implements it

**To EMIT (the important direction — always emit the master):**
- Fill every field you can; never silently drop `reasoning` or `verified`.
- If unverified, write `verified=no` — do not omit it.
- Put the interpretive metadata FIRST, the payload last.
- Emit this master even if the immediate consumer only wants prose — derive the prose separately.

**To PARSE:**
- Split on lines starting with `::`. The token after `::` is the field name; header attrs are
  `key=value` on the `::artifact` line. Block fields accumulate following lines until the next `::`.
- A missing delimiter should NOT be fatal — fall back to semantic content (tested: meaning
  survives a dropped `::body`). Graceful degradation over strict validation.

**To DERIVE a lossy view (master → MP3):**
- *Human prose*: render `summary` + `body`, drop the metadata.
- *Tool/JSON*: map fields 1:1 to keys.
- *Retrieval index*: keep only `id` + `summary` + `from` + `conf`; fetch `body`/`reasoning` on demand.
Never store a derived view as if it were the master.

---

## 5. Fine-tuning a small model to speak it (the "where this shines" path)
Capable models do all of the above **zero-shot**. The opportunity: make a **4B / quantized-7B**
coding worker do it **reliably** (the 1B models could not). Training-data shape — two directions:

- **PARSE / extract:** input = an artifact in `::` format; target = the field values (or a
  flattened view). Teaches robust reading.
- **EMIT / generate:** input = a raw task/scenario; target = the schema-complete `::` artifact,
  *with the reasoning block populated separately from the body.* Teaches the master discipline.
- **DERIVE:** input = a master artifact; target = a specific lossy view (prose / JSON / summary).

Source the pairs from real work (the git/aider/delegation corpora + the Linux training set):
take exchanges where code passed verification → wrap (task → reasoning + working-code) as a
`::artifact`. A worker fine-tuned this way both *produces* clean handoffs AND *reads* them — so
the whole heterogeneous swarm speaks one cheap, lossless-at-the-seam language. That is the payoff:
not a smarter model, a **cheaper, more legible pipeline.**

---

## 6. To reproduce / extend the validation
1. Author one artifact in `::` and in XML (same content).
2. Cold-read test: hand each to fresh models (no format explanation) + extraction questions;
   score field accuracy. (Result: 100% on capable models, both formats.)
3. Generation test: give one example, ask for a NEW artifact for a different scenario; check
   structural validity. (Result: 100% on capable models.)
4. Malformation test: delete a delimiter; re-ask. (Result: meaning survived.)
5. Token count: compare encodings. (Result: `::` ≈17% lighter.)
6. **Next, unrun:** volume test — push a few hundred auto-generated artifacts through the
   readers, measure the per-field misread rate. THAT decides whether it's worth building for real.

---

## 7. Naming — decide last
The extension genuinely doesn't matter; name it for what it is (agent work-exchange), not
"transformer-intrinsic." `.aw` / `.llmx` / `.agentx` are all fine. **Do not** build model-specific
variants (`.qwen`, `.gemini`) — the win is one format the whole swarm shares.
