import { useEffect, useRef, useState } from "react";

import {
  api,
  type RuntimeExecRun,
  type RuntimeSessionState,
  type WorkingCopySummary,
} from "../api";

export interface RuntimeStreamFrame {
  key: string;
  type: "runtime.state" | "runtime.output";
  payload: Record<string, unknown>;
}

const TERMINAL_EXEC_STATES = new Set(["persisted", "failed", "cancelled"]);

async function waitForRuntime(
  runtimeId: string,
  isActive: () => boolean,
): Promise<RuntimeSessionState> {
  for (let attempt = 0; attempt < 180; attempt += 1) {
    if (!isActive()) throw new Error("session_changed");
    const runtime = await api.getRuntime(runtimeId);
    if (runtime.state === "ready") return runtime;
    if (runtime.state === "failed" || runtime.state === "closed") {
      throw new Error(runtime.termination_reason ?? runtime.state);
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error("runtime_open_timeout");
}

async function waitForExec(
  runtimeId: string,
  execId: string,
  isActive: () => boolean,
): Promise<RuntimeExecRun> {
  for (let attempt = 0; attempt < 1800; attempt += 1) {
    if (!isActive()) throw new Error("session_changed");
    const exec = await api.getRuntimeExec(runtimeId, execId);
    if (TERMINAL_EXEC_STATES.has(exec.state)) return exec;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error("runtime_exec_timeout");
}

export function RunPanel({
  projectId,
  sessionId,
  csrf,
  workingCopy,
  frames,
  onWorkingCopy,
}: {
  projectId: string;
  sessionId: string;
  csrf: string | null;
  workingCopy: WorkingCopySummary | null;
  frames: RuntimeStreamFrame[];
  onWorkingCopy: (workingCopy: WorkingCopySummary | null) => void;
}) {
  const [command, setCommand] = useState("pytest -q");
  const [runtime, setRuntime] = useState<RuntimeSessionState | null>(
    workingCopy?.runtime ?? null,
  );
  const [exec, setExec] = useState<RuntimeExecRun | null>(
    workingCopy?.last_exec ?? null,
  );
  const [stdout, setStdout] = useState("");
  const [stderr, setStderr] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const processed = useRef(new Set<string>());
  const stdoutRef = useRef("");
  const stderrRef = useRef("");
  const generation = useRef(0);

  useEffect(() => {
    generation.current += 1;
    processed.current.clear();
    stdoutRef.current = "";
    stderrRef.current = "";
    setRuntime(null);
    setExec(null);
    setStdout("");
    setStderr("");
    setBusy(false);
    setError(null);
  }, [sessionId]);

  useEffect(() => {
    setRuntime(workingCopy?.runtime ?? null);
    setExec(workingCopy?.last_exec ?? null);
  }, [workingCopy]);

  useEffect(() => {
    for (const frame of frames) {
      if (processed.current.has(frame.key)) continue;
      processed.current.add(frame.key);
      const runtimeId = String(frame.payload.runtime_session_id ?? "");
      if (!runtime || runtimeId !== runtime.id) continue;
      if (frame.type === "runtime.output") {
        const execId = String(frame.payload.exec_run_id ?? "");
        if (!exec || execId !== exec.id) continue;
        const delta = String(frame.payload.delta ?? "");
        if (frame.payload.stream === "stderr") {
          setStderr((value) => {
            const next = (value + delta).slice(-100_000);
            stderrRef.current = next;
            return next;
          });
        } else {
          setStdout((value) => {
            const next = (value + delta).slice(-100_000);
            stdoutRef.current = next;
            return next;
          });
        }
      }
    }
  }, [frames, runtime, exec]);

  const refreshWorkingCopy = async (runGeneration: number) => {
    const next = await api.getWorkingCopy(sessionId);
    if (generation.current === runGeneration) onWorkingCopy(next);
  };

  const ensureRuntime = async (runGeneration: number) => {
    if (!csrf) throw new Error("missing_csrf");
    const opened = await api.openRuntime(csrf, projectId, sessionId);
    if (generation.current !== runGeneration) throw new Error("session_changed");
    setRuntime(opened);
    return opened.state === "ready"
      ? opened
      : await waitForRuntime(
          opened.id,
          () => generation.current === runGeneration,
        );
  };

  const run = async () => {
    if (!csrf || !command.trim() || busy) return;
    if (workingCopy?.state === "conflicted") {
      setError("Rebase the conflicted changes before running more commands.");
      return;
    }
    const runGeneration = generation.current;
    setBusy(true);
    setError(null);
    setStdout("");
    setStderr("");
    stdoutRef.current = "";
    stderrRef.current = "";
    try {
      const ready = await ensureRuntime(runGeneration);
      if (generation.current !== runGeneration) return;
      setRuntime(ready);
      const queued = await api.execRuntime(csrf, ready.id, command.trim());
      if (generation.current !== runGeneration) return;
      setExec(queued);
      const settled = await waitForExec(
        ready.id,
        queued.id,
        () => generation.current === runGeneration,
      );
      if (generation.current !== runGeneration) return;
      setExec(settled);
      if (!stdoutRef.current && settled.stdout_head) {
        stdoutRef.current = settled.stdout_head;
        setStdout(settled.stdout_head);
      }
      if (!stderrRef.current && settled.stderr_tail) {
        stderrRef.current = settled.stderr_tail;
        setStderr(settled.stderr_tail);
      }
      setRuntime(await api.getRuntime(ready.id));
      await refreshWorkingCopy(runGeneration);
    } catch (e) {
      if (generation.current === runGeneration) {
        setError(e instanceof Error ? e.message : "Run failed.");
      }
    } finally {
      if (generation.current === runGeneration) setBusy(false);
    }
  };

  const stop = async () => {
    if (!csrf || !runtime || !exec || TERMINAL_EXEC_STATES.has(exec.state)) {
      return;
    }
    const runGeneration = generation.current;
    try {
      await api.cancelRuntime(csrf, runtime.id);
      if (generation.current !== runGeneration) return;
      setError(null);
      const settled = await waitForExec(
        runtime.id,
        exec.id,
        () => generation.current === runGeneration,
      );
      if (generation.current !== runGeneration) return;
      setExec(settled);
      await refreshWorkingCopy(runGeneration);
    } catch {
      if (generation.current === runGeneration) {
        setError("Could not stop this run.");
      }
    }
  };

  const close = async () => {
    if (!csrf || !runtime || busy) return;
    const runGeneration = generation.current;
    setBusy(true);
    try {
      const closing = await api.closeRuntime(csrf, runtime.id);
      if (generation.current !== runGeneration) return;
      setRuntime(closing);
      for (let attempt = 0; attempt < 180; attempt += 1) {
        if (generation.current !== runGeneration) return;
        const current = await api.getRuntime(runtime.id);
        setRuntime(current);
        if (current.state === "closed" || current.state === "failed") break;
        await new Promise((resolve) => setTimeout(resolve, 500));
      }
      await refreshWorkingCopy(runGeneration);
    } catch {
      if (generation.current === runGeneration) {
        setError("Could not close the runtime.");
      }
    } finally {
      if (generation.current === runGeneration) setBusy(false);
    }
  };

  const running = exec?.state === "queued" || exec?.state === "running";

  return (
    <section className="run-panel">
      <header>
        <div>
          <strong>Run</strong>
          <span>
            {runtime
              ? `${runtime.state}${exec ? ` · ${exec.state}` : ""}`
              : "Runtime opens on first command"}
          </span>
        </div>
        {runtime && runtime.state !== "closed" && (
          <button
            className="btn btn-quiet btn-small"
            onClick={() => void close()}
            disabled={busy || running}
          >
            Close
          </button>
        )}
      </header>
      <div className="run-command">
        <input
          value={command}
          aria-label="Project command"
          placeholder="pytest -q"
          onChange={(event) => setCommand(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") void run();
          }}
        />
        {running ? (
          <button className="btn btn-danger" onClick={() => void stop()}>
            Stop
          </button>
        ) : (
          <button
            className="btn btn-primary"
            onClick={() => void run()}
            disabled={
              busy ||
              !command.trim() ||
              workingCopy?.state === "conflicted"
            }
          >
            {busy ? "Running…" : "Run"}
          </button>
        )}
      </div>
      <div className="run-output" aria-live="polite" aria-label="Runtime output">
        {!stdout && !stderr && (
          <span className="muted">Command output will stream here.</span>
        )}
        {stdout && <pre>{stdout}</pre>}
        {stderr && <pre className="stderr">{stderr}</pre>}
      </div>
      {exec && (
        <footer>
          <span>exit {exec.exit_code ?? "—"}</span>
          <span>{exec.termination_reason ?? exec.state}</span>
          {exec.output_truncated && <span>output truncated</span>}
        </footer>
      )}
      {error && <div className="auth-error small">{error}</div>}
    </section>
  );
}
