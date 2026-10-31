import type { Metadata, Viewport } from "next";
import "./globals.scss";

export const metadata: Metadata = {
  title: "Odysseus — Instala tu investigador autónomo",
  description:
    "Odysseus es un asistente autónomo de investigación que corre en tu propio hardware. Instálalo con un único ejecutable para macOS, Windows y Linux.",
  openGraph: {
    title: "Odysseus — Instala tu investigador autónomo",
    description: "Un único ejecutable que se instala, se configura y se actualiza solo.",
    type: "website",
  },
};

export const viewport: Viewport = {
  themeColor: "#060b14",
  colorScheme: "dark",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="es">
      <head>
        <link rel="preload" href="/fonts/Inter-SemiBold.woff2" as="font" type="font/woff2" crossOrigin="" />
      </head>
      <body>{children}</body>
    </html>
  );
}
