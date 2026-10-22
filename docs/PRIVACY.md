# Privacy

- **What is read:** the first ~1500 characters of text from supported documents, plus file names and sizes. With `--ocr`, scanned PDF pages (first 2 by default) and images are read on your own CPU; images and extracted text are not stored.
- **Where it goes:** to the Ollama server on your own machine (`127.0.0.1`). Nothing is sent anywhere else.
  A non-local server is refused unless you set `allow_remote = true`.
- **What is stored:** a local SQLite journal in `<folder>\.slothdesk\` (file names, categories, hashes, model
  name, confidence) and a log file. File **contents are never stored or logged**.
- **Telemetry:** none.
- **Retention:** history stays until you run `slothdesk purge --days N` or delete the `.slothdesk` folder.
  Purging history disables `undo` for those runs; it never touches your files.
- **Baseline evaluation:** the optional closed-model comparison sends only the synthetic sample corpus,
  never your files, and only if you set it up yourself.
- **Sensitive files:** medical, financial and identity documents are exactly why this runs locally.
  The tool does not store or interpret their contents beyond choosing a folder.
