---
name: quant-lab-jev-route
description: "Jev-scored skill routing for quant-lab: one omh_jev_ask noul call scores every ml4t-*/quant-lab-* skill in this repo, then loads the ones above threshold. Use when the user says: jev route, route skills, which skills apply, jev pick the skills."
when_to_use: "Use at the start of a quant-lab research turn when the right ml4t-* skills are not obvious, or when the user asks which skills apply. Not for docs/typo work, and not when the task already names its skill."
dependencies: []
metadata:
  hermes:
    tags: [quant-lab, routing, jev, research]
    category: research
    role: router
    requires_tools: [omh_jev_ask]
---

# quant-lab Jev route

One Jev call scores the whole catalog; thresholds decide what loads. This is the
prompt-level equivalent of the omp `skill-router` extension — same judge contract,
same 0.70/0.30 thresholds — without a hook, so nothing is gated and nothing is
inherited by subagents.

## Preconditions

- Call only when this turn's user message asked for Jev or for skill routing. Never
  auto-run on every turn.
- `omh_jev_ask` must be configured (`OPENROUTER_API_KEY` plus
  `{"openrouter_route": true}` in `~/.omh/jev/settings.json`). If it returns
  `key_missing`, stop and report; do not guess a selection.

## Steps

1. Build the catalog from frontmatter only — one command, do not read the skill bodies:

   ```sh
   cd ~/dev/quant-lab && for d in .agents/skills/*/SKILL.md; do
     awk 'BEGIN{n="";w=""} /^name:/{sub(/^name:[ ]*/,"");gsub(/"/,"");n=$0}
          /^when_to_use:/{sub(/^when_to_use:[ ]*/,"");gsub(/^"|"$/,"");w=$0}
          /^---$/ && NR>1 {print n"\t"w; exit}' "$d"; done
   ```

2. Call `omh_jev_ask` **once**, with one `noul` question per skill:

   - `state`: `{"intent": "<the user's task, verbatim or tightly paraphrased>", "cwd": "<repo root>"}`
   - `questions`: `{"<skill-name>": {"type": "noul", "instructions": "Is <skill-name> relevant to this task? <when_to_use, ≤160 chars>", "criteria": {"true": "the skill's guidance applies", "false": "it does not apply"}}}`
   - `model`: `"jev-1.13"` — the version the 0.70/0.30 thresholds were tuned against. Do not switch versions mid-comparison.
   - `route`: `"auto"`

   Every catalog id must be in the map; a missing answer is a hard error, not a partial
   route. One call measures ~460 ms and ~$0.0002.

3. Apply the thresholds to the `noul` probabilities:

   - `≥ 0.70` → **selected**
   - `0.30 – 0.70` → **ambiguous**: list them, load at most three, and only when
     fewer than three roots were selected
   - `< 0.30` → dropped

4. Dependency closure: read `dependencies:` from each selected skill's frontmatter
   and add those transitively (the dependency name may be unprefixed; `lookahead-bias`
   means `ml4t-lookahead-bias`). Cycle-safe, topological order, dependencies load
   before dependents.

5. Load bodies by path, not by name: `~/dev/quant-lab/.agents/skills/<name>/SKILL.md`.
   Stop at **32 KiB** of loaded body text; drop the lowest-probability roots first and
   record them under `dropped_by_cap`, never as ambiguous, so the audit row stays honest.

6. Append one audit row so the selection is inspectable later:

   ```sh
   printf '%s\n' "{\"at\":\"$(date -u +%FT%TZ)\",\"intent_sha256\":\"$(printf %s "$INTENT" | sha256sum | cut -d' ' -f1)\",\"selected\":$SELECTED_JSON,\"ambiguous\":$AMBIGUOUS_JSON,\"dropped_by_cap\":$DROPPED_JSON,\"injected_bytes\":$BYTES,\"served_model\":\"$SERVED\",\"status\":\"$STATUS\"}" >> ~/.local/state/quant-lab-routing.jsonl
   ```

7. Report one line: selected skills with their probabilities, ambiguous count,
   injected KiB, served model, cost. Then answer the user's task using those skills.

## Boundaries

- Probabilities are a routing signal, not evidence of quality or approval.
- No gate: this does not block tools. If routing matters for a mutating action, use
  the omp session in this repo instead, where the extension refuses mutations on
  unresolved coverage.
- Empty selection is a real answer for non-research work (docs, typos). Say
  "no research skill applies" and continue; do not force a skill.
