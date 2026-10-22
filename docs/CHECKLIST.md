# Checklist coverage

Mapped against the 12-section software-development guide. **Done** = implemented and tested.
**N/A** = does not apply to a local command-line tool (reason given). **Gap** = known and honest.

## 1. Requirements and planning
All **Done**: functional/non-functional requirements, user stories, acceptance criteria, edge cases,
business rules, constraints, scope limits. See `REQUIREMENTS.md`.

## 2. Architecture and code quality
| Item | Status |
|---|---|
| System architecture, separation of concerns, modularity | Done: `ARCHITECTURE.md`, one job per module |
| KISS / DRY | Done: stdlib only, shared helpers (`is_inside`, `safe_move`) |
| SOLID / design patterns | Done where useful: `LLMClient` protocol (dependency inversion), circuit breaker, journal-before-action |
| Technical debt, code smells, refactoring | Ruff enforced in CI; no known shortcuts. Gap: Windows file-lock path is untested in this sandbox |
| Dependency management | Done: zero runtime deps; optional extras pinned to ranges; Dependabot + `pip-audit` |
| API contracts | Done: JSON schema with category enum for the model; fake-server tests pin the Ollama request/response contract |

## 3. Database and data management
| Item | Status |
|---|---|
| Schema, data modeling, integrity | Done: foreign key, `CHECK` on status, primary keys |
| Normalization, indexes | Done: index on `moves(run_id)`; decision cache keyed by hash+model+prompt version |
| Transactions / ACID / concurrency | Done: SQLite transactions, WAL mode, single-instance lock |
| Migrations | Done: versioned via `PRAGMA user_version` |
| Backup and recovery | Done: `backup` command; crash recovery of `pending` moves |
| Retention and deletion | Done: `purge --days N` |
| Connection pooling | N/A: one local connection |

## 4. Security
| Item | Status |
|---|---|
| Input validation | Done: config validated at startup; model output validated against allow-list; filenames sanitized |
| Path traversal | Done: destination and undo targets must resolve inside `Sorted/` or the chosen folder |
| Prompt injection | Done: file text is fenced as untrusted data; even if obeyed, output is validated and cannot choose paths |
| Least privilege | Done: moves only; no delete, no overwrite, no network except loopback |
| Secrets management | Done: API key (baseline provider only) read from environment, refused in config files, hidden from `repr` |
| Dependency vulnerabilities | Done: `pip-audit` and `bandit` in CI |
| Encryption in transit/at rest | N/A by design (loopback traffic, local files). Disk encryption is the OS's job |
| Authentication, RBAC, CSRF, XSS, SQL injection, rate limiting | N/A for auth/web items (no accounts, no web UI). SQL uses bound parameters. Model calls are paced |
| Audit logging, threat model | Done: journal is the audit trail; `SECURITY.md` |

## 5. Error handling and reliability
Done: exceptions isolated per file; timeouts; retries with exponential backoff; circuit breaker;
graceful degradation (model down means files wait, rule-routed files still move); idempotent reruns
(hash cache, no-clobber moves); rollback via `undo` (verifies content hash first); crash recovery;
graceful shutdown on Ctrl+C / SIGTERM in `watch`. N/A: failover, dead-letter queue, disaster recovery
beyond `backup` (single local process).

## 6. Testing
| Item | Status |
|---|---|
| Unit, integration, CLI end-to-end | Done: 195 tests |
| Negative, boundary | Done: invalid config ranges, size/age/confidence limits, bad model JSON |
| Mocking | Done: fake model client; fake local HTTP server |
| Contract testing | Done for the Ollama HTTP API (fake server) |
| Security tests | Done: injection text, path traversal names, remote URL refusal |
| Regression/smoke | Done in CI on Linux and Windows |
| Coverage | 97% measured, 85% enforced. High coverage is not proof of quality |
| Load/stress/chaos | Partial: breaker and outage behavior tested; no large-folder soak test. Gap |
| Real-model run | Gap: not run in this sandbox. Use `doctor` and the evaluation harness on your machine |

## 7. Performance and scalability
Done: measured-not-guessed approach via the evaluation harness (latency p50/p95); rule shortcuts;
hash cache; bounded text (1500 chars); per-pass file cap (backpressure); CPU/AC gating; lower priority.
N/A: horizontal/vertical scaling, load balancing, pagination, N+1 queries.

## 8. DevOps, deployment, CI/CD
Done: Git-ready layout, GitHub Actions matrix (Linux + Windows, Python 3.11/3.12), lint + tests + security
scans, Dependabot, environment variables for config, local config separate from code.
N/A: containers, IaC, staging/prod, canary, feature flags (desktop tool). Rollback = reinstall previous version;
`undo` rolls back data changes.

## 9. Monitoring and observability
Done: structured JSON logs with per-run correlation id and rotation; file contents never logged; health
check (`doctor`); morning report; run summaries with counts, latency and token usage.
N/A: distributed tracing, SLO/SLA, alerting (single local process).

## 10. Frontend and UX
CLI only. Done: clear messages, dry-run default, explicit empty/error/success output, UTF-8-safe printing.
N/A: responsive design, accessibility standards for web, i18n, browser compatibility.
Gap: no GUI. OCR is slow on a weak CPU and optional.

## 11. AI and ML concerns
| Item | Status |
|---|---|
| Data leakage | Done: a separate held-out test set that is never tuned on; tests fail if any prompt example appears in either test set |
| Model evaluation | Done: 39-sample labelled corpus + `run_eval` (accuracy, accuracy on files it would move, files left for you, per-category, latency) + `compare` for side-by-side models. n=39 is a sanity check, not a benchmark |
| Hallucination / output validation / guardrails | Done: schema enum, allow-list check, confidence threshold, "unsure means Misc" |
| Prompt injection | Done and tested (corpus includes injection attempts) |
| Human-in-the-loop | Done: dry run, review via report, undo |
| Versioning and reproducibility | Done: model name and prompt version stored on every move; temperature 0 |
| Latency, cost | Done: measured per call; cost is zero for local use |
| Fallback strategy | Done: rules, then "wait for next run" |
| Privacy / data minimization | Done: only 1500 characters, local only; OCR runs on-device |
| Data/model drift, RAG evaluation, bias | N/A (no training, no RAG). Gap: categories and OCR are tuned to English-language documents (other scripts are untested) |
| Model security | Done: loopback-only default; response size cap |

## 12. Legal, privacy, compliance
Done: `PRIVACY.md`, `HACKATHON.md` (rules and partner notes), data minimization, retention control, local data residency by default, MIT license.
Dependencies are standard library or optional `pypdf`/`psutil` (permissive licenses). **Check the license of
whichever Ollama model you pull**; model licenses differ. Consent/data-subject-rights: N/A (single user, own files).
