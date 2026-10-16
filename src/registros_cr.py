"""src/registros_cr.py — Costa Rica public-records source catalog (Cóbano).

Encodes the domain knowledge for the Cóbano neighbors' investigations: for each
authoritative CR source, whether it is automatable (static PDF / REST API) or
manual (interactive UI with login/captcha), and the UNIQUE KEY to use (cédula,
finca, sumaria, cédula jurídica) so name-search homonym noise is avoided.

The agent pairs this with the `docfind` tool: for automatable sources it builds
the fetch URL and greps the primary document; for manual sources it reports the
exact steps + key the human must use.
"""
import json
from typing import Any, Dict, Optional

# Each entry:
#   automatable: "pdf" | "wp-json" | "manual"
#   key: the UNIQUE identifier that defeats homonyms
#   how: what to do (URL pattern / manual steps)
SOURCES: Dict[str, Dict[str, Any]] = {
    "tse": {
        "name": "Tribunal Supremo de Elecciones (TSE)",
        "automatable": "pdf",
        "key": "cédula · nº de resolución (DRPP / declaratoria)",
        "how": (
            "Buscar la 'declaratoria de elección' o resolución DRPP del cantón "
            "(Orotina/Puntarenas/Cóbano) en tse.go.cr/pdf/...; descargar el PDF "
            "y grep por nombre + cédula con `docfind`. Ahí están concejales, "
            "síndicos y sus cédulas exactas."
        ),
    },
    "gaceta": {
        "name": "La Gaceta (Imprenta Nacional / Asamblea)",
        "automatable": "pdf",
        "key": "cédula jurídica (3-101-XXXXXX) · nombre · nº de aviso",
        "how": (
            "Descargar el boletín PDF (imprentanacional.go.cr/gaceta o la copia "
            "asamblea.go.cr/sd/rep_cedil/...) y grep por cédula jurídica o nombre "
            "completo. Ahí están sociedades, asambleas, y contratación directa."
        ),
    },
    "registro_nacional": {
        "name": "Registro Nacional (personas / bienes)",
        "automatable": "manual",
        "key": "cédula física (6-XXXX-XXXX) · cédula jurídica (3-101-XXXXXX) · finca (P-NNNNNN) · placa",
        "how": (
            "Consulta interactiva en rnpdigital.com (requiere sesión). Key: la "
            "cédula o nº de finca, NO el nombre. Ver propiedades, vehículos, "
            "hipotecas, y representantes de sociedades."
        ),
    },
    "poder_judicial": {
        "name": "Poder Judicial (expedientes / causas)",
        "automatable": "manual",
        "key": "nº de sumaria/expediente (ej. 19-005889-0494-TR)",
        "how": (
            "Consulta de expedientes en pjenlinea.poder-judicial.go.cr por número "
            "de sumaria, NO por nombre. La key sale de registros de bienes "
            "(levantamientos) o de la prensa."
        ),
    },
    "surcos": {
        "name": "SURCOS Digital (prensa local)",
        "automatable": "wp-json",
        "key": "slug · fecha · término exacto",
        "how": (
            "WordPress: usar la REST API con `docfind --json`: "
            "https://surcosdigital.com/wp-json/wp/v2/posts?search=<término>&per_page=20 "
            "o sitemap.xml / RSS si la API falla."
        ),
    },
    "municipal": {
        "name": "Actas del concejo municipal / de distrito",
        "automatable": "pdf",
        "key": "nombre · cédula · nº de acuerdo",
        "how": (
            "Las actas son PDFs estáticos en el sitio de la municipalidad "
            "(muniorotina.go.cr, etc.). Descargar y grep por nombre/cédula; ahí "
            "están permisos, contratos y votaciones."
        ),
    },
    "sicop": {
        "name": "SICOP (contratación pública)",
        "automatable": "manual",
        "key": "cédula jurídica del proveedor",
        "how": (
            "App JS en sicop.go.cr (consulta de proveedores/procedimientos). "
            "Key: cédula jurídica (3-101-XXXXXX) del proveedor. Manual, no scrapeable."
        ),
    },
    "cgr": {
        "name": "Contraloría General de la República (refrendos)",
        "automatable": "manual",
        "key": "cédula jurídica",
        "how": "cgr.go.cr — refrendos/contratos. Manual, por cédula jurídica.",
    },
}


def source_help(name: Optional[str] = None) -> str:
    """Return a compact cheat-sheet for one source, or all of them."""
    lines: list = []
    items = {name: SOURCES[name]} if name in SOURCES else SOURCES
    for k, s in items.items():
        mode = {"pdf": "AUTOMATABLE (docfind)", "wp-json": "AUTOMATABLE (docfind --json)",
                "manual": "MANUAL (instrucciones)"}[s["automatable"]]
        lines.append(
            f"## {k} — {s['name']} [{mode}]\n"
            f"  key: {s['key']}\n"
            f"  how: {s['how']}"
        )
    return "\n\n".join(lines)


def resolve_url(source: str, query: str) -> Optional[str]:
    """Build the fetch URL for the automatable sources (pdf / wp-json).
    Returns None for manual sources (the agent should print the manual steps)."""
    import urllib.parse
    s = SOURCES.get(source)
    if not s:
        return None
    q = urllib.parse.quote(query)
    if source == "surcos":
        return f"https://surcosdigital.com/wp-json/wp/v2/posts?search={q}&per_page=20"
    # tse / gaceta / municipal need the concrete document URL discovered first
    # (declaratoria / boletín / acta); return None so the agent uses the `how`
    # text + docfind on the specific PDF it finds.
    return None


async def do_registros_cr(content: str, owner: Optional[str] = None,
                          session_id: Optional[str] = None) -> Dict[str, Any]:
    """Tool: return the CR public-records source cheat-sheet (one source or all).

    JSON: {"source": "tse"} → one source; {} or {"source": ""} → all sources.
    """
    try:
        parsed = json.loads(content) if isinstance(content, str) and content.strip().startswith("{") else {}
    except (json.JSONDecodeError, TypeError):
        parsed = {}
    if not isinstance(parsed, dict):
        parsed = {}
    name = parsed.get("source") or parsed.get("name") or None
    if name and name not in SOURCES:
        return {"error": f"unknown source '{name}'. Available: {', '.join(sorted(SOURCES))}", "exit_code": 1}
    return {"output": source_help(name), "exit_code": 0}
