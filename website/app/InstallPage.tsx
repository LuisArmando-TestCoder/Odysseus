"use client";

import "./page.scss";

import { useCallback, useEffect, useId, useRef, useState } from "react";
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
  Send,
  Pause,
  Play,
  RotateCcw,
  Fingerprint,
  ExternalLink,
  Plus,
  type LucideIcon,
} from "lucide-react";

import {
  CHAPTERS,
  FAQ,
  LOCAL_URL,
  OS,
  OS_ORDER,
  REPO_URL,
  type Integrity,
  type OsKey,
} from "./content";

// ── Platform detection ──────────────────────────────────────────────────
const OS_ICON: Record<OsKey, LucideIcon> = { macos: Apple, windows: Laptop, linux: Monitor };
const CTA_LABEL = "Instalar Odysseus";

type Env = { os: OsKey | null; mobile: boolean };

function detectEnv(): Env {
  if (typeof navigator === "undefined") return { os: null, mobile: false };
  const ua = navigator.userAgent.toLowerCase();
  const p = (navigator.platform || "").toLowerCase();
  // iPadOS reports "MacIntel" but has touch: treat it as a phone/tablet.
  const ipad = p.includes("mac") && navigator.maxTouchPoints > 1;
  if (ipad || /iphone|ipad|ipod|android|mobile/.test(ua)) return { os: null, mobile: true };
  if (ua.includes("win") || p.includes("win")) return { os: "windows", mobile: false };
  if (ua.includes("mac") || p.includes("mac")) return { os: "macos", mobile: false };
  if (ua.includes("linux") || ua.includes("x11") || p.includes("linux")) return { os: "linux", mobile: false };
  return { os: null, mobile: false };
}

const fmtBytes = (n: number) => (n < 1024 ? `${n} B` : `${(n / 1024).toFixed(1)} KB`);

// ── Content ─────────────────────────────────────────────────────────────
const BOOT = [
  ["núcleo", "OK"],
  ["memoria vectorial", "OK"],
  ["agente · herramientas MCP", "OK"],
  ["investigación profunda", "OK"],
  ["enlace local", "SEGURO"],
] as const;

const PROTOCOL: { icon: LucideIcon; title: string; body: string }[] = [
  { icon: Terminal, title: "Un paso", body: "Clona, configura y arranca desde una sola línea o un solo archivo." },
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

// ── Hooks ───────────────────────────────────────────────────────────────
/** Copy text and remember which control did it, for ~2 s of success state. */
function useCopy() {
  const [copied, setCopied] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout>>();
  const copy = useCallback(async (text: string, key: string) => {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      return false; // clipboard blocked: the text stays visible and selectable
    }
    setCopied(key);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setCopied(null), 2400);
    return true;
  }, []);
  return { copied, copy };
}

// ── Small pieces ────────────────────────────────────────────────────────
function Reactor() {
  // Arc-reactor rings: pure SVG + CSS rotation, decorative only.
  const gid = useId().replace(/:/g, "");
  const ticks = Array.from({ length: 60 });
  return (
    <svg className="reactor" viewBox="0 0 600 600" aria-hidden="true">
      <defs>
        <radialGradient id={gid} cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor="#eaffff" stopOpacity="0.9" />
          <stop offset="18%" stopColor="#4fd8ff" stopOpacity="0.55" />
          <stop offset="60%" stopColor="#4fd8ff" stopOpacity="0.06" />
          <stop offset="100%" stopColor="#4fd8ff" stopOpacity="0" />
        </radialGradient>
      </defs>
      <circle cx="300" cy="300" r="300" fill={`url(#${gid})`} className="reactor__core" />
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

/** Text-roll label: the real text is the accessible name; ::after is visual. */
function RollLabel({ text }: { text: string }) {
  return (
    <span className="cta__label">
      <span data-text={text}>{text}</span>
    </span>
  );
}

/** Copyable code line with its own success state. */
function CodeLine({
  text,
  prompt,
  id,
  copied,
  onCopy,
  label = "Copiar",
}: {
  text: string;
  prompt?: string;
  id: string;
  copied: string | null;
  onCopy: (text: string, key: string) => void;
  label?: string;
}) {
  const done = copied === id;
  return (
    <div className={`cmd ${done ? "is-done" : ""}`}>
      <code tabIndex={0} aria-label={`Comando: ${text}`}>
        {prompt && <span className="cmd__prompt" aria-hidden="true">{prompt}</span>}
        {text}
      </code>
      <button type="button" className="cmd__copy" onClick={() => onCopy(text, id)} data-cta={`copy-${id}`}>
        {done ? <Check size={15} aria-hidden="true" /> : <Copy size={15} aria-hidden="true" />}
        <span>{done ? "Copiado" : label}</span>
      </button>
    </div>
  );
}

/**
 * The one primary action, adapted to the visitor's machine so the label never
 * lies about what happens:
 *  · macOS/Linux → copies the one-line installer (the reliable path there)
 *  · Windows     → downloads install-windows.cmd (double-click)
 *  · phone       → sends this page to a computer (share sheet or copy link)
 *  · unknown     → jumps to the per-OS console
 */
function PrimaryCta({
  env,
  where,
  block = false,
  copied,
  onCopy,
  onDownload,
}: {
  env: Env;
  where: string;
  block?: boolean;
  copied: string | null;
  onCopy: (text: string, key: string) => Promise<boolean>;
  onDownload: () => void;
}) {
  const cls = `cta ${block ? "cta--block" : ""}`;
  const key = `cta-${where}`;
  const done = copied === key;
  const Glow = <span className="cta__glow" aria-hidden="true" />;

  if (env.mobile) {
    const send = async () => {
      const url = location.href.split("#")[0];
      if (navigator.share) {
        try {
          await navigator.share({ title: "Odysseus", text: "Instalar Odysseus en mi computadora", url });
          return;
        } catch {
          /* dismissed or unsupported: fall back to copying the link */
        }
      }
      await onCopy(url, key);
    };
    return (
      <button type="button" className={`${cls} ${done ? "is-done" : ""}`} onClick={send} data-cta={where} data-magnetic>
        {Glow}
        {done ? <Check size={18} aria-hidden="true" /> : <Send size={18} aria-hidden="true" />}
        <RollLabel text={done ? "Enlace copiado" : "Enviar a mi computadora"} />
      </button>
    );
  }

  if (env.os && OS[env.os].method === "paste") {
    const spec = OS[env.os];
    return (
      <button
        type="button"
        className={`${cls} ${done ? "is-done" : ""}`}
        onClick={() => onCopy(spec.oneLiner, key)}
        data-cta={where}
        data-magnetic
        aria-describedby="cta-hint"
      >
        {Glow}
        {done ? <Check size={18} aria-hidden="true" /> : <Terminal size={18} aria-hidden="true" />}
        <RollLabel text={done ? "Copiado · pégalo en Terminal" : CTA_LABEL} />
      </button>
    );
  }

  if (env.os) {
    const spec = OS[env.os];
    return (
      <a
        className={`${cls} ${done ? "is-done" : ""}`}
        href={`/${spec.file}`}
        download
        onClick={onDownload}
        data-cta={where}
        data-magnetic
        aria-describedby="cta-hint"
      >
        {Glow}
        <Download size={18} strokeWidth={2.2} aria-hidden="true" />
        <RollLabel text={CTA_LABEL} />
      </a>
    );
  }

  return (
    <a className={cls} href="#instalar" data-cta={where} data-magnetic>
      {Glow}
      <ArrowDown size={18} aria-hidden="true" />
      <RollLabel text={CTA_LABEL} />
    </a>
  );
}

/** "What you'll see": the installer's own output, printed line by line. */
function Transcript({ os }: { os: OsKey }) {
  const lines = OS[os].transcript;
  const [shown, setShown] = useState(lines.length);
  const [armed, setArmed] = useState(false); // JS on + motion allowed
  const [playing, setPlaying] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const seen = useRef(false);

  // Restart whenever the OS changes; first play waits until it's on screen.
  useEffect(() => {
    if (matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    setArmed(true);
    setShown(0);
    if (seen.current) {
      setPlaying(true);
      return;
    }
    const el = ref.current;
    if (!el) return;
    const io = new IntersectionObserver(([e]) => {
      if (e.isIntersecting) {
        seen.current = true;
        setPlaying(true);
        io.disconnect();
      }
    }, { threshold: 0.4 });
    io.observe(el);
    return () => io.disconnect();
  }, [os]);

  useEffect(() => {
    if (!playing) return;
    if (shown >= lines.length) {
      setPlaying(false);
      return;
    }
    const slow = /Esperando|Cloning|Clonando/.test(lines[shown] ?? "");
    const t = setTimeout(() => setShown((n) => n + 1), slow ? 900 : 360);
    return () => clearTimeout(t);
  }, [playing, shown, lines]);

  const finished = shown >= lines.length;

  return (
    <div ref={ref} className={`tx ${armed ? "is-armed" : ""}`}>
      <div className="tx__bar">
        <span className="tx__dots" aria-hidden="true"><i /><i /><i /></span>
        <p className="label">Lo que verás · {OS[os].label} · abreviado</p>
        {armed && (
          <button
            type="button"
            className="tx__ctl"
            onClick={() => {
              if (finished) {
                setShown(0);
                setPlaying(true);
              } else setPlaying((p) => !p);
            }}
          >
            {finished ? <RotateCcw size={13} aria-hidden="true" /> : playing ? <Pause size={13} aria-hidden="true" /> : <Play size={13} aria-hidden="true" />}
            <span>{finished ? "Repetir" : playing ? "Pausar" : "Reanudar"}</span>
          </button>
        )}
      </div>
      <ol className="tx__body">
        {lines.map((l, i) => (
          <li
            key={`${os}-${i}`}
            className={`${i < shown ? "on" : ""} ${/✓|OK /.test(l) ? "ok" : ""} ${/^(▶|==>)/.test(l) ? "hd" : ""}`}
          >
            {l}
          </li>
        ))}
        {armed && !finished && <li className="tx__caret" aria-hidden="true" />}
      </ol>
    </div>
  );
}

// ── Page ────────────────────────────────────────────────────────────────
export default function InstallPage({ integrity }: { integrity: Integrity }) {
  const [env, setEnv] = useState<Env>({ os: null, mobile: false });
  const [tab, setTab] = useState<OsKey>("macos");
  const [downloaded, setDownloaded] = useState<OsKey | null>(null);
  const [clock, setClock] = useState("");
  const [hidden, setHidden] = useState(false);
  const [scrolled, setScrolled] = useState(false);
  const [chapter, setChapter] = useState(1);
  const [announce, setAnnounce] = useState("");
  const { copied, copy } = useCopy();
  const heroRef = useRef<HTMLElement>(null);

  const copyAndAnnounce = useCallback(
    async (text: string, key: string) => {
      const ok = await copy(text, key);
      setAnnounce(ok ? "Copiado al portapapeles" : "No se pudo copiar: selecciona el texto y cópialo a mano");
      return ok;
    },
    [copy],
  );

  useEffect(() => {
    const d = detectEnv();
    setEnv(d);
    if (d.os) setTab(d.os);

    const tick = () =>
      setClock(new Date().toLocaleTimeString("es", { hour: "2-digit", minute: "2-digit", second: "2-digit" }));
    tick();
    const id = setInterval(tick, 1000);

    // Hide header on scroll down, show on scroll up.
    let last = 0;
    const onScroll = () => {
      const y = window.scrollY;
      setHidden(y > 160 && y > last + 2);
      if (y < last - 2) setHidden(false);
      setScrolled(y > 40);
      last = y;
    };
    addEventListener("scroll", onScroll, { passive: true });

    // Chapter index follows the section crossing the viewport centre.
    const io = new IntersectionObserver(
      (entries) =>
        entries.forEach((e) => {
          if (e.isIntersecting) setChapter(Number((e.target as HTMLElement).dataset.chapter));
        }),
      { rootMargin: "-50% 0px -50% 0px" },
    );
    document.querySelectorAll<HTMLElement>("[data-chapter]").forEach((s) => io.observe(s));

    // Pause the reactor/grid loops while their section is off screen.
    const idle = new IntersectionObserver((entries) =>
      entries.forEach((e) => e.target.classList.toggle("is-off", !e.isIntersecting)),
    );
    document.querySelectorAll(".hero, .finale").forEach((s) => idle.observe(s));

    const fine = matchMedia("(pointer: fine)").matches;
    const calm = matchMedia("(prefers-reduced-motion: reduce)").matches;

    // Subtle pointer parallax on the hero (desktop pointers only).
    const hero = heroRef.current;
    const onMove = (e: PointerEvent) => {
      if (!hero) return;
      const r = hero.getBoundingClientRect();
      hero.style.setProperty("--px", ((e.clientX - r.left) / r.width - 0.5).toFixed(3));
      hero.style.setProperty("--py", ((e.clientY - r.top) / r.height - 0.5).toFixed(3));
    };
    if (hero && fine && !calm) hero.addEventListener("pointermove", onMove);

    // Magnetic primary CTA: leans ≤ 10 px toward the pointer, springs back.
    const onMag = (e: PointerEvent) => {
      const el = (e.target as HTMLElement).closest<HTMLElement>("[data-magnetic]");
      document.querySelectorAll<HTMLElement>("[data-magnetic].is-pulled").forEach((m) => {
        if (m !== el) {
          m.classList.remove("is-pulled");
          m.style.setProperty("--mx", "0px");
          m.style.setProperty("--my", "0px");
        }
      });
      if (!el) return;
      const r = el.getBoundingClientRect();
      const clamp = (v: number) => Math.max(-10, Math.min(10, v));
      el.classList.add("is-pulled");
      el.style.setProperty("--mx", `${clamp((e.clientX - (r.left + r.width / 2)) * 0.18)}px`);
      el.style.setProperty("--my", `${clamp((e.clientY - (r.top + r.height / 2)) * 0.3)}px`);
    };
    if (fine && !calm) addEventListener("pointermove", onMag, { passive: true });

    return () => {
      clearInterval(id);
      removeEventListener("scroll", onScroll);
      removeEventListener("pointermove", onMag);
      io.disconnect();
      idle.disconnect();
      hero?.removeEventListener("pointermove", onMove);
    };
  }, []);

  const spec = OS[tab];
  const TabIcon = OS_ICON[tab];
  const file = integrity[spec.file];
  const Detected = env.os ? OS_ICON[env.os] : env.mobile ? Send : Monitor;
  const onDownload = (k: OsKey) => () => {
    setDownloaded(k);
    setAnnounce(`Descarga de ${OS[k].file} iniciada. Ábrelo desde tu carpeta de Descargas.`);
  };
  const ctaProps = { env, copied, onCopy: copyAndAnnounce, onDownload: onDownload(env.os ?? tab) };
  const heroFile = integrity[OS[env.os ?? tab].file];

  const heroHint = env.mobile
    ? "Los instaladores corren en macOS, Windows y Linux"
    : env.os
      ? OS[env.os].heroHint
      : "Elige tu sistema abajo";

  return (
    <>
      <a className="skip" href="#main">
        Saltar al contenido
      </a>

      <div className="atmos" aria-hidden="true">
        <div className="atmos__scan" />
        <div className="atmos__noise" />
      </div>

      <p className="sr-only" aria-live="polite">{announce}</p>

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
          <a href="#instalar" aria-current={chapter === 2 ? "location" : undefined}>Instalar</a>
          <a href="#capacidades" aria-current={chapter === 3 ? "location" : undefined}>Módulos</a>
          <a href="#ayuda" aria-current={chapter === 4 ? "location" : undefined}>Ayuda</a>
          <a href={REPO_URL} target="_blank" rel="noreferrer">
            GitHub
          </a>
        </nav>
        <span className="hdr__index" aria-hidden="true">
          <b>{String(chapter).padStart(2, "0")}</b> / {String(CHAPTERS.length).padStart(2, "0")}
          <span className="hdr__chapter"> · {CHAPTERS[chapter - 1]}</span>
        </span>
        <a className="hdr__cta" href="#instalar" data-cta="header">
          <Download size={15} strokeWidth={2.2} aria-hidden="true" />
          <span>Instalar</span>
        </a>
        <span className="hdr__progress" aria-hidden="true" />
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
              <PrimaryCta {...ctaProps} where="hero" />
              <a className="link-arrow" href="#instalar">
                {env.os ? "Otros sistemas" : "Ver los pasos"} <ArrowDown size={15} aria-hidden="true" />
              </a>
            </div>

            <p className="hero__micro label" id="cta-hint">
              <span>
                <Detected size={13} aria-hidden="true" />
                {env.os ? `${OS[env.os].label} · ${heroHint}` : heroHint}
              </span>
              <span>Gratis · MIT · sin cuenta</span>
            </p>

            <ul className="proof" aria-label="Datos del instalador">
              <li><b>3</b><span>sistemas</span></li>
              {heroFile && (
                <li><b>{heroFile.lines}</b><span>líneas de instalador, léelas</span></li>
              )}
              <li><b>127.0.0.1</b><span>corre en tu máquina</span></li>
            </ul>
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
              <div><dt>Plataforma</dt><dd>{env.os ? OS[env.os].label : env.mobile ? "móvil" : "—"}</dd></div>
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
          <header className="chapter__head reveal">
            <p className="label">02 — Protocolo de instalación</p>
            <h2 id="install-title">Tres pasos. Una línea.</h2>
            <p className="chapter__lede">
              Pega una línea en tu terminal — en Windows, haz doble clic en un archivo. El instalador prepara todo
              y puedes repetirlo cuando quieras para reparar o actualizar.
            </p>
          </header>

          <div className="install__grid">
            <ol className="steps reveal" aria-label={`Pasos para ${spec.label}`} key={tab}>
              {spec.steps.map((s, i) => (
                <li
                  key={s.title}
                  className={`step ${downloaded === tab && i === 1 ? "is-next" : ""}`}
                  style={{ "--i": i } as React.CSSProperties}
                >
                  <span className="step__n" aria-hidden="true">{String(i + 1).padStart(2, "0")}</span>
                  <div>
                    <h3>{s.title}</h3>
                    <p>{s.body}</p>
                  </div>
                </li>
              ))}
            </ol>

            <div className="reveal">
              <Frame className="console">
                <div className="console__bar">
                  <div className="tabs" role="tablist" aria-label="Sistema operativo">
                    {OS_ORDER.map((k) => {
                      const I = OS_ICON[k];
                      return (
                        <button
                          key={k}
                          type="button"
                          role="tab"
                          id={`tab-${k}`}
                          aria-selected={tab === k}
                          aria-controls="console-panel"
                          tabIndex={tab === k ? 0 : -1}
                          className="tab"
                          onClick={() => setTab(k)}
                          onKeyDown={(e) => {
                            const i = OS_ORDER.indexOf(k);
                            const map: Record<string, number> = {
                              ArrowRight: (i + 1) % 3,
                              ArrowLeft: (i + 2) % 3,
                              Home: 0,
                              End: 2,
                            };
                            if (e.key in map) {
                              e.preventDefault();
                              const n = OS_ORDER[map[e.key]];
                              setTab(n);
                              document.getElementById(`tab-${n}`)?.focus();
                            }
                          }}
                        >
                          <I size={14} aria-hidden="true" />
                          {OS[k].label}
                          {env.os === k && <span className="tab__you">tú</span>}
                        </button>
                      );
                    })}
                  </div>
                  <span className="console__status label">
                    <span className="pulse" aria-hidden="true" /> listo
                  </span>
                </div>

                <div id="console-panel" role="tabpanel" aria-labelledby={`tab-${tab}`} className="console__body" key={tab}>
                  {spec.method === "paste" ? (
                    <>
                      <p className="label console__k">
                        <span className="badge">Recomendado</span> pega esto en {tab === "macos" ? "Terminal" : "tu terminal"}
                      </p>
                      <CodeLine text={spec.oneLiner} prompt={spec.prompt} id={`one-${tab}`} copied={copied} onCopy={copyAndAnnounce} label="Copiar línea" />
                      <p className="console__note">
                        Descarga el instalador de la rama main por HTTPS y lo ejecuta. Te pedirá tu contraseña solo si
                        hace falta instalar herramientas del sistema.
                      </p>

                      <p className="label console__k">O descarga el archivo</p>
                      <div className="alt">
                        <a className="alt__file" href={`/${spec.file}`} download onClick={onDownload(tab)} data-cta="console-file">
                          <Download size={15} aria-hidden="true" />
                          <span>{spec.file}</span>
                          {file && <em>{fmtBytes(file.bytes)}</em>}
                        </a>
                        <CodeLine text={spec.runFile} prompt={spec.prompt} id={`run-${tab}`} copied={copied} onCopy={copyAndAnnounce} />
                      </div>
                    </>
                  ) : (
                    <>
                      <p className="label console__k"><span className="badge">Recomendado</span> descarga y haz doble clic</p>
                      <a
                        className={`cta cta--block ${downloaded === tab ? "is-done" : ""}`}
                        href={`/${spec.file}`}
                        download
                        onClick={onDownload(tab)}
                        data-cta="console"
                      >
                        <span className="cta__glow" aria-hidden="true" />
                        {downloaded === tab ? <Check size={18} aria-hidden="true" /> : <TabIcon size={18} aria-hidden="true" />}
                        <RollLabel text={downloaded === tab ? "Descargado · ábrelo con doble clic" : `Descargar ${spec.file}`} />
                      </a>
                      <p className="console__note">
                        ¿SmartScreen? Más información → Ejecutar de todas formas. El archivo usa una excepción de política
                        solo para esta ejecución; no cambia nada en tu sistema.
                      </p>

                      <p className="label console__k">O pega esto en PowerShell</p>
                      <CodeLine text={spec.oneLiner} prompt={spec.prompt} id={`one-${tab}`} copied={copied} onCopy={copyAndAnnounce} label="Copiar línea" />
                    </>
                  )}

                  {file && (
                    <details className="verify">
                      <summary>
                        <Fingerprint size={15} aria-hidden="true" />
                        <span>Verifica antes de ejecutar</span>
                        <em>{file.lines} líneas · {fmtBytes(file.bytes)}</em>
                        <Plus size={14} aria-hidden="true" className="verify__plus" />
                      </summary>
                      <div className="verify__body">
                        <p className="label">SHA-256 de {file.name}</p>
                        <CodeLine text={file.sha256} id={`sha-${tab}`} copied={copied} onCopy={copyAndAnnounce} />
                        <p className="label">Compáralo con</p>
                        <CodeLine text={spec.verify} prompt={spec.prompt} id={`ver-${tab}`} copied={copied} onCopy={copyAndAnnounce} />
                        <a
                          className="link-arrow"
                          href={`${REPO_URL}/blob/main/installer/bootstrap/${spec.file}`}
                          target="_blank"
                          rel="noreferrer"
                        >
                          Leer el código en GitHub <ExternalLink size={13} aria-hidden="true" />
                        </a>
                      </div>
                    </details>
                  )}

                  <p className="console__needs">{spec.needs}</p>
                </div>
              </Frame>
            </div>
          </div>

          <div className="install__after reveal">
            <Transcript os={tab} />
            <div className="arrive">
              <p className="label">Al terminar</p>
              <p className="arrive__url">
                <span className="pulse" aria-hidden="true" /> {LOCAL_URL}
              </p>
              <p className="arrive__body">
                Odysseus se abre en tu navegador y queda un acceso directo para abrirlo y detenerlo. Nada sale de tu
                máquina salvo las llamadas a los modelos o servicios que tú configures.
              </p>
              <a className="link-arrow" href="#ayuda">
                ¿Algo no salió bien? <ArrowRight size={14} aria-hidden="true" />
              </a>
            </div>
          </div>

          <ul className="protocol reveal">
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
          </ul>
        </section>

        {/* ── 03 Capabilities ─────────────────────────────────────── */}
        <section className="chapter caps" id="capacidades" data-chapter="3" aria-labelledby="caps-title">
          <header className="chapter__head reveal">
            <p className="label">03 — Módulos</p>
            <h2 id="caps-title">Un asistente, seis sistemas.</h2>
            <p className="chapter__lede">
              Todo corre en tu máquina. Tú decides qué modelos usar y qué sale a internet.
            </p>
          </header>

          <ul className="modules">
            {MODULES.map((m, i) => {
              const I = m.icon;
              return (
                <li key={m.code} className="reveal" style={{ "--i": i % 3 } as React.CSSProperties}>
                  <Frame className="module">
                    <div className="module__top">
                      <span className="module__icon"><I size={18} aria-hidden="true" /></span>
                      <span className="label">{String(i + 1).padStart(2, "0")} · {m.code}</span>
                    </div>
                    <h3>{m.title}</h3>
                    <p>{m.body}</p>
                    <p className="module__tags">{m.tags}</p>
                  </Frame>
                </li>
              );
            })}
          </ul>
        </section>

        {/* ── 04 Help ─────────────────────────────────────────────── */}
        <section className="chapter help" id="ayuda" data-chapter="4" aria-labelledby="help-title">
          <div className="help__grid">
            <header className="chapter__head reveal">
              <p className="label">04 — Ayuda</p>
              <h2 id="help-title">Antes de que lo preguntes.</h2>
              <p className="chapter__lede">
                Lo que suele pasar la primera vez, y cómo actualizar, mover o quitar Odysseus.
              </p>
              <a className="link-arrow" href={`${REPO_URL}/blob/main/docs/auto-update.md`} target="_blank" rel="noreferrer">
                Cómo funcionan las actualizaciones <ExternalLink size={13} aria-hidden="true" />
              </a>
            </header>

            <div className="faq reveal">
              {FAQ.map((f, i) => (
                <details key={f.q} className="faq__item" open={i === 0}>
                  <summary>
                    <span className="faq__n" aria-hidden="true">{String(i + 1).padStart(2, "0")}</span>
                    <span className="faq__q">{f.q}</span>
                    <Plus size={16} aria-hidden="true" className="faq__plus" />
                  </summary>
                  <div className="faq__a">
                    <p>{f.a}</p>
                    {f.code && (
                      <CodeLine text={f.code} prompt="$" id={`faq-${i}`} copied={copied} onCopy={copyAndAnnounce} />
                    )}
                  </div>
                </details>
              ))}
            </div>
          </div>
        </section>

        {/* ── 05 Finale ───────────────────────────────────────────── */}
        <section className="finale" data-chapter="5" aria-labelledby="finale-title">
          <div className="finale__rings" aria-hidden="true"><Reactor /></div>
          <div className="finale__inner reveal">
            <p className="label">05 — Activación</p>
            <h2 id="finale-title">Enciende tu propio Jarvis.</h2>
            <PrimaryCta {...ctaProps} where="finale" />
            <p className="hero__micro label">
              {env.os ? OS[env.os].heroHint : heroHint} · Sin cuenta · Se actualiza solo
            </p>
          </div>
        </section>
      </main>

      <footer className="ftr">
        <div className="ftr__row">
          <nav aria-label="Enlaces del proyecto" className="ftr__links">
            <a href={REPO_URL} target="_blank" rel="noreferrer"><Github size={14} aria-hidden="true" /> Repositorio</a>
            <a href={`${REPO_URL}#readme`} target="_blank" rel="noreferrer">Documentación</a>
            <a href={`${REPO_URL}/tree/main/installer/bootstrap`} target="_blank" rel="noreferrer">Código del instalador</a>
            <a href={`${REPO_URL}/blob/main/SECURITY.md`} target="_blank" rel="noreferrer">Seguridad</a>
            <a href={`${REPO_URL}/blob/main/THREAT_MODEL.md`} target="_blank" rel="noreferrer">Modelo de amenazas</a>
            <a href={`${REPO_URL}/blob/main/LICENSE`} target="_blank" rel="noreferrer">Licencia MIT</a>
          </nav>
          <a className="link-arrow" href="#top">Volver arriba <ArrowRight size={14} aria-hidden="true" /></a>
        </div>
        <p className="ftr__wordmark" aria-hidden="true">ODYSSEUS</p>
      </footer>
    </>
  );
}
