"use client";

export default function Error({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <div className="card" style={{ maxWidth: 520, margin: "3rem auto", textAlign: "center" }}>
      <div className="label">Can&apos;t reach the close API</div>
      <p style={{ marginTop: ".75rem" }}>
        The dashboard couldn&apos;t load data. The most likely cause is that
        the FastAPI server isn&apos;t running yet.
      </p>
      <p style={{ color: "var(--muted)" }}>
        Start it with <code>uvicorn nostro.api.main:app --port 8000</code> and
        try again.
      </p>
      <button
        onClick={() => reset()}
        style={{
          marginTop: ".5rem",
          padding: ".5rem 1rem",
          borderRadius: "8px",
          border: "1px solid var(--line)",
          background: "var(--panel)",
          color: "var(--fg)",
          cursor: "pointer",
        }}
      >
        Retry
      </button>
    </div>
  );
}
