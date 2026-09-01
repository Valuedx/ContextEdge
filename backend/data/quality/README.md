# Playbook quality — tenant data (not application code)

All product-specific governance lives here as **JSON per tenant**, never in Python validators.

## Layout

| Path | Purpose |
|------|---------|
| `default_policy_pack.json` | Empty tenant template (zero rules) |
| `default_ontology.json` | Empty tenant template (zero terms) |
| `artifact_routing.json` | Evidence-type → artifact routing (generic) |
| `examples/<profile>/policy_pack.json` | Optional product example pack |
| `examples/<profile>/ontology.json` | Optional product example ontology |

## Create flow (like Process Studio)

1. **Define ontology** — canonical component names and aliases for *this* tenant's product.
2. **Define policy pack** — normalized actions the tenant discourages or forbids.
3. **Seed to database** — one active pack + one active ontology version per tenant.
4. **Run readiness** — `scripts/verify_quality_persistence.py` and quality pytest suite.
5. **Enable enforcement** — set `PLAYBOOK_QUALITY_MODE=enforcing` when shadow data is acceptable.

## Seed commands

```bash
# Empty templates (generic tenant — validators run, policy/ontology empty until you add JSON)
python backend/scripts/seed_quality_policy_pack.py --tenant <uuid>

# Product example profile (AutomationEdge only — not loaded by default)
python backend/scripts/seed_quality_policy_pack.py --tenant <uuid> --profile automationedge

# Custom tenant JSON files
python backend/scripts/seed_quality_policy_pack.py --tenant <uuid> \
  --policy-pack path/to/my_policy_pack.json \
  --ontology path/to/my_ontology.json
```

## Policy pack schema

```json
{
  "version": "string",
  "owner": "string | null",
  "notes": "string | null",
  "rules": [
    {
      "normalized_action": "lowercase action phrase",
      "decision": "forbidden | discouraged | allowed",
      "alternative_action": "string | null",
      "rationale": "string | null",
      "source_kind": "review_adjudication | runtime | manual"
    }
  ]
}
```

## Ontology schema

```json
{
  "version": "string",
  "owner": "string | null",
  "terms": [
    {
      "canonical_term": "Display Name",
      "term_kind": "product | component | capability | environment",
      "aliases": ["alias1", "alias2"],
      "parent_term": "string | null"
    }
  ]
}
```

After changing JSON on disk, restart workers or call `seed_data.clear_quality_data_cache()` so loaders pick up edits.


## The `product` term

Exactly one term per ontology may use `"term_kind": "product"`. It names the
product the tenant runs, and it is the **only** place the system learns that
name:

```json
{ "term_kind": "product", "canonical_term": "Acme RPA", "aliases": ["ARPA"] }
```

It is read by `quality_policy_service.active_product_label()` and reaches the
generation prompt through
`knowledge_retrieval_service.format_knowledge_block(documents, product_label)`,
where it renders the KB version caveats:

```
with a product term     [kb-1] … — PRODUCT VERSION: Acme RPA 8.2.3 (matches ticket 8.2.3)
without a product term  [kb-1] … — PRODUCT VERSION: 8.2.3 (matches ticket 8.2.3)
```

Omitting it is supported and produces the second form. That is accurate for a
tenant that has not declared a product, and it is deliberately not defaulted:
a hardcoded default in this path is what previously put one customer's product
name into every other customer's prompt.
