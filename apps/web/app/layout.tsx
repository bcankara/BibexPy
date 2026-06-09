import type { Metadata } from "next";
import { I18nProvider } from "@/lib/i18n";
import { AppShell } from "@/components/AppShell";
import { DialogProvider } from "@/components/Dialogs";
import { InitialLoadGate } from "@/components/InitialLoadGate";
import "./globals.css";

export const metadata: Metadata = {
  title: "BibexPy V2",
  description: "Harmonizing the Bibliometric Symphony of Scopus and Web of Science",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen flex flex-col bg-bg">
        <InitialLoadGate />
        <I18nProvider>
          <DialogProvider>
            <AppShell>{children}</AppShell>
          </DialogProvider>
        </I18nProvider>
      </body>
    </html>
  );
}
