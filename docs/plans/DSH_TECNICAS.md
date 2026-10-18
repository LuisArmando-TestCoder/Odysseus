# DSH → Odysseus — técnicas portables de búsqueda y análisis

Base para dotar a Odysseus de las capacidades de DSH. Cada técnica está
extraída del comportamiento real de DSH (inspeccionado en esta sesión) y
mapeada al módulo concreto de Odysseus donde aterrizarla.

---

## 1. Metodología de búsqueda (el core)

**Principio:** un buscador sirve para *descubrir* la fuente autoritativa, nunca
para *responder*. Una vez conocida la fuente, se descarga el documento primario
y se hace grep local.

**Las 5 reglas:**

1. **Identificador único antes que nombre.** Nombres = homónimos = ruido.
   Persona → cédula (`6-0260-0212`); empresa → cédula jurídica
   (`3-101-429950`); inmueble → finca (`2-00321395-000`); caso → sumaria
   (`19-005889-0494-TR`). El ID único no puede devolver homónimo.
2. **Documento primario, no UI JS.** Si el sitio es JS-renderizado, ir a su capa
   de datos: REST API (`wp-json`), `sitemap.xml`, RSS, o el PDF estático.
3. **Extraer + grep local.** Descargado el documento, la "búsqueda" es
   determinista y no se puede bloquear: `pdftotext`/`pypdf` + `grep -C`.
4. **Paralelizar probes; un solo fallback, nunca loop.** Varias consultas/descargas
   a la vez; si una se bloquea, cambiar de *fuente*, no reintentar lo mismo.
5. **Buscar en el idioma local y acotar al dominio** (`site:` o el buscador del sitio).

---

## 2. Análisis de documentos (pipeline)

```
descargar (curl/urllib, UA de navegador)  → nunca al contexto
  → detectar tipo (file / magic)
  → extraer texto (PDF: pdftotext|pypdf · HTML: quitar tags · JSON: pretty)
  → grep case-insensitive con ±N líneas de contexto
  → solo el fragmento con hit entra al contexto
```

Verificación hecha: `docfind.py --file gaceta54.pdf --term "LEDEZMA"` encuentra
"Maynor de Jesús Ledezma Rojas … 3-101-229291" en un PDF de 2,1 MB en <1 s.

---

## 3. Gestión de contexto sin saturación

- **Spill a disco, leer por partes.** El resultado largo se escribe a archivo y
  solo se lee la cola o el fragmento relevante (`head`/`tail`/`read --offset`).
- **grep con número de línea, luego `read --offset/--limit`** alrededor del hit.
- **mmap para archivos enormes** (buscar en 100 MB sin cargarlos en memoria).
- **Jobs en background + lectura incremental** para tareas largas.
- **Nunca** meter el documento completo al contexto.

Odysseus ya tiene el patrón correcto en `src/context_tree.py` (chunks en disco +
grep por keyword): solo hay que aplicarlo a más fuentes.

---

## 4. Mapa de port (técnica DSH → módulo Odysseus)

| Técnica DSH | Dónde aterrizar en Odysseus |
|---|---|
| fetch-primary-doc + grep | `src/search/content.py` (web_fetch) + nuevo `docfind` tool |
| key única (cédula/finca/sumaria) | `src/osint_framework.py` / `src/osint_research.py` — añadir resolutores por ID |
| REST API / sitemap discovery | `src/search/content.py` — detectar WordPress (`wp-json`) y caer a `sitemap.xml` |
| parallel probes + fallback limpio | `src/search/universal.py` — multi-proveedor ya existe; añadir timeout corto + no-reintento-ciego |
| contexto disk-first + grep | `src/context_tree.py` (ya existe) — extender a resultados de búsqueda |
| checkpoint de ejecución | `src/checkpoint.py` (ya existe) |
| append de findings | `document_append` (ya existe) |

---

## 5. Toolkit Cóbano (registros públicos de Costa Rica)

| Fuente | Cómo se accede | Key única |
|---|---|---|
| **TSE** (tse.go.cr) | PDFs estáticos de declaratorias/acreditaciones | cédula, nº de resolución |
| **La Gaceta** (imprentanacional.go.cr / asamblea.go.cr) | PDF de cada boletín → grep | cédula jurídica, nombre |
| **Registro Nacional** (rnpdigital.com) | consulta personas/bienes (interactiva) | cédula, nº de finca, placa |
| **Municipalidad** (actas del concejo) | PDFs estáticos en el sitio | nombre, cédula |
| **Poder Judicial** (pjenlinea.poder-judicial.go.cr) | consulta de expedientes (interactiva) | nº de sumaria/expediente |
| **SURCOS Digital** (surcosdigital.com) | `wp-json/wp/v2/posts?search=` · `sitemap.xml` · RSS | slug, fecha |
| **SICOP** (sicop.go.cr) | app JS — consulta manual | cédula jurídica del proveedor |
| **CGR** (cgr.go.cr) | refrendos de contratos | cédula jurídica |

**Regla por fuente:** PDF estático → descargar y `docfind.py`; WordPress →
`wp-json`/`sitemap`; app JS con captcha/sesión → consulta manual guiada (no
scraping).

**Scrapers solo donde no haya API ni PDF** y con respeto a `robots.txt` y rate
limit. Para los casos Cóbano, la mayoría de fuentes son PDFs estáticos o
`wp-json` — no se necesitan scrapers complejos.

---

## 6. Rendimiento de Odysseus (lo ya diagnosticado)

**Backend (velocidad de requests):**
- `deep_research.max_time = 300` (5 min) corta la investigación → hacerlo setting.
- `max_empty_rounds = 2` + buscador frágil → retry en vez de bail.
- Sin heartbeats durante awaits largos → proxy mata el SSE.
- `search_engine_error.log` (SSL expirado/timeout) → timeout corto + fallback.

**Frontend (framerate):**
- Sin virtualización + `innerHTML` masivo (parcialmente arreglado: batch +
  `content-visibility`).
- Pendiente: throttle del streaming markdown, sticky-scroll cableado en
  `_scrollTranscript`, windowing real.

---

## 7. docfind.py

Utilitario autocontenido que encarna la técnica núm. 1–3:

```bash
python3 docfind.py --url "https://…/Gaceta%2054.pdf" --term "LEDEZMA" --context 2
python3 docfind.py --file gaceta54.pdf --term "3-101-229291"
python3 docfind.py --url "https://surcosdigital.com/wp-json/wp/v2/posts?search=Montezuma" --term "Rebeca" --json
```

Exit codes: `0` hit · `1` sin match · `2` error. Extrae PDF (pdftotext o pypdf),
HTML (strip tags), JSON (pretty), y hace grep case-insensitive con contexto.
