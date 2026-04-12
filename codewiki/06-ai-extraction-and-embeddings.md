# AI extraction and embeddings

## Summary

You will understand how ContextEdge calls **language models** through **LiteLLM**, how **embeddings** power semantic search, and how **classification** and **JSON extraction** tasks (episodes, patterns, contradictions) are structured—without treating the AI layer as a black box unrelated to governance.

## Business picture

Machines read tickets faster than humans, but their output must be **reviewable**. The platform uses models to **summarize**, **classify**, **cluster semantically**, and **propose structure** (steps, root cause, outcomes). Cheap or fast models can triage; stronger models can reconstruct narratives. Everything still sits under tenant isolation and human lifecycle states (draft episodes, playbook review)—AI suggests; governance approves.

## Technical walkthrough

1. **Provider abstraction** — `ai/provider.py` configures LiteLLM (API keys, Vertex credentials, retries). `MODEL_ROUTING` picks defaults per **task** key: `classification`, `extraction`, `embedding`. `get_model_for_task` resolves the model id.

2. **Completion** — `llm_complete` sends a chat-style `acompletion`; `llm_complete_json` requests `response_format` JSON and parses with error logging on failure.

3. **Embeddings** — `generate_embedding` calls `litellm.aembedding` with fixed **3072** dimensions (aligned with Vertex / model expectations). Used by `embed_evidence` in `ai/embeddings.py` (title + body, truncated body) and by search (`vector_search`, `hybrid_ranker` query embedding). `embed_evidence_batch` fills sparse texts with zero vectors to keep array shapes consistent.

4. **Relevance classification** — `ai/classifiers/relevance.py` `classify_relevance` builds a short prompt and returns `classification`, `confidence`, `reasoning` via `llm_complete_json`. The Celery task `classify_relevance_task` in `extraction_tasks.py` invokes this to move evidence out of `unclassified`.

5. **Episode reconstruction** — `ai/extractors/episode_extractor.py` `reconstruct_episode` sends ordered evidence text through a large structured prompt; output is a list of episodes with steps and confidence. `episode_service.create_episodes_from_evidence` persists `Episode` and `EpisodeStep` rows.

6. **Other extractors** — `ai/extractors/pattern_extractor.py` and `identity_extractor.py` (pattern and identity flows) follow the same “prompt + JSON” style where used by services.

7. **Contradiction scanning** — `contradiction_service.py` uses `llm_complete_json` to compare playbook steps to KB-style evidence and may add graph edges via `add_contradicts_edge`.

8. **Configuration** — Model names and keys come from `config.py` / environment (documented in setup and runbook); changing models is an ops concern, not a code change.

## Design decisions

- **LiteLLM as a single integration point** — *Why:* swap OpenAI, Anthropic, Vertex without rewriting call sites. *Tradeoff:* behavior varies slightly by provider; testing must cover the configured backend.

- **Fixed embedding dimension** — *Why:* consistent pgvector column shape and distance math. *Tradeoff:* changing embedding models may require re-embedding and migration.

- **Task-based model routing** — *Why:* use a smaller/faster model for classification, a stronger one for extraction. *Tradeoff:* misconfiguration affects quality more than code bugs.

- **JSON-mode extraction** — *Why:* structured downstream ORM mapping. *Tradeoff:* models occasionally violate schema; code must validate and log (`llm_json_parse_failed`).

## Code map

| Concern | Module path | Key symbols | When it runs |
| --- | --- | --- | --- |
| LLM + embeddings | `backend/src/contextedge/ai/provider.py` | `llm_complete`, `llm_complete_json`, `generate_embedding`, `get_model_for_task` | Many services |
| Evidence vectors | `backend/src/contextedge/ai/embeddings.py` | `embed_evidence`, `embed_evidence_batch` | Normalize, batch jobs |
| Relevance | `backend/src/contextedge/ai/classifiers/relevance.py` | `classify_relevance` | Worker / pipeline |
| Episodes | `backend/src/contextedge/ai/extractors/episode_extractor.py` | `reconstruct_episode` | Episode creation |
| Episode persist | `backend/src/contextedge/services/episode_service.py` | `create_episodes_from_evidence` | API / tasks |
| Contradictions | `backend/src/contextedge/services/contradiction_service.py` | (LLM-assisted compare) | Evaluation tasks |
| Celery hooks | `backend/src/contextedge/workers/extraction_tasks.py` | `classify_relevance_task`, `reconstruct_episode_task`, `generate_embeddings` | Queues |

## Acme VPN incident (this layer)

The classifier marks VPN tickets as **operational**; embedding groups them near past “certificate expiry” evidence; `reconstruct_episode` proposes one episode titled **Corporate VPN authentication failure** with ordered steps from Jira + Teams + mail, leaving `reviewer_state` pending human approval.

## Further reading

- [05-search-hybrid-and-access.md](./05-search-hybrid-and-access.md) — where embeddings are queried  
- [07-episodes-patterns-playbooks.md](./07-episodes-patterns-playbooks.md) — lifecycle around AI outputs  
- [`docs/SETUP_GUIDE.md`](../docs/SETUP_GUIDE.md) — provider keys and env  
