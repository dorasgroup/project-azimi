import "./globals.css";
import { Providers } from "@/shared/components/providers";

export const metadata = {
  title: "Ideation Panel",
  description: "Internal RAG system for Ideation knowledge workflows",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html dir="rtl" lang="fa">
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
