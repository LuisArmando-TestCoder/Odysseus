// ── Shared content for the install page ────────────────────────────────
// Plain data (no "use client") so the server page can reuse the FAQ for
// structured data and the client page can render it. Every claim here is
// taken from installer/bootstrap/* and installer/*.py — keep them in sync.

export const REPO_URL = "https://github.com/LuisArmando-TestCoder/Odysseus";
export const RAW = "https://raw.githubusercontent.com/LuisArmando-TestCoder/Odysseus/main/installer/bootstrap";
export const LOCAL_URL = "http://127.0.0.1:7860";

export type OsKey = "macos" | "windows" | "linux";
export const OS_ORDER: OsKey[] = ["macos", "windows", "linux"];

/** Facts about each installer file, computed at build time (app/page.tsx). */
export type FileFacts = { name: string; bytes: number; lines: number; sha256: string };
export type Integrity = Partial<Record<string, FileFacts>>;

export type OsSpec = {
  label: string;
  /** How the recommended path works: paste a line, or double-click a file. */
  method: "paste" | "file";
  /** File served from /public (the downloadable "executable"). */
  file: string;
  /** One-liner that fetches the installer from main. */
  oneLiner: string;
  /** Prompt glyph shown before the one-liner. */
  prompt: string;
  /** Command to run the downloaded file (alternative path for paste OSes). */
  runFile: string;
  /** Verify the downloaded file's SHA-256. */
  verify: string;
  /** Where the code lives after installing. */
  home: string;
  needs: string;
  heroHint: string;
  steps: { title: string; body: string }[];
  transcript: string[];
};

const ASSISTANT_STEP = (envPath: string) => ({
  title: "Responde al asistente",
  body: `Pide solo las llaves que faltan, con un enlace a la guía de cada una, guarda ${envPath} con permisos 600 y abre Odysseus en tu navegador.`,
});

// Tail every installer shares (installer/install.py). Abridged on the page.
const WIZARD_TAIL = (envPath: string) => [
  "▶ Running the Odysseus installer…",
  "  ✓ venv creado",
  "  ✓ Dependencias instaladas",
  "  Cada variable muestra enlaces de tutorial…",
  `  ✓ .env escrito en ${envPath} (permisos 600)`,
  "  Esperando a que el servidor responda (la primera vez puede tardar ~1-2 min)…",
  `  ✓ Odysseus está corriendo en ${LOCAL_URL}`,
  "  Listo. Puedes cerrar esta terminal.",
];

export const OS: Record<OsKey, OsSpec> = {
  macos: {
    label: "macOS",
    method: "paste",
    file: "install-macos.sh",
    oneLiner: `/bin/bash -c "$(curl -fsSL ${RAW}/install-macos.sh)"`,
    prompt: "$",
    runFile: "bash ~/Downloads/install-macos.sh",
    verify: "shasum -a 256 ~/Downloads/install-macos.sh",
    home: "~/odysseus",
    needs: "macOS 12+ · instala las herramientas de Xcode y Python 3.12 si faltan",
    heroHint: "Copia una línea · pégala en Terminal",
    steps: [
      { title: "Abre Terminal", body: "Pulsa ⌘ Espacio, escribe «Terminal» y pulsa Enter." },
      {
        title: "Pega la línea y pulsa Enter",
        body: "Instala las herramientas de Xcode y Python 3.12 si faltan y clona Odysseus en ~/odysseus.",
      },
      ASSISTANT_STEP("~/odysseus/.env"),
    ],
    transcript: [
      "▶ Odysseus — macOS installer",
      "  install folder: ~/odysseus",
      "  ✓ git: git version …",
      "  ✓ Python: Python 3.12 …",
      "  Cloning https://github.com/LuisArmando-TestCoder/Odysseus.git …",
      ...WIZARD_TAIL("~/odysseus/.env"),
    ],
  },
  windows: {
    label: "Windows",
    method: "file",
    file: "install-windows.cmd",
    oneLiner: `irm ${RAW}/install-windows.ps1 | iex`,
    prompt: "PS>",
    runFile: "",
    verify: "Get-FileHash $HOME\\Downloads\\install-windows.cmd",
    home: "%USERPROFILE%\\odysseus",
    needs: "Windows 10/11 · instala Git y Python 3.12 con winget",
    heroHint: "Descarga un archivo · haz doble clic",
    steps: [
      {
        title: "Descarga install-windows.cmd",
        body: "Un archivo pequeño que lanza el instalador de PowerShell sin cambiar la política de tu sistema.",
      },
      {
        title: "Haz doble clic",
        body: "Si SmartScreen pregunta: Más información → Ejecutar de todas formas. Instala Git y Python 3.12 con winget.",
      },
      ASSISTANT_STEP("%USERPROFILE%\\odysseus\\.env"),
    ],
    transcript: [
      "==> Odysseus - Windows installer",
      "  install folder: C:\\Users\\tú\\odysseus",
      "  OK git: C:\\Program Files\\Git\\cmd\\git.exe",
      "  OK Python: …",
      "==> Cloning https://github.com/LuisArmando-TestCoder/Odysseus.git",
      "==> Running the Odysseus installer",
      ...WIZARD_TAIL("C:\\Users\\tú\\odysseus\\.env").slice(1),
    ],
  },
  linux: {
    label: "Linux",
    method: "paste",
    file: "install-linux.sh",
    oneLiner: `bash -c "$(curl -fsSL ${RAW}/install-linux.sh)"`,
    prompt: "$",
    runFile: "bash ~/Downloads/install-linux.sh",
    verify: "sha256sum ~/Downloads/install-linux.sh",
    home: "~/odysseus",
    needs: "Debian, Ubuntu, Fedora, Arch, openSUSE, Alpine · usa tu gestor de paquetes",
    heroHint: "Copia una línea · pégala en tu terminal",
    steps: [
      { title: "Abre una terminal", body: "Ctrl Alt T en la mayoría de escritorios." },
      {
        title: "Pega la línea y pulsa Enter",
        body: "Instala git y Python 3.11+ con apt, dnf, pacman, zypper o apk y clona Odysseus en ~/odysseus.",
      },
      ASSISTANT_STEP("~/odysseus/.env"),
    ],
    transcript: [
      "▶ Odysseus — Linux installer",
      "  install folder: ~/odysseus",
      "  ✓ git: git version …",
      "  ✓ Python: Python 3.12 …",
      "  Cloning https://github.com/LuisArmando-TestCoder/Odysseus.git …",
      ...WIZARD_TAIL("~/odysseus/.env"),
    ],
  },
};

export const CHAPTERS = ["Inicio", "Instalar", "Módulos", "Ayuda", "Activación"] as const;

export const FAQ: { q: string; a: string; code?: string }[] = [
  {
    q: "¿Es seguro pegar un comando de internet?",
    a: "Lee el instalador antes de ejecutarlo: el código está en el repositorio y esta página publica su SHA-256. La línea descarga exactamente ese archivo desde la rama main de GitHub por HTTPS, y todo el script vive dentro de una función, así que una descarga cortada nunca se ejecuta a medias.",
  },
  {
    q: "¿Dónde se instala?",
    a: "En ~/odysseus (en Windows, %USERPROFILE%\\odysseus). Para usar otra carpeta, define ODYSSEUS_HOME antes de ejecutarlo. Si la carpeta ya existe y no es una instalación de Odysseus, el instalador se detiene sin tocarla.",
    code: "export ODYSSEUS_HOME=/ruta/que/prefieras",
  },
  {
    q: "macOS me pide instalar las herramientas de línea de comandos",
    a: "Es normal la primera vez: haz clic en Instalar en el diálogo del sistema. El instalador espera hasta que terminen y continúa solo. Si Homebrew pide tu contraseña, es para instalar Python 3.12.",
  },
  {
    q: "Windows SmartScreen bloquea el archivo",
    a: "Elige Más información → Ejecutar de todas formas. Si prefieres no descargar nada, pega la línea de PowerShell de la pestaña Windows: hace lo mismo.",
  },
  {
    q: "¿Cómo se actualiza?",
    a: "Revisa la rama main cada hora y al iniciar sesión (launchd en macOS, cron en Linux, Tarea programada en Windows). Si una actualización no pasa la comprobación de salud, vuelve a la versión anterior. Ejecutar el instalador otra vez repara la instalación sin borrar tus datos.",
  },
  {
    q: "El puerto 7860 está ocupado",
    a: "Cierra el programa que lo usa, o define otro puerto en tu .env y vuelve a abrir Odysseus.",
    code: "APP_PORT=7861",
  },
  {
    q: "¿Puedo instalarlo sin preguntas (servidor, CI)?",
    a: "Sí. Descarga el archivo y pásale --non-interactive: usa el .env que ya tengas y se salta el asistente.",
    code: "bash install-linux.sh --non-interactive",
  },
  {
    q: "¿Cómo lo desinstalo?",
    a: "Desde la carpeta de instalación, el desinstalador detiene Odysseus y quita la actualización programada, el inicio con sesión y los accesos directos. Tu código, tu .env y data/ se quedan: bórralos tú si quieres quitarlo todo (haz copia antes).",
    code: "python installer/uninstall.py",
  },
];
