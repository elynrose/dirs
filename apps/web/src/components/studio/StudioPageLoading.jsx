/** Lightweight fallback while a lazy studio page chunk loads. */
export function StudioPageLoading({ label = "Loading page…" }) {
  return (
    <section className="panel" style={{ padding: "2rem 1.5rem" }} aria-busy="true">
      <p className="subtle">{label}</p>
    </section>
  );
}
