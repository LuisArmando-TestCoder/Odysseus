# Odysseus — Página de descarga

Sitio estático (Next.js + React + SCSS, **sin Tailwind**, con **Framer Motion** y
**Lucide** para iconos) que aloja el enlace de descarga del instalador de
Odysseus, el ejecutable auto-instalable del repositorio.

## Stack

- **Next.js 14** (App Router) con `output: "export"` para servir 100% estático.
- **React 18**.
- **SCSS** — módulos y tokens de diseño centralizados en `app/globals.scss`.
- **Framer Motion** — animaciones de entrada, hover y estado activo.
- **Lucide React** — todos los iconos (sin assets externos).

## Desplegar desde el propio repositorio

El build genera una carpeta estática (`out/`) lista para servirse en
GitHub Pages, Cloudflare Pages, Netlify, o cualquier host estático. El workflow
`.github/workflows/deploy-website.yml` hace `next build` y publica `out/` en
GitHub Pages de forma automática en cada push.

## Desarrollo local

```bash
cd website
npm install
npm run dev        # http://localhost:3000
```

## Build de producción

```bash
cd website
npm install
npm run build      # genera website/out/