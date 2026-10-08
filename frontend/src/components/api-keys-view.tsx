"use client";

import { useEffect, useState, type FormEvent } from "react";

import { Badge, Button, Card, ErrorNote, PageTitle } from "@/components/ui";
import { api, errorMessage, sendJson } from "@/lib/api-client";
import { formatDateTime } from "@/lib/format";
import type { ApiKeyInfo, ApiKeyKind, CreatedApiKey } from "@/lib/types";

export function ApiKeysView() {
  const [keys, setKeys] = useState<ApiKeyInfo[] | null>(null);
  const [created, setCreated] = useState<CreatedApiKey | null>(null);
  const [publicApiUrl, setPublicApiUrl] = useState("");
  const [kind, setKind] = useState<ApiKeyKind>("secret");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reloads, setReloads] = useState(0); // +1 loads the list again

  useEffect(() => {
    let active = true;
    api<{ items: ApiKeyInfo[] }>("/api-keys").then(
      (list) => active && setKeys(list.items),
      (err: unknown) => active && setError(errorMessage(err)),
    );
    return () => {
      active = false;
    };
  }, [reloads]);

  useEffect(() => {
    let active = true;
    fetch("/api/config")
      .then((response) => response.json() as Promise<{ publicApiUrl: string }>)
      .then(
        (config) => active && setPublicApiUrl(config.publicApiUrl),
        () => undefined,
      );
    return () => {
      active = false;
    };
  }, []);

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const fields = new FormData(form);
    const origins = String(fields.get("origins") ?? "")
      .split(/\s+/)
      .filter(Boolean);
    setBusy(true);
    setError(null);
    try {
      const key = await api<CreatedApiKey>(
        "/api-keys",
        sendJson("POST", {
          name: fields.get("name"),
          kind,
          allowed_origins: kind === "public" ? origins : [],
        }),
      );
      setCreated(key);
      form.reset();
      setKind("secret");
      setReloads((count) => count + 1);
    } catch (err) {
      setError(errorMessage(err));
    }
    setBusy(false);
  }

  async function revoke(key: ApiKeyInfo) {
    const message = `Revoke "${key.name}"? Programs and websites that use it stop working at once.`;
    if (!window.confirm(message)) return;
    try {
      await api<void>(`/api-keys/${key.id}`, { method: "DELETE" });
      setReloads((count) => count + 1);
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  return (
    <>
      <PageTitle
        title="API keys"
        description="Secret keys are for your servers and scripts. Public keys are for the chat widget on your websites: they only work for the chat, and only on the websites you list."
      />
      <div className="space-y-6">
        {created && (
          <NewKey created={created} publicApiUrl={publicApiUrl} onDone={() => setCreated(null)} />
        )}
        <ErrorNote message={error} />
        <Card title="Create a key">
          <form onSubmit={create} className="grid gap-4 md:grid-cols-2">
            <label className="block text-sm">
              <span className="font-medium text-gray-700">Name</span>
              <input
                name="name"
                required
                maxLength={100}
                placeholder="Website chat, or Backend server"
                className="mt-1 block w-full rounded-lg border border-gray-300 px-3 py-2 focus:border-indigo-500 focus:ring-2 focus:ring-indigo-200 focus:outline-none"
              />
            </label>
            <fieldset className="text-sm">
              <legend className="font-medium text-gray-700">Type</legend>
              <div className="mt-2 flex gap-4">
                {(["secret", "public"] as const).map((option) => (
                  <label key={option} className="flex items-center gap-2">
                    <input
                      type="radio"
                      name="kind"
                      value={option}
                      checked={kind === option}
                      onChange={() => setKind(option)}
                    />
                    {option === "secret" ? "Secret (servers)" : "Public (chat widget)"}
                  </label>
                ))}
              </div>
            </fieldset>
            {kind === "public" && (
              <label className="block text-sm md:col-span-2">
                <span className="font-medium text-gray-700">Allowed websites</span>
                <textarea
                  name="origins"
                  required
                  rows={2}
                  placeholder="https://www.example.com"
                  className="mt-1 block w-full rounded-lg border border-gray-300 px-3 py-2 font-mono text-xs focus:border-indigo-500 focus:ring-2 focus:ring-indigo-200 focus:outline-none"
                />
                <span className="mt-1 block text-xs text-gray-500">
                  One per line: the scheme, the host and the port, like http://localhost:5500.
                </span>
              </label>
            )}
            <div className="md:col-span-2">
              <Button type="submit" disabled={busy}>
                {busy ? "Creating..." : "Create key"}
              </Button>
            </div>
          </form>
        </Card>
        <Card title="Your keys">
          {keys === null ? (
            <p className="text-sm text-gray-500">Loading...</p>
          ) : keys.length === 0 ? (
            <p className="text-sm text-gray-500">No keys yet.</p>
          ) : (
            <KeyTable keys={keys} onRevoke={(key) => void revoke(key)} />
          )}
        </Card>
      </div>
    </>
  );
}

function NewKey({
  created,
  publicApiUrl,
  onDone,
}: {
  created: CreatedApiKey;
  publicApiUrl: string;
  onDone: () => void;
}) {
  const snippet = `<script src="${publicApiUrl}/widget.js" data-api-key="${created.key}" async></script>`;
  return (
    <section className="space-y-4 rounded-xl border border-amber-300 bg-amber-50 p-5">
      <div>
        <h2 className="text-base font-semibold text-gray-900">Copy your new key now</h2>
        <p className="mt-1 text-sm text-gray-700">
          It is shown only once: we only keep a fingerprint (a hash) of it.
        </p>
      </div>
      <CopyField
        label={created.kind === "public" ? "Public key" : "Secret key"}
        value={created.key}
      />
      {created.kind === "public" && publicApiUrl ? (
        <div className="space-y-2">
          <p className="text-sm text-gray-700">
            Add the chat to your website with this tag, before &lt;/body&gt;:
          </p>
          <CopyField label="Embed code" value={snippet} />
        </div>
      ) : (
        <p className="text-sm text-gray-700">
          Send it as <code className="font-mono">Authorization: Bearer &lt;key&gt;</code>. Never put
          a secret key in a web page.
        </p>
      )}
      <Button variant="secondary" onClick={onDone}>
        Done
      </Button>
    </section>
  );
}

function CopyField({ label, value }: { label: string; value: string }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      // no clipboard access: the text can still be selected and copied by hand
    }
  }

  return (
    <div>
      <p className="text-xs font-medium text-gray-500 uppercase">{label}</p>
      <div className="mt-1 flex gap-2">
        <code
          aria-label={label}
          className="flex-1 overflow-x-auto rounded-lg border border-gray-200 bg-white px-3 py-2 font-mono text-xs whitespace-nowrap"
        >
          {value}
        </code>
        <Button variant="secondary" onClick={() => void copy()}>
          {copied ? "Copied" : "Copy"}
        </Button>
      </div>
    </div>
  );
}

function KeyTable({ keys, onRevoke }: { keys: ApiKeyInfo[]; onRevoke: (key: ApiKeyInfo) => void }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead className="border-b border-gray-200 text-xs text-gray-500 uppercase">
          <tr>
            <th className="py-2 pr-4 font-medium">Name</th>
            <th className="py-2 pr-4 font-medium">Type</th>
            <th className="py-2 pr-4 font-medium">Key</th>
            <th className="py-2 pr-4 font-medium">Websites</th>
            <th className="py-2 pr-4 font-medium">Created</th>
            <th className="py-2 pr-4 font-medium">Last used</th>
            <th className="py-2">
              <span className="sr-only">Actions</span>
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {keys.map((key) => (
            <tr key={key.id} className={key.revoked_at ? "text-gray-400" : ""}>
              <td className="py-2 pr-4 font-medium">{key.name}</td>
              <td className="py-2 pr-4">
                <Badge
                  className={
                    key.kind === "public" ? "bg-sky-100 text-sky-800" : "bg-gray-100 text-gray-700"
                  }
                >
                  {key.kind}
                </Badge>
              </td>
              <td className="py-2 pr-4 font-mono text-xs">{key.prefix}...</td>
              <td className="py-2 pr-4 text-xs">{key.allowed_origins.join(", ") || "–"}</td>
              <td className="py-2 pr-4 whitespace-nowrap">{formatDateTime(key.created_at)}</td>
              <td className="py-2 pr-4 whitespace-nowrap">
                {key.last_used_at ? formatDateTime(key.last_used_at) : "never"}
              </td>
              <td className="py-2 text-right">
                {key.revoked_at ? (
                  <span className="text-xs">revoked</span>
                ) : (
                  <Button
                    variant="danger"
                    aria-label={`Revoke ${key.name}`}
                    onClick={() => onRevoke(key)}
                  >
                    Revoke
                  </Button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
