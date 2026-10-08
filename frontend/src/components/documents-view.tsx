"use client";

import { useEffect, useState, type DragEvent } from "react";

import { Button, Card, ErrorNote, PageTitle, Spinner, StatusBadge } from "@/components/ui";
import { api, errorMessage, newId } from "@/lib/api-client";
import { hasPending, mergeFirstPage } from "@/lib/documents";
import { fileType, formatBytes, formatDateTime } from "@/lib/format";
import type { DocumentInfo, DocumentList, UploadResult } from "@/lib/types";

const PAGE_SIZE = 50;
// While a file is processed, check again this often (only then: no checks when all is done).
const POLL_MS = 3000;
const ACCEPT = ".pdf,.docx,.txt,.md,.markdown,.html,.htm";

type Upload = {
  id: string;
  name: string;
  state: "uploading" | "duplicate" | "failed";
  message?: string;
};

export function DocumentsView() {
  const [documents, setDocuments] = useState<DocumentInfo[] | null>(null);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [uploads, setUploads] = useState<Upload[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [refreshes, setRefreshes] = useState(0); // +1 loads the first page again

  useEffect(() => {
    let active = true;
    api<DocumentList>(`/documents?limit=${PAGE_SIZE}`).then(
      (page) => {
        if (!active) return;
        setDocuments((current) => mergeFirstPage(current ?? [], page));
        setNextCursor((cursor) => cursor ?? page.next_cursor);
        setError(null);
      },
      (err: unknown) => active && setError(errorMessage(err)),
    );
    return () => {
      active = false;
    };
  }, [refreshes]);

  const pending = documents !== null && hasPending(documents);
  useEffect(() => {
    if (!pending) return;
    const timer = window.setTimeout(() => setRefreshes((count) => count + 1), POLL_MS);
    return () => window.clearTimeout(timer);
  }, [pending, documents]);

  async function uploadFiles(files: File[]) {
    for (const file of files) {
      const id = newId();
      setUploads((list) => [...list, { id, name: file.name, state: "uploading" }]);
      try {
        const form = new FormData();
        form.append("file", file);
        const result = await api<UploadResult>("/documents", { method: "POST", body: form });
        const uploaded = result.document;
        setDocuments((list) => [uploaded, ...(list ?? []).filter((d) => d.id !== uploaded.id)]);
        setUploads((list) =>
          result.duplicate
            ? list.map((u) =>
                u.id === id
                  ? { ...u, state: "duplicate", message: "Uploaded before: nothing changed." }
                  : u,
              )
            : list.filter((u) => u.id !== id),
        );
      } catch (err) {
        setUploads((list) =>
          list.map((u) =>
            u.id === id ? { ...u, state: "failed", message: errorMessage(err) } : u,
          ),
        );
      }
    }
  }

  async function deleteDocument(document: DocumentInfo) {
    if (!window.confirm(`Delete "${document.filename}"? This cannot be undone.`)) return;
    try {
      const updated = await api<DocumentInfo>(`/documents/${document.id}`, { method: "DELETE" });
      setDocuments((list) => (list ?? []).map((d) => (d.id === updated.id ? updated : d)));
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  async function loadMore() {
    if (!nextCursor) return;
    try {
      const page = await api<DocumentList>(`/documents?limit=${PAGE_SIZE}&cursor=${nextCursor}`);
      setDocuments((list) => [...(list ?? []), ...page.items]);
      setNextCursor(page.next_cursor);
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  return (
    <>
      <PageTitle
        title="Documents"
        description="Upload the files to answer questions from. Each file is read, cut into chunks and indexed; that takes a few seconds."
      />
      <div className="space-y-6">
        <Dropzone onFiles={(files) => void uploadFiles(files)} />
        {uploads.length > 0 && (
          <UploadList
            uploads={uploads}
            onDismiss={(id) => setUploads((list) => list.filter((u) => u.id !== id))}
          />
        )}
        <ErrorNote message={error} />
        <Card title="Your documents">
          {documents === null ? (
            <p className="text-sm text-gray-500">Loading...</p>
          ) : documents.length === 0 ? (
            <p className="text-sm text-gray-500">No documents yet. Upload one above.</p>
          ) : (
            <DocumentTable documents={documents} onDelete={(d) => void deleteDocument(d)} />
          )}
          {nextCursor && (
            <Button variant="secondary" className="mt-4" onClick={() => void loadMore()}>
              Load more
            </Button>
          )}
        </Card>
      </div>
    </>
  );
}

function Dropzone({ onFiles }: { onFiles: (files: File[]) => void }) {
  const [dragging, setDragging] = useState(false);

  function drop(event: DragEvent<HTMLLabelElement>) {
    event.preventDefault();
    setDragging(false);
    onFiles(Array.from(event.dataTransfer.files));
  }

  return (
    <label
      onDragOver={(event) => {
        event.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={drop}
      className={`flex cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed px-6 py-10 text-center transition-colors focus-within:ring-2 focus-within:ring-indigo-300 ${
        dragging
          ? "border-indigo-500 bg-indigo-50"
          : "border-gray-300 bg-white hover:border-indigo-400"
      }`}
    >
      <span className="text-sm font-medium text-gray-800">Drop files here, or click to choose</span>
      <span className="mt-1 text-xs text-gray-500">PDF, Word (.docx), text, Markdown or HTML</span>
      <input
        type="file"
        multiple
        accept={ACCEPT}
        aria-label="Choose files to upload"
        className="sr-only"
        onChange={(event) => {
          const input = event.currentTarget;
          onFiles(Array.from(input.files ?? []));
          input.value = ""; // the same file can be chosen again
        }}
      />
    </label>
  );
}

function UploadList({
  uploads,
  onDismiss,
}: {
  uploads: Upload[];
  onDismiss: (id: string) => void;
}) {
  return (
    <ul className="space-y-2" aria-label="Uploads">
      {uploads.map((upload) => (
        <li
          key={upload.id}
          className={`flex items-center gap-3 rounded-lg border px-3 py-2 text-sm ${
            upload.state === "failed"
              ? "border-red-200 bg-red-50 text-red-800"
              : "border-gray-200 bg-white text-gray-700"
          }`}
        >
          {upload.state === "uploading" && <Spinner />}
          <span className="font-medium">{upload.name}</span>
          <span className="text-gray-500">
            {upload.state === "uploading" ? "Uploading..." : upload.message}
          </span>
          {upload.state !== "uploading" && (
            <button
              type="button"
              onClick={() => onDismiss(upload.id)}
              className="ml-auto text-xs text-gray-500 hover:text-gray-800"
            >
              Dismiss
            </button>
          )}
        </li>
      ))}
    </ul>
  );
}

function DocumentTable({
  documents,
  onDelete,
}: {
  documents: DocumentInfo[];
  onDelete: (document: DocumentInfo) => void;
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead className="border-b border-gray-200 text-xs text-gray-500 uppercase">
          <tr>
            <th className="py-2 pr-4 font-medium">Name</th>
            <th className="py-2 pr-4 font-medium">Type</th>
            <th className="py-2 pr-4 font-medium">Size</th>
            <th className="py-2 pr-4 font-medium">Pages</th>
            <th className="py-2 pr-4 font-medium">Chunks</th>
            <th className="py-2 pr-4 font-medium">Status</th>
            <th className="py-2 pr-4 font-medium">Uploaded</th>
            <th className="py-2">
              <span className="sr-only">Actions</span>
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {documents.map((document) => (
            <tr key={document.id}>
              <td className="py-2 pr-4 font-medium text-gray-900">
                {document.filename}
                {document.status === "failed" && document.error && (
                  <p className="mt-0.5 text-xs font-normal text-red-700">{document.error}</p>
                )}
              </td>
              <td className="py-2 pr-4">{fileType(document.mime_type)}</td>
              <td className="py-2 pr-4 whitespace-nowrap">{formatBytes(document.size_bytes)}</td>
              <td className="py-2 pr-4">{document.page_count ?? "–"}</td>
              <td className="py-2 pr-4">
                {document.status === "ready" ? document.chunk_count : "–"}
              </td>
              <td className="py-2 pr-4">
                <StatusBadge status={document.status} />
              </td>
              <td className="py-2 pr-4 whitespace-nowrap text-gray-500">
                {formatDateTime(document.created_at)}
              </td>
              <td className="py-2 text-right">
                <Button
                  variant="danger"
                  disabled={document.status === "deleting"}
                  aria-label={`Delete ${document.filename}`}
                  onClick={() => onDelete(document)}
                >
                  Delete
                </Button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
