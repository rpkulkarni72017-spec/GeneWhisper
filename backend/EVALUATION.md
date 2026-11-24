# Phenotype evaluation readiness

## What can be evaluated now

The current prototype can be checked for schema and provenance validation,
deterministic mapping behavior against the pinned HPO release, human-review and
stale-submission handling, and in-memory/SQLite persistence and projection
consistency. The extraction module adapts supplied extraction results; it does
not implement or evaluate a clinical NLP extractor. These are software
correctness checks, not clinical performance estimates.

## Ground truth required

Before reporting performance, establish a documented, authorized benchmark
with:

- Stable case and source-document identities, document revisions, and data
  provenance.
- Expert-annotated mention spans and text, assertion status, temporality,
  experiencer, and canonical HPO identifiers tied to a recorded ontology
  release.
- A documented review/adjudication process, including disagreement handling,
  no-finding cases, and annotation completeness.
- Patient- or family-grouped development and held-out splits fixed before
  tuning, with benchmark access and exclusions recorded.

Do not treat unverified dataset columns, mappings, or labels as reference truth.

## Metrics to report

- **Extraction:** strict exact-span precision, recall, and F1; optionally a
  separately defined relaxed-overlap score. Report assertion, temporality, and
  experiencer classification metrics on matched mentions, plus end-to-end
  mention-level scores.
- **HPO mapping:** candidate Recall@k, top-1 accuracy, and mean reciprocal rank
  against adjudicated canonical terms. Report exact-ID and any ontology-aware
  score separately, with the ontology version and obsolete/alternative-ID
  handling specified.
- **Integrated phenotype sets:** case-level precision, recall, and F1 for the
  human-confirmed phenotype projection. Keep candidate retrieval, human
  decisions, and final projections as distinct stages; report per-case and
  aggregate results.

Use case-level grouping for confidence intervals and summaries so repeated
mentions from one case do not appear independent.

## Limitations and leakage risks

The current tests use small synthetic examples and do not measure real-world
language variability, prevalence, annotator disagreement, or generalization.
They do not establish clinical accuracy or suitability for diagnosis. No gene
prioritization performance is evaluated by this phenotype workflow.

Prevent leakage from duplicate or near-duplicate documents, repeated cases or
families across splits, post hoc summaries that expose annotations, and test
labels reused during rule or prompt tuning. Freeze the HPO release and mapping
configuration for each evaluation; prevent benchmark labels or reviewer
decisions from feeding back into the held-out evaluation.

Synthetic tests verify code paths and invariants only. They are authored from
expected behavior rather than independently adjudicated clinical evidence, so
their passing results cannot be interpreted as clinical validity.
