import "./globals.css";
import Link from "next/link";

export const metadata = { title: "Nostro", description: "Settlement reconciliation" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <nav>
          <Link href="/"><strong>Nostro</strong></Link>
          <Link href="/">Close</Link>
          <Link href="/exceptions">Exceptions</Link>
        </nav>
        <main>{children}</main>
      </body>
    </html>
  );
}
