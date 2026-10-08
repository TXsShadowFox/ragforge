/**
 * Server-Sent Events from a fetch() body. EventSource cannot send a POST, so the chat
 * reads the stream itself. (widget/widget.js has the same logic in plain JavaScript.)
 */

export type ServerEvent = { name: string; data: unknown };

/** The events of a response body, in order. Works however the bytes are split. */
export async function* readEvents(body: ReadableStream<Uint8Array>): AsyncGenerator<ServerEvent> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (value) buffer += decoder.decode(value, { stream: true });
    buffer = buffer.replace(/\r\n/g, "\n");
    let end = buffer.indexOf("\n\n");
    while (end >= 0) {
      const event = parseEvent(buffer.slice(0, end));
      buffer = buffer.slice(end + 2);
      if (event) yield event;
      end = buffer.indexOf("\n\n");
    }
    if (done) return;
  }
}

/** One event: "event: <name>" and "data: <json>" lines. Lines starting with ":" are comments. */
export function parseEvent(block: string): ServerEvent | null {
  let name = "message";
  const data: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith(":")) continue;
    const colon = line.indexOf(":");
    const field = colon < 0 ? line : line.slice(0, colon);
    const value = colon < 0 ? "" : line.slice(colon + 1).replace(/^ /, "");
    if (field === "event") name = value;
    else if (field === "data") data.push(value);
  }
  if (data.length === 0) return null;
  try {
    return { name, data: JSON.parse(data.join("\n")) };
  } catch {
    return null;
  }
}
