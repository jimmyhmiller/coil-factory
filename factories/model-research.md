---
name: model-research
description: Find out what a newly offered free model really is, with sources, so the paper factory can rank it.
input: A repository holding MODEL.json, the model's catalogue entry, and check_research.py.
harness: codex
model: gpt-5.5
tools: files, shell
attempts: 2
reviewers: none
---

OpenCode Zen has started offering a free model, described in MODEL.json (its
OpenCode id, display name, release date and limits). The paper factory ranks
free models by testing them itself; this research tells a person what the model
is and supplies published evidence about how capable it is.

Search the web and read primary sources: the developer's announcement or model
card, OpenCode's announcements and docs, Artificial Analysis, Hugging Face,
OpenRouter. Establish:

- Whether it is a known, named model from an identified developer, or a stealth
  or codename model whose origin is undisclosed. For a stealth model, report
  community guesses only as guesses and never as identity.
- The developer and the underlying named model, if known.
- Total and active parameter counts, if published.
- Whether weights are open.
- A published, independent capability score, preferring the Artificial Analysis
  Intelligence Index. Give the value and the exact page it came from. A score
  from the vendor's own materials is not independent; say so in notes instead.
- Data terms of the free offer, for example whether prompts are used for
  training.

Link a source for every claim. Write null for anything you could not establish
from a source; never estimate. Conflicting sources go in notes with both links.

Write RESEARCH.json:

    {"model": "<the OpenCode id from MODEL.json>",
     "identity": "known" or "stealth",
     "developer": string or null,
     "underlying_model": string or null,
     "parameters_total": string or null, e.g. "550B",
     "parameters_active": string or null, e.g. "55B",
     "open_weights": true, false or null,
     "published_index": null or {"name": "Artificial Analysis Intelligence Index", "value": number, "url": "https://..."},
     "data_terms": string or null,
     "summary": "one or two plain sentences: what this model is",
     "notes": "conflicts, caveats, guesses",
     "sources": ["https://...", ...]}

Run `python3 check_research.py` before finishing.

## Station: research
gate: python3 check_research.py

Research the model in MODEL.json and write RESEARCH.json as described above.
