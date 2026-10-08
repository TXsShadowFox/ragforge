"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";

import { Badge, Button, ErrorNote, PageTitle } from "@/components/ui";
import {
  api,
  errorMessage,
  goToLoginIfLoggedOut,
  newId,
  sendJson,
  toApiError,
} from "@/lib/api-client";
import { formatMs } from "@/lib/format";
import { readEvents } from "@/lib/sse";
import type { ChatResult, Citation } from "@/lib/types";

type Turn = {
  id: string;
  question: string;
  answer: string;
  citations: Citation[];
  messageId: string | null;
  cacheHit: boolean;
  latencyMs: number | null;
  usage: ChatResult["usage"] | null;
  error: string | null;
  streaming: boolean;
  rating: "up" | "down" | null;
};

/** Try the chat as your users will: streamed answers, sources, and follow-up questions. */
export function PlaygroundView() {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [question, setQuestion] = useState("");
  const [topK, setTopK] = useState(5);
  const [busy, setBusy] = useState(false);
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => {
    end.current?.scrollIntoView({ block: "end" });
  }, [turns]);

  function update(id: string, patch: Partial<Turn>) {
    setTurns((list) => list.map((turn) => (turn.id === id ? { ...turn, ...patch } : turn)));
  }

  async function ask(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const text = question.trim();
    if (!text || busy) return;
    const id = newId();
    setBusy(true);
    setQuestion("");
    setTurns((list) => [
      ...list,
      {
        id,
        question: text,
        answer: "",
        citations: [],
        messageId: null,
        cacheHit: false,
        latencyMs: null,
        usage: null,
        error: null,
        streaming: true,
        rating: null,
      },
    ]);
    try {
      const response = await fetch("/api/v1/chat", {
        method: "POST",
        headers: { "content-type": "application/json", accept: "text/event-stream" },
        body: JSON.stringify({ question: text, session_id: sessionId, top_k: topK, stream: true }),
      });
      if (!response.ok || !response.body) {
        const error = await toApiError(response);
        goToLoginIfLoggedOut(error);
        throw error;
      }
      let answer = "";
      for await (const event of readEvents(response.body)) {
        if (event.name === "token") {
          answer += (event.data as { text: string }).text;
          update(id, { answer });
        } else if (event.name === "done") {
          const result = event.data as ChatResult;
          update(id, {
            answer: result.answer,
            citations: result.citations,
            messageId: result.message_id,
            cacheHit: result.cache_hit,
            latencyMs: result.latency_ms,
            usage: result.usage,
          });
          setSessionId(result.session_id);
        } else if (event.name === "error") {
          const message = (event.data as { message?: string }).message;
          update(id, { error: message ?? "The answer could not be finished." });
        }
      }
    } catch (err) {
      update(id, { error: errorMessage(err) });
    }
    update(id, { streaming: false });
    setBusy(false);
  }

  async function rate(turn: Turn, rating: "up" | "down") {
    if (!turn.messageId) return;
    update(turn.id, { rating });
    try {
      await api(`/messages/${turn.messageId}/feedback`, sendJson("POST", { rating }));
    } catch (err) {
      update(turn.id, { rating: turn.rating, error: errorMessage(err) });
    }
  }

  return (
    <>
      <PageTitle
        title="Chat playground"
        description="Ask questions about your documents. Follow-up questions use the conversation; New conversation starts again."
      />
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <label className="flex items-center gap-2 text-gray-700">
          Sources per answer
          <select
            value={topK}
            onChange={(event) => setTopK(Number(event.target.value))}
            className="rounded-lg border border-gray-300 bg-white px-2 py-1"
          >
            {[1, 2, 3, 5, 8, 10].map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>
        <Button
          variant="secondary"
          disabled={busy || turns.length === 0}
          onClick={() => {
            setTurns([]);
            setSessionId(null);
          }}
        >
          New conversation
        </Button>
      </div>

      <div className="mt-6 space-y-6" aria-live="polite">
        {turns.length === 0 && (
          <p className="rounded-xl border border-dashed border-gray-300 bg-white px-6 py-10 text-center text-sm text-gray-500">
            No questions yet. Upload documents first, then ask about them below.
          </p>
        )}
        {turns.map((turn) => (
          <div key={turn.id} className="space-y-3">
            <p className="ml-auto w-fit max-w-[80%] rounded-2xl rounded-br-sm bg-indigo-600 px-4 py-2 text-sm whitespace-pre-wrap text-white">
              {turn.question}
            </p>
            <Answer turn={turn} onRate={(rating) => void rate(turn, rating)} />
          </div>
        ))}
        <div ref={end} className="scroll-mb-28" />
      </div>

      <form
        onSubmit={ask}
        className="sticky bottom-0 mt-6 flex gap-2 bg-gray-50/95 py-4 backdrop-blur"
      >
        <label className="sr-only" htmlFor="question">
          Your question
        </label>
        <textarea
          id="question"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              event.currentTarget.form?.requestSubmit();
            }
          }}
          rows={2}
          maxLength={2000}
          placeholder="Ask a question about your documents (Enter to send, Shift+Enter for a new line)"
          className="flex-1 resize-none rounded-xl border border-gray-300 bg-white px-4 py-3 text-sm shadow-sm focus:border-indigo-500 focus:ring-2 focus:ring-indigo-200 focus:outline-none"
        />
        <Button type="submit" disabled={busy || !question.trim()} className="px-5">
          {busy ? "Answering..." : "Ask"}
        </Button>
      </form>
    </>
  );
}

function Answer({ turn, onRate }: { turn: Turn; onRate: (rating: "up" | "down") => void }) {
  return (
    <article className="max-w-[90%] rounded-2xl rounded-bl-sm border border-gray-200 bg-white px-4 py-3 shadow-sm">
      <p className="text-sm whitespace-pre-wrap text-gray-900">
        {turn.answer}
        {turn.streaming && <span className="ml-0.5 animate-pulse">&#9612;</span>}
      </p>
      <div className="mt-2">
        <ErrorNote message={turn.error} />
      </div>
      {turn.citations.length > 0 && (
        <ol className="mt-3 space-y-1 text-sm" aria-label="Sources">
          {turn.citations.map((citation) => (
            <li key={citation.number}>
              <details className="rounded-lg bg-gray-50 px-3 py-1.5">
                <summary className="cursor-pointer text-gray-700">
                  [{citation.number}] {citation.filename}
                  {citation.page !== null && `, page ${citation.page}`}
                </summary>
                <p className="mt-1 text-xs whitespace-pre-wrap text-gray-600">{citation.snippet}</p>
              </details>
            </li>
          ))}
        </ol>
      )}
      {!turn.streaming && turn.messageId && (
        <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-gray-500">
          {turn.cacheHit ? (
            <Badge className="bg-emerald-100 text-emerald-800">from cache</Badge>
          ) : (
            <Badge className="bg-gray-100 text-gray-700">
              {turn.usage
                ? `${turn.usage.prompt_tokens} + ${turn.usage.completion_tokens} tokens`
                : "LLM"}
            </Badge>
          )}
          <span>{formatMs(turn.latencyMs)}</span>
          <span className="ml-auto flex gap-1">
            {(["up", "down"] as const).map((rating) => (
              <button
                key={rating}
                type="button"
                aria-label={rating === "up" ? "Good answer" : "Bad answer"}
                aria-pressed={turn.rating === rating}
                onClick={() => onRate(rating)}
                className={`rounded-md border px-2 py-0.5 ${
                  turn.rating === rating
                    ? "border-indigo-400 bg-indigo-50"
                    : "border-gray-200 hover:bg-gray-50"
                }`}
              >
                {rating === "up" ? "\u{1F44D}" : "\u{1F44E}"}
              </button>
            ))}
          </span>
        </div>
      )}
    </article>
  );
}
