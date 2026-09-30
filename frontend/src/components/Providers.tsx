"use client";

import { AuthProvider } from "@/lib/auth";
import { DataFlagsProvider } from "@/lib/dataflags";
import { AppShell } from "./AppShell";

export function Providers({ children }: { children: React.ReactNode }) {
  return (
    <AuthProvider>
      <DataFlagsProvider>
        <AppShell>{children}</AppShell>
      </DataFlagsProvider>
    </AuthProvider>
  );
}
