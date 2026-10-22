# Requirements and planning

## Problem
Download and desktop folders fill with unnamed files. Sorting them by hand is a chore people avoid.
Existing "smart" tools upload your documents to a cloud service, which is unacceptable for bank,
identity and medical files.

## Functional requirements
| ID | Requirement |
|---|---|
| F1 | Scan the top level of a chosen folder and find files worth sorting |
| F2 | Classify text-bearing files (txt, md, csv, docx, pdf) with a local model via Ollama |
| F3 | Route images, media, installers and archives by extension, with no model call |
| F4 | Move files into `Sorted/<Category>/` with a readable name; never overwrite |
| F5 | Leave low-confidence or unreadable files untouched and list why |
| F6 | Default to dry run; require `--apply` to change anything |
| F7 | Journal every move; `undo` restores a run |
| F8 | Produce a morning report; `watch` mode repeats politely |
| F9 | Provide a measurable evaluation harness |

## Non-functional requirements
| Area | Target |
|---|---|
| Privacy | Default config sends file text only to a loopback address; no telemetry |
| Safety | No deletion, no overwrite, no writes outside the chosen folder |
| Performance | Usable on a dual-core CPU with no GPU: tiny model, 1500 chars per file, rule shortcuts, hash cache |
| Reliability | Survives model outages, timeouts, crashes mid-move, locked files |
| Portability | Windows, macOS, Linux; Python 3.11+; no required third-party packages |
| Maintainability | Small modules, typed, linted, at least 85% test coverage enforced |

## User stories and acceptance criteria
1. *As someone with a messy Downloads folder, I want a preview before anything moves.*
   Accept: `run` without `--apply` changes no files and prints each planned move.
2. *I want to reverse a bad result.*
   Accept: `undo` restores moved files; it refuses (and says why) if a file changed since, is missing,
   or the original path is now occupied.
3. *I want my private documents to stay on my machine.*
   Accept: a non-local model URL is rejected unless explicitly opted in.
4. *I want it to stay out of my way.*
   Accept: `watch` skips passes on battery or high CPU and runs at lower priority.
5. *I want mistakes to be rare, not hidden.*
   Accept: confidence below the threshold leaves the file in place and lists it in the report.

## Edge cases handled
Empty/unreadable/scanned files, encrypted PDFs, oversized files, files still downloading, files
modified moments ago, symlinks, name collisions, very long names, files vanishing mid-run, model
down or returning invalid JSON, categories the model invents, text that tries to give the model orders.

## Business rules
Category must be in the configured allow-list (`Misc` always exists as the fallback). Rule-based routes
have confidence 1.0; model routes need `min_confidence`. Names are `YYYY-MM-DD_<title><ext>` using the file's
modified date and a model-suggested title.

## Constraints and assumptions
Target machine: low-end CPU, no GPU, limited RAM, so only small models are practical. Ollama must be
installed separately. Assumes the user tidies one folder at a time and owns it.

## Scope control (out of scope for v1)
OCR, GUI, cloud sync, recursive scanning, learning from user corrections, multi-user support.
