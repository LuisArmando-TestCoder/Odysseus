import type { Metadata } from "next";
import "./globals.scss";

export const metadata: Metadata = {
  title: "Odysseus — Descargar",
  description:
    "Descarga e instala Odysseus con un único ejecutable para Linux, Windows y macOS.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="es">
      <body>{children}</body>
    </html>
  );
}