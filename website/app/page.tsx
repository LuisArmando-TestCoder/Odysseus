"use client";

import "./page.scss";

import { useState } from "react";
import { motion } from "framer-motion";
import {
  Monitor,
  Laptop,
  Apple,
  Download,
  Terminal,
  RefreshCw,
  Github,
  ShieldCheck,
  Sparkles,
  KeyRound,
  Link2,
  Copy,
  Check,
  FileText,
  type LucideIcon,
} from "lucide-react";

// Single source of truth for the installer. The executable lives at the root
// of the repo and bootstraps the whole app on every OS.
const INSTALLER_URL =
  "https://github.com/LuisArmando-TestCoder/Odysseus";

const REPO_RAW =
  "https://github.com/LuisArmando-TestCoder/Odysseus.git";

// One-liner commands the page offers to copy + run in a terminal.
const INSTALL_COMMANDS: Record<OsKey, string> = {
  linux: `git clone ${REPO_RAW} && cd Odysseus && python3 installer/install.py`,
  windows:
    "git clone https://github.com/LuisArmando-TestCoder/Odysseus.git\npowershell -ExecutionPolicy Bypass -File .\\installer\\self_update.ps1",
  macos: `git clone ${REPO_RAW} && cd Odysseus && python3 installer/install.py`,
};

type OsKey = "linux" | "windows" | "macos";

type DownloadOption = {
  key: OsKey;
  label: string;
  tagline: string;
  icon: LucideIcon;
};

const OPTIONS: DownloadOption[] = [
  {
    key: "linux",
    label: "Linux",
    tagline: ".deb · AppImage · tar.gz",
    icon: Monitor,
  },
  {
    key: "windows",
    label: "Windows",
    tagline: ".exe · instalador",
    icon: Laptop,
  },
  {
    key: "macos",
    label: "macOS",
    tagline: ".dmg · arm / intel",
    icon: Apple,
  },
];

function CopyButton({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
    } catch {
      // Fallback: legacy clipboard path (non-secure contexts).
      const ta = document.createElement("textarea");
      ta.value = value;
      document.body.appendChild(ta);
      ta.select();
      try {
        document.execCommand("copy");
      } catch {
        /* ignore */
      }
      document.body.removeChild(ta);
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 1600);
  }

  return (
    <button
      type="button"
      className="copy-btn"
      onClick={copy}
      aria-label="Copiar comando de instalación"
    >
      {copied ? <Check size={14} /> : <Copy size={14} />}
      <span>{copied ? "Copiado" : "Copiar"}</span>
    </button>
  );
}

function Feature({
  icon: Icon,
  title,
  children,
}: {
  icon: LucideIcon;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <motion.div
      className="feature"
      initial={{ opacity: 0, y: 16 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: "-60px" }}
      transition={{ duration: 0.45 }}
    >
      <div className="feature-icon">
        <Icon size={18} strokeWidth={2} />
      </div>
      <h3>{title}</h3>
      <p>{children}</p>
    </motion.div>
  );
}

export default function HomePage() {
  const [active, setActive] = useState<OsKey | null>(null);

  return (
    <main className="page">
      <div className="bg-glow" />

      {/* Hero */}
      <section className="hero">
        <motion.div
          className="hero-badge"
          initial={{ opacity: 0, y: -8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5 }}
        >
          <Sparkles size={13} /> Asistente autónomo
        </motion.div>

        <motion.h1
          initial={{ opacity: 0, y: 18 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, delay: 0.05 }}
        >
          Odysseus
        </motion.h1>

        <motion.p
          className="hero-sub"
          initial={{ opacity: 0, y: 18 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, delay: 0.12 }}
        >
          Un único ejecutable que instala, se actualiza solo cada día con{" "}
          <code>git pull</code>, y deja todo listo. Elige tu sistema y listo.
        </motion.p>

        <motion.div
          className="hero-actions"
          initial={{ opacity: 0, y: 18 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, delay: 0.18 }}
        >
          <a
            className="cta"
            href={INSTALLER_URL}
            target="_blank"
            rel="noopener noreferrer"
          >
            <Download size={18} strokeWidth={2.2} />
            Descargar instalador
          </a>
          <a
            className="cta ghost"
            href={INSTALLER_URL}
            target="_blank"
            rel="noopener noreferrer"
          >
            <Github size={18} strokeWidth={2.2} />
            Ver en GitHub
          </a>
        </motion.div>
      </section>

      {/* Download cards */}
      <section className="downloads">
        {OPTIONS.map((opt, i) => {
          const Icon = opt.icon;
          const isActive = active === opt.key;
          return (
            <motion.button
              key={opt.key}
              type="button"
              className={`download-card${isActive ? " active" : ""}`}
              onClick={() => setActive(opt.key)}
              initial={{ opacity: 0, y: 22 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, margin: "-40px" }}
              transition={{ duration: 0.45, delay: i * 0.08 }}
              whileHover={{ y: -4 }}
              whileTap={{ scale: 0.98 }}
            >
              <div className="download-card-icon">
                <Icon size={26} strokeWidth={1.8} />
              </div>
              <div className="download-card-title">{opt.label}</div>
              <div className="download-card-tagline">{opt.tagline}</div>
              <div className="download-card-cta">
                <Download size={15} strokeWidth={2.2} />
                Descargar
              </div>
            </motion.button>
          );
        })}
      </section>

      {/* Active OS — terminal command */}
      <AnimatePresenceWrapper value={active}>
        <div className="terminal-panel">
          <div className="terminal-head">
            <span className="dot" />
            <span className="dot" />
            <span className="dot" />
            <span className="terminal-title">
              {active ? "Terminal" : "Elige un sistema"}
            </span>
            {active && <CopyButton value={INSTALL_COMMANDS[active]} />}
          </div>
          {active ? (
            <pre className="terminal-body">{INSTALL_COMMANDS[active]}</pre>
          ) : (
            <div className="terminal-empty">
              <Terminal size={16} strokeWidth={2} />
              Selecciona una tarjeta de arriba para ver el comando de instalación.
            </div>
          )}
        </div>
      </AnimatePresenceWrapper>

      {/* Features */}
      <section className="features">
        <Feature icon={Terminal} title="Instalación en un paso">
          El ejecutable clona el repositorio, pide las credenciales que faltan y
          escribe tu <code>.env</code> automáticamente.
        </Feature>
        <Feature icon={KeyRound} title="Credenciales con tutoriales">
          Cada variable de entorno incluye un enlace directo a su documentación
          oficial para que consigas tu API key sin adivinar.
        </Feature>
        <Feature icon={RefreshCw} title="Autoactualización diaria">
          Un cron job revisa actualizaciones todos los días y ejecuta{" "}
          <code>git pull</code> para que siempre corras la última versión.
        </Feature>
        <Feature icon={Link2} title="Acceso directo en tu escritorio">
          Al instalar, se crea un acceso directo en Linux, Windows y macOS para
          abrir Odysseus de un clic.
        </Feature>
        <Feature icon={ShieldCheck} title="Seguro por diseño">
          Tus secretos se guardan en un <code>.env</code> con permisos{" "}
          <code>0600</code> y nunca se imprimen en pantalla.
        </Feature>
        <Feature icon={FileText} title="Documentación completa">
          El repositorio incluye guías de instalación, modelo de amenazas y hoja
          de ruta para que sepas exactamente qué corre.
        </Feature>
      </section>

      <footer className="footer">
        <span>
          Odysseus — <a href={INSTALLER_URL} target="_blank" rel="noopener noreferrer">github.com/LuisArmando-TestCoder/Odysseus</a>
        </span>
      </footer>
    </main>
  );
}

// Small helper to animate the terminal panel in/out when the OS selection
// changes, without pulling in a heavier animation pattern.
function AnimatePresenceWrapper({
  value,
  children,
}: {
  value: OsKey | null;
  children: React.ReactNode;
}) {
  return (
    <motion.div
      key={value ?? "none"}
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3 }}
    >
      {children}
    </motion.div>
  );
}