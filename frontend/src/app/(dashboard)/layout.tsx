import type { ReactNode } from "react";

import { Shell } from "@/components/shell";

/** Every page after login: the navigation bar around the page. */
export default function DashboardLayout({ children }: { children: ReactNode }) {
  return <Shell>{children}</Shell>;
}
