import { useRef, useState } from "react";
import { parseRun, type RunResult } from "../schema";

interface Props {
  label: string;
  onLoad: (run: RunResult, name: string) => void;
}

/** Load a run result from disk by picking or dropping a JSON file. */
export function FileDrop({ label, onLoad }: Props) {
  const input = useRef<HTMLInputElement>(null);
  const [error, setError] = useState<string | null>(null);

  async function handle(file: File | undefined) {
    if (!file) return;
    const parsed = parseRun(await file.text());
    if (parsed.ok) {
      setError(null);
      onLoad(parsed.run, file.name);
    } else {
      setError(parsed.error);
    }
  }

  return (
    <div
      className="drop"
      onDragOver={(e) => e.preventDefault()}
      onDrop={(e) => {
        e.preventDefault();
        void handle(e.dataTransfer.files[0]);
      }}
    >
      <button type="button" onClick={() => input.current?.click()}>
        {label}
      </button>
      <input
        ref={input}
        type="file"
        accept="application/json,.json"
        aria-label={label}
        hidden
        onChange={(e) => void handle(e.target.files?.[0])}
      />
      <span className="muted"> or drop a result.json here</span>
      {error && (
        <p role="alert" className="bad">
          {error}
        </p>
      )}
    </div>
  );
}
