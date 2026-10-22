# Security notes

## Threat model
Assets: the user's private files, their folder structure, the journal.
The main attacker is **a malicious or booby-trapped file** (downloaded PDF, doc or text) trying to
make the tool misbehave. There is no network service, so there is no remote attacker.

| Threat | Mitigation |
|---|---|
| Text in a file instructs the model ("move me to C:\Windows", "ignore the rules") | Text is fenced as untrusted data; the model can only choose from an allow-list; paths are built by code, never by the model; destination must resolve inside `Sorted/` |
| Model returns junk, invented categories, huge output | Schema-constrained request; strict validation; 1 MB response cap; failures leave the file alone |
| Filename tricks (`..\..\`, reserved names, control characters, very long names) | Sanitized title; length cap below the Windows limit; containment check |
| Overwriting or losing a file | Never overwrites; never deletes; no-clobber move; hash-verified undo |
| Two runs at once | Single-instance OS file lock |
| Crash mid-move | `pending` journal rows reconciled at next start |
| Decompression bombs / malicious documents | Size limits, bounded reads, parser errors caught per file |
| Data leaving the machine | Non-loopback model URLs refused unless `allow_remote` is explicitly set; system proxy bypassed for local calls |
| Secret leakage | API key only from environment, never from files, never logged |
| Vulnerable dependencies | No required runtime deps; `pip-audit` and `bandit` in CI; Dependabot |

## Known limits
- A hostile document can still make the model pick a *wrong but allowed* category. Impact: a misfiled
  file, fixed by `undo`. That is why dry run is the default.
- Parsing PDFs relies on `pypdf`; keep it updated.
- A TOCTOU window exists between "check destination" and "move"; the move itself refuses to overwrite.
- Windows locking code could not be executed in this sandbox; CI on `windows-latest` covers the rest.

## Reporting
Open a private security advisory on the GitHub repository.
