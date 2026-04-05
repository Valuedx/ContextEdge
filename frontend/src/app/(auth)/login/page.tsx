"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { login } from "@/lib/auth";
import { useAuthStore } from "@/lib/stores/auth-store";

export default function LoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const router = useRouter();
  const setAuthenticated = useAuthStore((s) => s.setAuthenticated);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const res = await login(email, password);
      setAuthenticated(res.access_token);
      router.push("/overview");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Login failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="relative flex min-h-screen items-center justify-center px-4 py-12">
      <div
        className="pointer-events-none absolute inset-0 opacity-40 blur-3xl"
        aria-hidden
      >
        <div className="absolute left-1/4 top-1/4 h-64 w-64 rounded-full bg-violet-500/30" />
        <div className="absolute bottom-1/4 right-1/4 h-72 w-72 rounded-full bg-cyan-500/25" />
      </div>
      <Card className="glass-panel-strong relative w-full max-w-md border-white/20">
        <CardHeader className="space-y-2 text-center">
          <CardTitle className="bg-gradient-to-br from-slate-900 via-violet-800 to-cyan-700 bg-clip-text text-3xl font-semibold tracking-tight text-transparent dark:from-white dark:via-violet-100 dark:to-cyan-200">
            ContextEdge
          </CardTitle>
          <p className="text-sm text-muted-foreground">
            Sign in to your workspace
          </p>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4">
            {error && (
              <div className="rounded-md bg-red-50 p-3 text-sm text-red-700 dark:bg-red-900/20 dark:text-red-400">
                {error}
              </div>
            )}
            <div className="space-y-2">
              <Label htmlFor="email">Email</Label>
              <Input
                id="email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="admin@contextedge.local"
                required
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="password">Password</Label>
              <Input
                id="password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
              />
            </div>
            <Button type="submit" className="w-full" disabled={loading}>
              {loading ? "Signing in..." : "Sign in"}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
