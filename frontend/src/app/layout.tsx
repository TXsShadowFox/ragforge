import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: { default: "RAGForge", template: "%s | RAGForge" },
  description: "Upload documents and chat with them, with sources.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en">
      <body className="min-h-screen">{children}</body>
    </html>
  );
}
