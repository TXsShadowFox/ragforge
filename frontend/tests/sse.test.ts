import { describe, expect, it } from "vitest";

import { parseEvent, readEvents, type ServerEvent } from "@/lib/sse";

const encoder = new TextEncoder();

function streamOf(chunks: Uint8Array[]): ReadableStream<Uint8Array> {
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(chunk);
      controller.close();
    },
  });
}

/** The text cut into pieces of `size` bytes, as a network might deliver it. */
function pieces(text: string, size: number): Uint8Array[] {
  const bytes = encoder.encode(text);
  const result: Uint8Array[] = [];
  for (let start = 0; start < bytes.length; start += size) {
    result.push(bytes.slice(start, start + size));
  }
  return result;
}

async function collect(body: ReadableStream<Uint8Array>): Promise<ServerEvent[]> {
  const events: ServerEvent[] = [];
  for await (const event of readEvents(body)) events.push(event);
  return events;
}

const STREAM =
  'event: start\ndata: {"session_id": "s1"}\n\n' +
  'event: token\ndata: {"text": "Größe "}\n\n' +
  'event: token\ndata: {"text": "ok"}\n\n' +
  'event: done\ndata: {"answer": "Größe ok"}\n\n';

const EXPECTED: ServerEvent[] = [
  { name: "start", data: { session_id: "s1" } },
  { name: "token", data: { text: "Größe " } },
  { name: "token", data: { text: "ok" } },
  { name: "done", data: { answer: "Größe ok" } },
];

describe("readEvents", () => {
  it.each([1, 3, 7, 1000])("reads the same events in pieces of %i bytes", async (size) => {
    // Size 1 also cuts the two-byte letters (ö, ß) in half.
    expect(await collect(streamOf(pieces(STREAM, size)))).toEqual(EXPECTED);
  });

  it("accepts Windows line endings", async () => {
    const stream = STREAM.replace(/\n/g, "\r\n");
    expect(await collect(streamOf(pieces(stream, 5)))).toEqual(EXPECTED);
  });

  it("drops an unfinished last event", async () => {
    const stream = 'event: token\ndata: {"text": "a"}\n\nevent: token\ndata: {"te';
    expect(await collect(streamOf(pieces(stream, 4)))).toEqual([
      { name: "token", data: { text: "a" } },
    ]);
  });
});

describe("parseEvent", () => {
  it("skips comments and joins data lines", () => {
    expect(parseEvent(': keep-alive\nevent: done\ndata: {"a":\ndata: 1}')).toEqual({
      name: "done",
      data: { a: 1 },
    });
  });

  it("calls an event without a name 'message'", () => {
    expect(parseEvent('data: {"x": true}')).toEqual({ name: "message", data: { x: true } });
  });

  it("ignores events without data or with broken JSON", () => {
    expect(parseEvent("event: ping")).toBeNull();
    expect(parseEvent("event: token\ndata: {broken")).toBeNull();
  });
});
