/** The documents list while files are being processed. */

import type { DocumentInfo, DocumentList, DocumentStatus } from "@/lib/types";

/** Documents in these states change soon, so the page checks again. */
export const PENDING_STATUSES: ReadonlySet<DocumentStatus> = new Set([
  "uploaded",
  "processing",
  "deleting",
]);

export function hasPending(documents: DocumentInfo[]): boolean {
  return documents.some((document) => PENDING_STATUSES.has(document.status));
}

/**
 * Put a fresh first page into the list. The list is newest first, and IDs are uuidv7
 * (they sort by time), so documents older than the page came from "Load more": keep them.
 * Any other document that is not on the fresh page was deleted.
 */
export function mergeFirstPage(current: DocumentInfo[], page: DocumentList): DocumentInfo[] {
  const oldest = page.items.at(-1)?.id;
  if (page.next_cursor === null || oldest === undefined) return page.items;
  return [...page.items, ...current.filter((document) => document.id < oldest)];
}
