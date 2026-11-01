// Server component: runs once at build time (output: "export"). It reads the
// installer files that `prebuild` synced into public/ and hands their size,
// line count and SHA-256 to the page, so the numbers people verify against
// are always the ones of the files actually being served.
import { createHash } from "crypto";
import { readFileSync } from "fs";
import path from "path";

import InstallPage from "./InstallPage";
import { FAQ, OS, OS_ORDER, REPO_URL, type Integrity } from "./content";

function facts(): Integrity {
  const out: Integrity = {};
  for (const k of OS_ORDER) {
    const name = OS[k].file;
    try {
      const buf = readFileSync(path.join(process.cwd(), "public", name));
      out[name] = {
        name,
        bytes: buf.length,
        lines: buf.toString("utf8").split("\n").filter((l, i, a) => i < a.length - 1 || l).length,
        sha256: createHash("sha256").update(buf).digest("hex"),
      };
    } catch {
      /* file missing: the page simply omits its verification row */
    }
  }
  return out;
}

export default function Page() {
  const integrity = facts();
  const jsonLd = [
    {
      "@context": "https://schema.org",
      "@type": "SoftwareApplication",
      name: "Odysseus",
      applicationCategory: "ProductivityApplication",
      operatingSystem: "macOS 12+, Windows 10/11, Linux",
      offers: { "@type": "Offer", price: "0", priceCurrency: "USD" },
      license: `${REPO_URL}/blob/main/LICENSE`,
      codeRepository: REPO_URL,
    },
    {
      "@context": "https://schema.org",
      "@type": "FAQPage",
      mainEntity: FAQ.map((f) => ({
        "@type": "Question",
        name: f.q,
        acceptedAnswer: { "@type": "Answer", text: f.code ? `${f.a} ${f.code}` : f.a },
      })),
    },
  ];

  return (
    <>
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }} />
      <InstallPage integrity={integrity} />
    </>
  );
}
