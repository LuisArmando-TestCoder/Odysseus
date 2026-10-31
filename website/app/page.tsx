"use client";

import "./page.scss";

import { useEffect, useRef, useState } from "react";
import { motion, MotionConfig } from "framer-motion";
import {
  Monitor,
  Laptop,
  Apple,
  Download,
  Terminal,
  RefreshCw,
  ShieldCheck,
  KeyRound,
  Link2,
  FileText,
  ArrowRight,
  ArrowDown,
  Copy,
  Check,
  Github,
  MessagesSquare,
  Bot,
  Radar,
  FolderSearch,
  BrainCircuit,
  Mail,
  type LucideIcon,
} from "lucide-react";

// ── Single source of truth ──────────────────────────────────────────────
// Each OS resolves to its own bootstrap executable (installs git + Python,
// clones the repo, runs the credential wizard). Served same-origin from
// /public so the `download` attribute forces a real file download.
const REPO_URL = "https://github.com/LuisArmando-TestCoder/Odysseus";
const CTA_LABEL = "Descargar Odysseus";

type OsKey = "macos" | "windows" | "linux";

// The one-line commands fetch the installer from the tip of main, so they
// always run the newest version. On macOS/Linux they're the most reliable
// path: a downloaded .sh opens in a text editor and Gatekeeper blocks
// unsigned downloads, while a script fetched by curl runs as-is.
const RAW = "https://raw.githubusercontent.com/LuisArmando-TestCoder/Odysseus/main/installer/bootstrap";

const OS: Record<
  OsKey,
  { label: string; icon: LucideIcon; file: string; run: string; alt: string; needs: string }
> = {
  macos: {
    label: "macOS",
    icon: Apple,
    file: "/install-macos.sh",
    run: `/bin/bash -c "$(curl -fsSL ${RAW}/install-macos.sh)"`,
    alt: "¿Descargaste el archivo? bash ~/Downloads/install-macos.sh",
    needs: "macOS 12+ · instala Xcode CLT y Python 3.12 si faltan",
  },
  windows: {
    label: "Windows",
    icon: Laptop,
    file: "/install-windows.cmd",
    run: `irm ${RAW}/install-windows.ps1 | iex`,
    alt: "O doble clic en install-windows.cmd (SmartScreen: Más información → Ejecutar de todas formas)",
    needs: "Windows 10/11 · instala Git y Python 3.12 con winget",
  },
  linux: {
    label: "Linux",
    icon: Monitor,
    file: "/install-linux.sh",
    run: `bash -c "$(curl -fsSL ${RAW}/install-linux.sh)"`,
    alt: "¿Descargaste el archivo? bash ~/Downloads/install-linux.sh",
    needs: "Debian, Ubuntu, Fedora, Arch, openSUSE · usa tu gestor de paquetes",
  },
};
const OS_ORDER: OsKey[] = ["macos", "windows", "linux"];

function detectOs(): OsKey | null {
  if (typeof navigator === "undefined") return null;
  const ua = navigator.userAgent.toLowerCase();
  const p = (navigator.platform || "").toLowerCase();
  // Phones and tablets can't run the installers: let the visitor pick.
  if (/iphone|ipad|ipod|android|mobile/.test(ua)) return null;
  if (ua.includes("win") || p.includes("win")) return "windows";
  if (ua.includes("mac") || p.includes("mac")) return "macos";
  if (ua.includes("linux") || ua.includes("x11") || p.includes("linux")) return "linux";
  return null;
}

// ── Content ─────────────────────────────────────────────────────────────
const BOOT = [
  ["núcleo", "OK"],
  ["memoria vectorial", "OK"],
  ["agente · herramientas MCP", "OK"],
  ["investigación profunda", "OK"],
  ["enlace local", "SEGURO"],
] as const;

const STEPS = [
  {
    n: "01",
    title: "Descarga",
    body: "Un único ejecutable para tu sistema. Sin cuentas, sin instaladores de terceros.",
  },
  {
    n: "02",
    title: "Ejecuta",
    body: "Instala git y Python si faltan, clona el repositorio y prepara el entorno. Puedes repetirlo sin riesgo.",
  },
  {
    n: "03",
    title: "Configura",
    body: "Un asistente pide solo las llaves que faltan, con enlace a la guía oficial de cada una, y escribe tu .env.",
  },
] as const;

const PROTOCOL: { icon: LucideIcon; title: string; body: string }[] = [
  { icon: Terminal, title: "Un paso", body: "Clona, configura y arranca desde un solo archivo." },
  { icon: KeyRound, title: "Llaves guiadas", body: "Cada variable enlaza a su documentación oficial." },
  { icon: RefreshCw, title: "Se actualiza solo", body: "Revisa main cada hora, reinicia en un momento tranquilo y vuelve atrás si algo falla." },
  { icon: Link2, title: "Una app", body: "Abrir y detener en los tres sistemas; repara su entorno y se reinicia si se cae." },
  { icon: ShieldCheck, title: "Secretos 0600", body: "Tu .env nunca se imprime en pantalla." },
  { icon: FileText, title: "Transparente", body: "Modelo de amenazas y hoja de ruta incluidos." },
];

const MODULES: { icon: LucideIcon; code: string; title: string; body: string; tags: string }[] = [
  {
    icon: Radar,
    code: "INV",
    title: "Investigación profunda",
    body: "Ejecuciones de varios pasos que buscan, leen y sintetizan fuentes en un informe visual.",
    tags: "multi-paso · fuentes citadas · informe",
  },
  {
    icon: FolderSearch,
    code: "INT",
    title: "Expedientes de inteligencia",
    body: "Cada investigación es un caso: entidades sin duplicados, hallazgos con fuente, relaciones y próximos pivotes.",
    tags: "casos · watchlist · dossier en un clic",
  },
  {
    icon: Bot,
    code: "AGT",
    title: "Agente autónomo",
    body: "Dale herramientas y deja que complete la tarea entera: web, archivos, shell, skills y memoria.",
    tags: "MCP · subagentes · metas continuas",
  },
  {
    icon: MessagesSquare,
    code: "COM",
    title: "Chat con cualquier modelo",
    body: "Modelos locales o por API, y un recetario que mide tu hardware y recomienda qué correr.",
    tags: "Ollama · llama.cpp · vLLM · OpenRouter",
  },
  {
    icon: BrainCircuit,
    code: "MEM",
    title: "Memoria que evoluciona",
    body: "Memoria persistente y skills: tu asistente entiende mejor tus tareas con cada uso.",
    tags: "ChromaDB · vector + palabra clave",
  },
  {
    icon: Mail,
    code: "OPS",
    title: "Correo, notas y calendario",
    body: "Triage de correo con IA, recordatorios, tareas programadas y sincronización CalDAV.",
    tags: "IMAP/SMTP · CalDAV · tareas cron",
  },
];

// ── Small pieces ────────────────────────────────────────────────────────
const reveal = {
  initial: { opacity: 0, y: 28 },
  whileInView: { opacity: 1, y: 0 },
  viewport: { once: true, margin: "-60px" },
  transition: { duration: 0.8, ease: [0.16, 1, 0.3, 1] },
} as const;

function Reactor() {
  // Arc-reactor rings: pure SVG + CSS rotation, decorative only.
  const ticks = Array.from({ length: 60 });
  return (
    <svg className="reactor" viewBox="0 0 600 600" aria-hidden="true">
      <defs>
        <radialGradient id="core" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor="#eaffff" stopOpacity="0.9" />
          <stop offset="18%" stopColor="#4fd8ff" stopOpacity="0.55" />
          <stop offset="60%" stopColor="#4fd8ff" stopOpacity="0.06" />
          <stop offset="100%" stopColor="#4fd8ff" stopOpacity="0" />
        </radialGradient>
      </defs>
      <circle cx="300" cy="300" r="300" fill="url(#core)" className="reactor__core" />
      <g className="ring ring--a">
        <circle cx="300" cy="300" r="286" className="ln-dim" />
        {ticks.map((_, i) => (
          <line
            key={i}
            x1="300"
            y1={i % 5 === 0 ? 6 : 12}
            x2="300"
            y2="22"
            className={i % 5 === 0 ? "ln" : "ln-dim"}
            transform={`rotate(${i * 6} 300 300)`}
          />
        ))}
      </g>
      <g className="ring ring--b">
        <circle cx="300" cy="300" r="236" className="ln" strokeDasharray="120 28 8 28" />
      </g>
      <g className="ring ring--c">
        <circle cx="300" cy="300" r="196" className="ln-dim" strokeDasharray="2 10" />
        <path d="M300 104 A196 196 0 0 1 496 300" className="ln-hi" />
        <path d="M300 496 A196 196 0 0 1 104 300" className="ln-hi" />
      </g>
      <g className="ring ring--d">
        <circle cx="300" cy="300" r="150" className="ln" strokeDasharray="40 14" />
      </g>
      <circle cx="300" cy="300" r="112" className="ln-dim" />
    </svg>
  );
}

function Frame({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return (
    <div className={`frame ${className}`}>
      <span className="frame__tick frame__tick--tl" aria-hidden="true" />
      <span className="frame__tick frame__tick--tr" aria-hidden="true" />
      <span className="frame__tick frame__tick--bl" aria-hidden="true" />
      <span className="frame__tick frame__tick--br" aria-hidden="true" />
      {children}
    </div>
  );
}

function Cta({ os, where }: { os: OsKey | null; where: string }) {
  const href = os ? OS[os].file : "#instalar";
  return (
    <a className="cta" href={href} download={os ? true : undefined} data-cta={where}>
      <span className="cta__glow" aria-hidden="true" />
      <Download size={18} strokeWidth={2.2} aria-hidden="true" />
      <span className="cta__label">
        <span data-text={CTA_LABEL}>{CTA_LABEL}</span>
      </span>
    </a>
  );
}

// ── Page ────────────────────────────────────────────────────────────────
export default function HomePage() {
  const [os, setOs] = useState<OsKey | null>(null);
  const [tab, setTab] = useState<OsKey>("macos");
  const [copied, setCopied] = useState(false);
  const [clock, setClock] = useState("");
  const [hidden, setHidden] = useState(false);
  const [scrolled, setScrolled] = useState(false);
  const [chapter, setChapter] = useState(1);
  const heroRef = useRef<HTMLElement>(null);

  useEffect(() => {
    const d = detectOs();
    setOs(d);
    if (d) setTab(d);

    const tick = () =>
      setClock(
        new Date().toLocaleTimeString("es", { hour: "2-digit", minute: "2-digit", second: "2-digit" }),
      );
    tick();
    const id = setInterval(tick, 1000);

    // Hide header on scroll down, show on scroll up.
    let last = 0;
    const onScroll = () => {
      const y = window.scrollY;
      setHidden(y > 160 && y > last);
      setScrolled(y > 40);
      last = y;
    };
    addEventListener("scroll", onScroll, { passive: true });

    // Chapter index follows the section in view.
    const io = new IntersectionObserver(
      (entries) =>
        entries.forEach((e) => {
          if (e.isIntersecting) setChapter(Number((e.target as HTMLElement).dataset.chapter));
        }),
      { threshold: 0.45 },
    );
    document.querySelectorAll<HTMLElement>("[data-chapter]").forEach((s) => io.observe(s));

    // Subtle pointer parallax on the hero (desktop pointers only).
    const hero = heroRef.current;
    const fine = matchMedia("(pointer: fine)").matches;
    const calm = matchMedia("(prefers-reduced-motion: reduce)").matches;
    const onMove = (e: PointerEvent) => {
      if (!hero) return;
      const r = hero.getBoundingClientRect();
      hero.style.setProperty("--px", ((e.clientX - r.left) / r.width - 0.5).toFixed(3));
      hero.style.setProperty("--py", ((e.clientY - r.top) / r.height - 0.5).toFixed(3));
    };
    if (hero && fine && !calm) hero.addEventListener("pointermove", onMove);

    return () => {
      clearInterval(id);
      removeEventListener("scroll", onScroll);
      io.disconnect();
      hero?.removeEventListener("pointermove", onMove);
    };
  }, []);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(OS[tab].run);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch {
      /* clipboard blocked: the command stays selectable */
    }
  };

  const Detected = os ? OS[os].icon : Monitor;
  const TabIcon = OS[tab].icon;

  return (
    <MotionConfig reducedMotion="user">
      <a className="skip" href="#main">
        Saltar al contenido
      </a>

      <div className="atmos" aria-hidden="true">
        <div className="atmos__scan" />
        <div className="atmos__noise" />
      </div>

      {/* ── Header ─────────────────────────────────────────────────── */}
      <header
        className={`hdr ${hidden ? "is-hidden" : ""} ${scrolled ? "is-condensed" : ""}`}
        onFocus={() => setHidden(false)}
      >
        <a href="#top" className="hdr__mark" aria-label="Odysseus, inicio">
          <span className="hdr__dot" aria-hidden="true" />
          ODYSSEUS
        </a>
        <nav aria-label="Principal" className="hdr__nav">
          <a href="#instalar">Instalar</a>
          <a href="#capacidades">Capacidades</a>
          <a href={REPO_URL} target="_blank" rel="noreferrer">
            GitHub
          </a>
        </nav>
        <span className="hdr__index" aria-hidden="true">
          {String(chapter).padStart(2, "0")} / 04
        </span>
        <a className="hdr__cta" href={os ? OS[os].file : "#instalar"} download={os ? true : undefined} data-cta="header">
          <Download size={15} strokeWidth={2.2} aria-hidden="true" />
          <span>Descargar</span>
        </a>
      </header>

      <main id="main">
        {/* ── 01 Hero ─────────────────────────────────────────────── */}
        <section className="hero" id="top" ref={heroRef} data-chapter="1" aria-labelledby="hero-title">
          <div className="grid-floor" aria-hidden="true">
            <div className="grid-floor__plane" />
            <div className="grid-floor__horizon" />
          </div>

          <div className="hero__reactor" aria-hidden="true">
            <Reactor />
          </div>

          <div className="hero__inner">
            <p className="label hero__eyebrow">
              <span className="pulse" aria-hidden="true" /> Sistema en línea<span className="hide-sm"> · v1.0 · local-first</span>
            </p>

            <h1 id="hero-title" className="hero__title">
              <span className="line"><span>ODYSSEUS</span></span>
            </h1>

            <p className="hero__value">
              Tu investigador autónomo, en tu propio hardware. Busca, recuerda y actúa — sin que tus datos
              salgan de tu máquina.
            </p>

            <div className="hero__actions">
              <Cta os={os} where="hero" />
              <a className="link-arrow" href="#instalar">
                Otros sistemas <ArrowDown size={15} aria-hidden="true" />
              </a>
            </div>

            <p className="hero__micro label">
              <span><Detected size={13} aria-hidden="true" />
              {os ? `Detectado: ${OS[os].label}` : "Elige tu sistema abajo"}</span>
              <span>Gratis · Código abierto MIT</span>
            </p>
          </div>

          {/* HUD readouts */}
          <div className="hud hud--left" aria-hidden="true">
            <p className="label hud__title">Secuencia de arranque</p>
            <ul className="boot">
              {BOOT.map(([k, v], i) => (
                <li key={k} style={{ "--i": i } as React.CSSProperties}>
                  <span>&gt; {k}</span>
                  <span className="boot__dots" />
                  <b>{v}</b>
                </li>
              ))}
            </ul>
          </div>
          <div className="hud hud--right" aria-hidden="true">
            <p className="label hud__title">Telemetría</p>
            <dl className="telemetry">
              <div><dt>Hora local</dt><dd>{clock || "--:--:--"}</dd></div>
              <div><dt>Plataforma</dt><dd>{os ? OS[os].label : "—"}</dd></div>
              <div><dt>Datos enviados</dt><dd>0 B</dd></div>
            </dl>
          </div>

          <a className="scroll-cue label" href="#instalar">
            <span className="scroll-cue__bar" aria-hidden="true" />
            Desplázate
          </a>
        </section>

        {/* ── 02 Install ──────────────────────────────────────────── */}
        <section className="chapter install" id="instalar" data-chapter="2" aria-labelledby="install-title">
          <motion.header className="chapter__head" {...reveal}>
            <p className="label">02 — Protocolo de instalación</p>
            <h2 id="install-title">Tres pasos. Un archivo.</h2>
            <p className="chapter__lede">
              El instalador hace el trabajo pesado y se puede ejecutar de nuevo cuando quieras.
            </p>
          </motion.header>

          <div className="install__grid">
            <motion.ol className="steps" {...reveal}>
              {STEPS.map((s) => (
                <li key={s.n} className="step">
                  <span className="step__n">{s.n}</span>
                  <div>
                    <h3>{s.title}</h3>
                    <p>{s.body}</p>
                  </div>
                </li>
              ))}
            </motion.ol>

            <motion.div {...reveal} transition={{ ...reveal.transition, delay: 0.1 }}>
              <Frame className="console">
                <div className="console__bar">
                  <div className="tabs" role="tablist" aria-label="Sistema operativo">
                    {OS_ORDER.map((k) => {
                      const I = OS[k].icon;
                      return (
                        <button
                          key={k}
                          role="tab"
                          id={`tab-${k}`}
                          aria-selected={tab === k}
                          aria-controls="console-panel"
                          tabIndex={tab === k ? 0 : -1}
                          className="tab"
                          onClick={() => setTab(k)}
                          onKeyDown={(e) => {
                            const i = OS_ORDER.indexOf(k);
                            if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
                              const n = OS_ORDER[(i + (e.key === "ArrowRight" ? 1 : 2)) % 3];
                              setTab(n);
                              document.getElementById(`tab-${n}`)?.focus();
                            }
                          }}
                        >
                          <I size={14} aria-hidden="true" />
                          {OS[k].label}
                          {os === k && <span className="tab__you">tú</span>}
                        </button>
                      );
                    })}
                  </div>
                  <span className="console__status label">
                    <span className="pulse" aria-hidden="true" /> listo
                  </span>
                </div>

                <div id="console-panel" role="tabpanel" aria-labelledby={`tab-${tab}`} className="console__body">
                  <p className="label console__k">01 · descarga</p>
                  <a className="cta cta--block" href={OS[tab].file} download data-cta="console">
                    <span className="cta__glow" aria-hidden="true" />
                    <TabIcon size={18} aria-hidden="true" />
                    <span className="cta__label">
                      <span data-text={`${CTA_LABEL} · ${OS[tab].label}`}>
                        {CTA_LABEL} · {OS[tab].label}
                      </span>
                    </span>
                  </a>

                  <p className="label console__k">02 · ejecuta en tu terminal</p>
                  <div className="cmd">
                    <code>
                      <span className="cmd__prompt" aria-hidden="true">{tab === "windows" ? "PS>" : "$"}</span>
                      {OS[tab].run}
                    </code>
                    <button className="cmd__copy" onClick={copy} aria-label="Copiar comando">
                      {copied ? <Check size={15} aria-hidden="true" /> : <Copy size={15} aria-hidden="true" />}
                      <span>{copied ? "Copiado" : "Copiar"}</span>
                    </button>
                  </div>
                  <p className="console__needs">{OS[tab].alt}</p>
                  <p className="console__needs">{OS[tab].needs}</p>
                  <p className="sr-only" aria-live="polite">{copied ? "Comando copiado" : ""}</p>
                </div>
              </Frame>
            </motion.div>
          </div>

          <motion.ul className="protocol" {...reveal}>
            {PROTOCOL.map((p) => {
              const I = p.icon;
              return (
                <li key={p.title}>
                  <I size={16} aria-hidden="true" />
                  <div>
                    <strong>{p.title}</strong>
                    <span>{p.body}</span>
                  </div>
                </li>
              );
            })}
          </motion.ul>
        </section>

        {/* ── 03 Capabilities ─────────────────────────────────────── */}
        <section className="chapter caps" id="capacidades" data-chapter="3" aria-labelledby="caps-title">
          <motion.header className="chapter__head" {...reveal}>
            <p className="label">03 — Módulos</p>
            <h2 id="caps-title">Un asistente, seis sistemas.</h2>
            <p className="chapter__lede">
              Todo corre en tu máquina. Tú decides qué modelos usar y qué sale a internet.
            </p>
          </motion.header>

          <ul className="modules">
            {MODULES.map((m, i) => {
              const I = m.icon;
              return (
                <motion.li
                  key={m.code}
                  {...reveal}
                  transition={{ ...reveal.transition, delay: (i % 3) * 0.07 }}
                >
                  <Frame className="module">
                    <div className="module__top">
                      <span className="module__icon"><I size={18} aria-hidden="true" /></span>
                      <span className="label">{String(i + 1).padStart(2, "0")} · {m.code}</span>
                    </div>
                    <h3>{m.title}</h3>
                    <p>{m.body}</p>
                    <p className="module__tags">{m.tags}</p>
                  </Frame>
                </motion.li>
              );
            })}
          </ul>
        </section>

        {/* ── 04 Finale ───────────────────────────────────────────── */}
        <section className="finale" data-chapter="4" aria-labelledby="finale-title">
          <div className="finale__rings" aria-hidden="true"><Reactor /></div>
          <motion.div className="finale__inner" {...reveal}>
            <p className="label">04 — Activación</p>
            <h2 id="finale-title">Enciende tu propio Jarvis.</h2>
            <Cta os={os} where="finale" />
            <p className="hero__micro label">Menos de 5 minutos · Sin cuenta · Se actualiza solo</p>
          </motion.div>
        </section>
      </main>

      <footer className="ftr">
        <div className="ftr__row">
          <nav aria-label="Enlaces del proyecto" className="ftr__links">
            <a href={REPO_URL} target="_blank" rel="noreferrer"><Github size={14} aria-hidden="true" /> Repositorio</a>
            <a href={`${REPO_URL}#readme`} target="_blank" rel="noreferrer">Documentación</a>
            <a href={`${REPO_URL}/blob/main/SECURITY.md`} target="_blank" rel="noreferrer">Seguridad</a>
            <a href={`${REPO_URL}/blob/main/THREAT_MODEL.md`} target="_blank" rel="noreferrer">Modelo de amenazas</a>
            <a href={`${REPO_URL}/blob/main/LICENSE`} target="_blank" rel="noreferrer">Licencia MIT</a>
          </nav>
          <a className="link-arrow" href="#top">Volver arriba <ArrowRight size={14} aria-hidden="true" /></a>
        </div>
        <p className="ftr__wordmark" aria-hidden="true">ODYSSEUS</p>
      </footer>
    </MotionConfig>
  );
}
