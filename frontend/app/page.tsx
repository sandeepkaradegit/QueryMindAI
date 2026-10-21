"use client";

import { useEffect, useState } from "react";
import { api, ApiError, type ConversationHistoryTurn, type QueryResult } from "@/lib/api";
import { SortableTable } from "@/components/SortableTable";
import { SimpleChart } from "@/components/SimpleChart";

type Turn =
  | { kind: "user"; text: string }
  | { kind: "result"; result: QueryResult }
  | { kind: "error"; text: string; ambiguities?: { term: string; candidates: { table_name: string; column_name: string }[] }[] };

const CHAT_HISTORY_STORAGE_KEY = "querymind:chat-history:v1";

function loadStoredTurns(): Turn[] {
  if (typeof window === "undefined") {
    return [];
  }
  const raw = window.localStorage.getItem(CHAT_HISTORY_STORAGE_KEY);
  if (!raw) {
    return [];
  }
  try {
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) {
      return [];
    }
    return parsed.filter((turn): turn is Turn => {
      if (typeof turn !== "object" || turn === null || !("kind" in turn)) {
        return false;
      }
      const kind = (turn as { kind?: unknown }).kind;
      if (kind === "user") {
        return typeof (turn as { text?: unknown }).text === "string";
      }
      if (kind === "result") {
        const result = (turn as { result?: unknown }).result;
        return typeof result === "object" && result !== null;
      }
      if (kind === "error") {
        return typeof (turn as { text?: unknown }).text === "string";
      }
      return false;
    });
  } catch {
    return [];
  }
}

function buildConversationHistory(turns: Turn[]): ConversationHistoryTurn[] {
  const history: ConversationHistoryTurn[] = [];
  let pendingQuestion: string | null = null;
  for (const turn of turns) {
    if (turn.kind === "user") {
      pendingQuestion = turn.text;
      continue;
    }
    if (turn.kind === "result" && pendingQuestion) {
      history.push({
        question: pendingQuestion,
        sql: turn.result.sql,
        nl_summary: turn.result.nl_summary,
      });
      pendingQuestion = null;
      continue;
    }
    if (turn.kind === "error") {
      pendingQuestion = null;
    }
  }
  return history.slice(-12);
}

export default function QueryChatPage() {
  const [question, setQuestion] = useState("");
  const [turns, setTurns] = useState<Turn[]>(loadStoredTurns);
  const [busy, setBusy] = useState(false);
  const [includeExplanation, setIncludeExplanation] = useState(true);
  const [listening, setListening] = useState(false);

  useEffect(() => {
    window.localStorage.setItem(CHAT_HISTORY_STORAGE_KEY, JSON.stringify(turns));
  }, [turns]);

  async function submit() {
    const text = question.trim();
    if (!text) return;
    const conversationHistory = buildConversationHistory(turns);
    setQuestion("");
    setBusy(true);
    setTurns((previous) => [...previous, { kind: "user", text }]);
    try {
      const result = await api.query(text, conversationHistory, includeExplanation);
      setTurns((previous) => [...previous, { kind: "result", result }]);
    } catch (caught) {
      if (caught instanceof ApiError) {
        const detail = caught.detail as { ambiguities?: Extract<Turn, { kind: "error" }>["ambiguities"] } | undefined;
        setTurns((previous) => [
          ...previous,
          {
            kind: "error",
            text: caught.status === 409 ? "That question is ambiguous. Please clarify:" : caught.message,
            ambiguities: detail?.ambiguities,
          },
        ]);
      } else {
        setTurns((previous) => [...previous, { kind: "error", text: "Unable to reach the API." }]);
      }
    } finally {
      setBusy(false);
    }
  }

  function startVoiceInput() {
    if (typeof window === "undefined") {
      return;
    }
    type RecognitionCtor = new () => {
      lang: string;
      continuous: boolean;
      interimResults: boolean;
      maxAlternatives: number;
      start: () => void;
      stop: () => void;
      onresult: ((event: {
        resultIndex: number;
        results: ArrayLike<ArrayLike<{ transcript: string }> & { isFinal?: boolean }>;
      }) => void) | null;
      onerror: ((event: { error?: string }) => void) | null;
      onend: (() => void) | null;
    };
    const Ctor = (window as unknown as { SpeechRecognition?: RecognitionCtor; webkitSpeechRecognition?: RecognitionCtor }).SpeechRecognition
      ?? (window as unknown as { webkitSpeechRecognition?: RecognitionCtor }).webkitSpeechRecognition;
    if (!Ctor) {
      setTurns((previous) => [...previous, { kind: "error", text: "Voice input is not supported in this browser." }]);
      return;
    }
    const recognition = new Ctor();
    recognition.lang = "en-US";
    recognition.continuous = false;
    recognition.interimResults = false;
    recognition.maxAlternatives = 1;
    recognition.onresult = (event) => {
      const chunks: string[] = [];
      for (let index = event.resultIndex; index < event.results.length; index += 1) {
        const result = event.results[index];
        if (!result?.isFinal) {
          continue;
        }
        const transcript = result[0]?.transcript?.trim();
        if (transcript) {
          chunks.push(transcript);
        }
      }
      if (!chunks.length) {
        return;
      }
      const transcript = chunks.join(" ");
      setQuestion((previous) => (previous ? `${previous} ${transcript}` : transcript));
    };
    recognition.onerror = (event) => {
      const reason = event.error ? ` (${event.error})` : "";
      setTurns((previous) => [...previous, { kind: "error", text: `Voice input failed${reason}.` }]);
    };
    recognition.onend = () => setListening(false);
    setListening(true);
    recognition.start();
  }

  return (
    <div>
      <h2>Query Chat</h2>
      <p className="muted">Ask a question about your data in plain English.</p>

      {turns.map((turn, index) => {
        if (turn.kind === "user") {
          return (
            <div key={index} className="chat-bubble chat-user">
              {turn.text}
            </div>
          );
        }
        if (turn.kind === "error") {
          return (
            <div key={index} className="chat-bubble chat-assistant">
              <div className="error">{turn.text}</div>
              {turn.ambiguities?.map((ambiguity) => (
                <div key={ambiguity.term} className="muted">
                  &quot;{ambiguity.term}&quot; could mean:{" "}
                  {ambiguity.candidates.map((candidate) => `${candidate.table_name}.${candidate.column_name}`).join(", ")}
                </div>
              ))}
            </div>
          );
        }
        const { result } = turn;
        return (
          <div key={index} className="chat-bubble chat-assistant">
            <p>{result.nl_summary}</p>
            <SimpleChart columns={result.columns} rows={result.rows} />
            <SortableTable columns={result.columns} rows={result.rows} />
            <details>
              <summary className="muted">
                SQL, explanation &amp; stats ({result.row_count} rows, {result.latency_ms} ms{result.cache_hit ? ", cache hit" : ""})
              </summary>
              <pre>{result.sql}</pre>
              {result.transpiled_sql && (
                <>
                  <p className="muted">Transpiled to {result.transpile_dialect}:</p>
                  <pre>{result.transpiled_sql}</pre>
                </>
              )}
              {result.explanation && <p className="muted">{result.explanation}</p>}
              <span className="badge">{result.query_type}</span>
            </details>
          </div>
        );
      })}

      <div className="card">
        <label htmlFor="question">Your question</label>
        <label style={{ display: "block", marginBottom: 8 }}>
          <input
            type="checkbox"
            checked={includeExplanation}
            onChange={(event) => setIncludeExplanation(event.target.checked)}
            style={{ marginRight: 8 }}
          />
          Include SQL explanation in response
        </label>
        <textarea
          id="question"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              void submit();
            }
          }}
          placeholder="e.g. What are the top 10 products by gross revenue?"
        />
        <div className="row" style={{ marginTop: 8 }}>
          <button type="button" disabled={busy || listening} onClick={startVoiceInput}>
            {listening ? "Listening..." : "🎤 Voice"}
          </button>
          <button disabled={busy || !question.trim()} onClick={() => void submit()}>
            {busy ? "Running..." : "Ask"}
          </button>
          <button
            type="button"
            onClick={() => setTurns([])}
            disabled={busy || turns.length === 0}
            style={{ marginLeft: 8 }}
          >
            Clear chat
          </button>
        </div>
      </div>
    </div>
  );
}
