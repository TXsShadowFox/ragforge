"use client";

import Link from "next/link";
import { useState, type FormEvent, type InputHTMLAttributes } from "react";

import { Button, ErrorNote } from "@/components/ui";
import { errorMessage, toApiError } from "@/lib/api-client";

/** Log in, or sign up a new company. The dashboard's server keeps the login token. */
export function AuthForm({ mode }: { mode: "login" | "signup" }) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const isSignup = mode === "signup";

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const fields = Object.fromEntries(new FormData(event.currentTarget).entries());
    try {
      const response = await fetch(isSignup ? "/api/signup" : "/api/session", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(fields),
      });
      if (response.ok) {
        // A full page load, not router.push(): Next.js would keep this page alive in the
        // background, with the typed password still in its form.
        // eslint-disable-next-line @next/next/no-location-assign-relative-destination
        window.location.assign("/documents");
        return;
      }
      setError((await toApiError(response)).message);
    } catch (err) {
      setError(errorMessage(err));
    }
    setBusy(false);
  }

  return (
    <main className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-sm">
        <p className="mb-6 text-center text-2xl font-semibold text-indigo-600">RAGForge</p>
        <form
          onSubmit={submit}
          className="space-y-4 rounded-xl border border-gray-200 bg-white p-6 shadow-sm"
        >
          <h1 className="text-lg font-semibold text-gray-900">
            {isSignup ? "Create your account" : "Log in"}
          </h1>
          {isSignup && (
            <Field label="Company name" name="tenant_name" autoComplete="organization" />
          )}
          <Field label="Email" name="email" type="email" autoComplete="email" />
          <Field
            label="Password"
            name="password"
            type="password"
            autoComplete={isSignup ? "new-password" : "current-password"}
            minLength={isSignup ? 8 : undefined}
            hint={isSignup ? "At least 8 characters." : undefined}
          />
          <ErrorNote message={error} />
          <Button type="submit" disabled={busy} className="w-full py-2">
            {busy ? "Please wait..." : isSignup ? "Sign up" : "Log in"}
          </Button>
        </form>
        <p className="mt-4 text-center text-sm text-gray-600">
          {isSignup ? "Already have an account? " : "New here? "}
          <Link
            href={isSignup ? "/login" : "/signup"}
            className="font-medium text-indigo-600 hover:underline"
          >
            {isSignup ? "Log in" : "Create an account"}
          </Link>
        </p>
      </div>
    </main>
  );
}

function Field({
  label,
  hint,
  ...input
}: InputHTMLAttributes<HTMLInputElement> & { label: string; hint?: string }) {
  return (
    <label className="block text-sm">
      <span className="font-medium text-gray-700">{label}</span>
      <input
        required
        {...input}
        className="mt-1 block w-full rounded-lg border border-gray-300 px-3 py-2 focus:border-indigo-500 focus:ring-2 focus:ring-indigo-200 focus:outline-none"
      />
      {hint && <span className="mt-1 block text-xs text-gray-500">{hint}</span>}
    </label>
  );
}
