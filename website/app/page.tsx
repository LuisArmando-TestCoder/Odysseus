"use client";

import "./page.scss";

import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import {
  Monitor,
  Laptop,
  Apple,
  Download,
  HelpCircle,
  Terminal,
  RefreshCw,
  ShieldCheck,
  KeyRound,
  Link2,
  FileText,
  ArrowDown,
  Sparkles,
  type LucideIcon,
} from "lucide-react";

// Single source of truth for the installer. Each OS resolves to its own
// bootstrap executable (a self-contained script that installs git + Python,
// clones the repo, and runs the credential wizard). The scripts are served
// same-origin from /public so the `download` attribute forces a real file
// download instead of the browser rendering the script inline.
const INSTALLER_URL = "https://github.com/LuisArmando-TestCoder/Odysseus";

type Feature = {
  icon: LucideIcon;
  title: string;
  body: string;
};

const FEATURES: Feature[] = [
  {
    icon: Terminal,
    title: "Instalación en un paso",
    body: "El ejecutable clona el repositorio, pide las credenciales que faltan y escribe tu .env automáticamente.",
  },
  {
    icon: KeyRound,
    title: "Credenciales con tutoriales",
    body: "Cada variable de entorno incluye un enlace directo a su documentación oficial para que consigas tu API key sin adivinar.",
  },
  {
    icon: RefreshCw,
    title: "Autoactualización diaria",
    body: "Un cron job revisa actualizaciones todos los días y ejecuta git pull para que siempre corras la última versión.",
  },
  {
    icon: Link2,
    title: "Acceso directo en tu escritorio",
    body: "Al instalar, se crea un acceso directo en Linux, Windows y macOS para abrir Odysseus de un clic.",
  },
  {
    icon: ShieldCheck,
    title: "Seguro por diseño",
    body: "Tus secretos se guardan en un .env con permisos 0600 y nunca se imprimen en pantalla.",
  },
  {
    icon: FileText,
    title: "Documentación completa",
    body: "El repositorio incluye guías de instalación, modelo de amenazas y hoja de ruta para que sepas exactamente qué corre.",
  },
];

type OsKey = "linux" | "windows" | "macos" | "unknown";

type Os = {
  key: OsKey;
  label: string;
  icon: LucideIcon;
};

const OS_BY_KEY: Record<OsKey, { label: string; icon: LucideIcon }> = {
  linux: { label: "Linux", icon: Monitor },
  windows: { label: "Windows", icon: Laptop },
  macos: { label: "macOS", icon: Apple },
  unknown: { label: "Cualquier sistema", icon: HelpCircle },
};

const OS_LIST: Os[] = [
  { key: "linux", label: "Linux", icon: Monitor },
  { key: "windows", label: "Windows", icon: Laptop },
  { key: "macos", label: "macOS", icon: Apple },
];

// Per-OS download targets. Each points at that OS's bootstrap executable,
// served same-origin so the `download` attribute triggers a real download.
const DOWNLOAD_URLS: Record<OsKey, string> = {
  linux: "/install-linux.sh",
  windows: "/install-windows.ps1",
  macos: "/install-macos.sh",
  unknown: INSTALLER_URL,
};

function detectOs(): OsKey {
  if (typeof navigator === "undefined") return "unknown";
  const ua = navigator.userAgent.toLowerCase();
  const platform = (navigator.platform || "").toLowerCase();

  if (ua.includes("win") || platform.includes("win")) return "windows";
  if (ua.includes("mac") || platform.includes("mac")) return "macos";
  if (ua.includes("linux") || ua.includes("x11") || platform.includes("linux"))
    return "linux";
  return "unknown";
}

export default function HomePage() {
  const [os, setOs] = useState<OsKey>("unknown");

  useEffect(() => {
    setOs(detectOs());
  }, []);

  const detected = OS_BY_KEY[os];
  const DetectedIcon = detected.icon;

  return (
    <main className="slides">
      <div className="bg-glow" />

      {/* ── Slide 1 — Hero, ultra focused, one CTA ───────────────────────── */}
      <section className="slide slide-hero">
        <motion.div
          className="hero-inner"
          initial={{ opacity: 0, y: 24 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.7, ease: "easeOut" }}
        >
          <motion.div
            className="hero-badge"
            initial={{ opacity: 0, y: -8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.5 }}
          >
            <Sparkles size={13} /> Asistente autónomo
          </motion.div>

          <h1 className="hero-title">Odysseus</h1>

          <p className="hero-tagline">
            Un único ejecutable que se instala y se actualiza solo.
          </p>

          <a
            className="hero-cta"
            href={DOWNLOAD_URLS[os]}
            download
          >
            <Download size={18} strokeWidth={2.2} />
            Descargar Odysseus
          </a>
          <span className="detected-os">
            <DetectedIcon size={15} strokeWidth={2} />
            Detectado: {detected.label}
          </span>
        </motion.div>

        <motion.a
          className="scroll-hint"
          href="#features"
          aria-label="Ver características"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.9, duration: 0.6 }}
        >
          <ArrowDown size={20} strokeWidth={2} />
        </motion.a>
      </section>

      {/* ── Slide 2 — Features, full screen explanation ──────────────────── */}
      <section className="slide slide-features" id="features">
        <motion.div
          className="features-inner"
          initial={{ opacity: 0, y: 24 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: "-80px" }}
          transition={{ duration: 0.6 }}
        >
          <h2 className="features-heading">Todo lo que hace Odysseus</h2>

          <div className="features-grid">
            {FEATURES.map((f) => {
              const Icon = f.icon;
              return (
                <div className="feature-card" key={f.title}>
                  <div className="feature-icon">
                    <Icon size={18} strokeWidth={2} />
                  </div>
                  <h3>{f.title}</h3>
                  <p>{f.body}</p>
                </div>
              );
            })}
          </div>

          <div className="os-row">
            {OS_LIST.map((os) => {
              const Icon = os.icon;
              return (
                <a
                  key={os.key}
                  className="os-pill"
                  href={DOWNLOAD_URLS[os.key]}
                  download
                >
                  <Icon size={16} strokeWidth={2} />
                  {os.label}
                </a>
              );
            })}
          </div>
        </motion.div>
      </section>
    </main>
  );
}