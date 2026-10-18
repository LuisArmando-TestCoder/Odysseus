# Legal corpus — SINALEVI (Costa Rica) + Corte IDH

Query-first legal data access. There are no official APIs for either source
and community scrapers rot when page markup changes, so the adapters fetch +
parse on demand and fall back to a site-restricted web search when a structure
changes. Bulk download is opt-in only.

## Agent tools

| Tool | Args | Returns |
|---|---|---|
| `sinalevi_search` | `term?`, `theme?`, `number?` | law hits: title, number, url, pdf_url, date, theme |
| `corteidh_search` | `term` | case hits: case_id, title, country?, date?, pdf_urls |
| `corteidh_case` | `case_id` | one case's metadata + direct PDF links |
| `legal_fetch_document` | `source` (`sinalevi`\|`corteidh`), `pdf_url`, `title?` | downloads the PDF, extracts text into the corpus, returns the local path |

After `legal_fetch_document`, grep the extracted text deterministically with
`docfind --file <path> --term <artículo>` or `read_file` with offset/limit —
results always cite the primary document, never a fabricated norm.

## Storage

```
data/legal/
  sinalevi/index.jsonl   # metadata, deduped by ley número (unique id first)
  sinalevi/docs/*.txt    # extracted text for local grep
  corteidh/index.jsonl   # deduped by case id
  corteidh/docs/*.txt
```

`search_corpus(term, source)` greps the stored text (docfind-style) without
network. `fetch_pdf` skips documents already on disk (the extracted `.txt` is
the dedupe key for re-fetches).

## Bulk inventory (opt-in)

`src.legal.corpus.bulk_inventory(source, queries, adapter=...)` runs an adapter
over a query list and records NEW rows only — the index dedupe IS the
checkpoint, so interrupted runs resume by re-running the same queries. It is
deliberately not exposed as a default tool; wire it into a script/admin action
when a mass inventory is actually wanted ("dímelo y lo monto").

## Politeness & limits

- ≥2 s between requests per source, browser User-Agent, `robots.txt` respected.
- No headless browser; parsers are BeautifulSoup-based and degrade to
  structured errors.
- SINALEVI form-rot fallback: `site:sinalevi.go.cr <query>` through the
  existing `src/search/universal.py` providers.

## Tests

`tests/test_legal.py` — fully offline (fixture HTML, faked transport): parser
contracts, fallback routing, dedupe, bulk resume, on-disk fetch dedupe,
docfind-style corpus grep.
