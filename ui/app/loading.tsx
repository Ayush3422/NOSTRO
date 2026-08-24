export default function Loading() {
  return (
    <div className="card" style={{ maxWidth: 480, margin: "3rem auto", textAlign: "center" }}>
      <div className="label">Running the close</div>
      <p style={{ marginTop: ".75rem", marginBottom: 0 }}>
        Matching and scoring roughly 5,000 rows across bank, Razorpay, and ERP
        sources. This can take a few seconds on the first request — the page
        will render as soon as it finishes.
      </p>
    </div>
  );
}
