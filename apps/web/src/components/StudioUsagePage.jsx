export function StudioUsagePage({
  usageSummary,
  usageErr,
  usageLoading,
  usageDays,
  setUsageDays,
  loadUsageSummary,
}) {
  return (
    <section className="panel usage-page">
      <header className="usage-page-header">
        <div>
          <h2>Usage</h2>
          <p className="subtle">
            LLM token totals by model plus media/TTS usage (image_gen, video_gen, narration, etc.) for this workspace. Costs are rough
            estimates from built-in price hints and credits — verify against your provider invoices.
          </p>
          {usageErr ? <p className="err usage-page-error">{usageErr}</p> : null}
        </div>
        <div className="usage-page-toolbar">
          <label htmlFor="usage-range" className="usage-range-label">
            Period
          </label>
          <select
            id="usage-range"
            value={String(usageDays)}
            disabled={usageLoading}
            onChange={(e) => setUsageDays(Number(e.target.value))}
          >
            <option value="7">Last 7 days</option>
            <option value="30">Last 30 days</option>
            <option value="90">Last 90 days</option>
          </select>
          <button type="button" className="secondary" disabled={usageLoading} onClick={() => loadUsageSummary(usageDays)}>
            Refresh
          </button>
        </div>
      </header>

      {usageSummary ? (
        <>
          <div className="usage-totals">
            <div className="usage-total-card">
              <span className="usage-total-label">Total tokens</span>
              <strong>{(usageSummary.totals?.total_tokens ?? 0).toLocaleString()}</strong>
              <span className="subtle">
                in {(usageSummary.totals?.prompt_tokens ?? 0).toLocaleString()} · out{" "}
                {(usageSummary.totals?.completion_tokens ?? 0).toLocaleString()}
              </span>
            </div>
            <div className="usage-total-card">
              <span className="usage-total-label">Est. cost (USD)</span>
              <strong>
                {new Intl.NumberFormat("en-US", {
                  style: "currency",
                  currency: "USD",
                  minimumFractionDigits: 2,
                  maximumFractionDigits: 4,
                }).format(
                  Number(
                    usageSummary.totals?.estimated_cost_usd_including_media ??
                      usageSummary.totals?.estimated_cost_usd ??
                      0,
                  ),
                )}
              </strong>
              <span className="subtle">
                {usageSummary.totals?.llm_calls ?? 0} LLM calls · LLM token subtotal{" "}
                {new Intl.NumberFormat("en-US", {
                  style: "currency",
                  currency: "USD",
                  minimumFractionDigits: 2,
                  maximumFractionDigits: 4,
                }).format(Number(usageSummary.totals?.estimated_cost_usd ?? 0))}
                ; headline adds media/TTS (credits ÷ 1000 as nominal USD).
              </span>
            </div>
            <div className="usage-total-card">
              <span className="usage-total-label">Directely credits</span>
              <strong>{Number(usageSummary.totals?.director_credits ?? 0).toLocaleString(undefined, { maximumFractionDigits: 2 })}</strong>
              <span className="subtle">
                LLM {Number(usageSummary.totals?.llm_credits ?? 0).toLocaleString(undefined, { maximumFractionDigits: 1 })} · all modalities in range
              </span>
            </div>
          </div>

          {usageSummary.models?.length ? (
            <div className="usage-chart-section">
              <h3 className="usage-section-title">Tokens by model</h3>
              <div className="usage-bar-chart" aria-label="Token usage by model">
                {(() => {
                  const rows = usageSummary.models;
                  const maxT = Math.max(...rows.map((m) => m.total_tokens || 0), 1);
                  return rows.map((m) => (
                    <div key={`${m.provider}:${m.model}`} className="usage-bar-row">
                      <div className="usage-bar-meta">
                        <span className="usage-bar-model">{m.model}</span>
                        <span className="usage-bar-provider">{m.provider}</span>
                      </div>
                      <div className="usage-bar-track">
                        <div
                          className="usage-bar-fill"
                          style={{ width: `${(100 * (m.total_tokens || 0)) / maxT}%` }}
                          title={`${(m.total_tokens || 0).toLocaleString()} tokens`}
                        />
                      </div>
                      <div className="usage-bar-count">{(m.total_tokens || 0).toLocaleString()}</div>
                    </div>
                  ));
                })()}
              </div>
            </div>
          ) : (
            <p className="subtle usage-empty">No LLM token records in this period yet. Run scripts, scene planning, or critics to populate usage.</p>
          )}

          {usageSummary.models?.length ? (
            <div className="usage-table-wrap">
              <h3 className="usage-section-title">Cost breakdown</h3>
              <table className="usage-table">
                <thead>
                  <tr>
                    <th>Model</th>
                    <th>Provider</th>
                    <th>Input tok</th>
                    <th>Output tok</th>
                    <th>Calls</th>
                    <th>Est. USD</th>
                    <th>Credits</th>
                  </tr>
                </thead>
                <tbody>
                  {usageSummary.models.map((m) => (
                    <tr key={`${m.provider}:${m.model}:row`}>
                      <td>{m.model}</td>
                      <td>{m.provider}</td>
                      <td>{(m.prompt_tokens ?? 0).toLocaleString()}</td>
                      <td>{(m.completion_tokens ?? 0).toLocaleString()}</td>
                      <td>{m.llm_calls ?? 0}</td>
                      <td>
                        {new Intl.NumberFormat("en-US", {
                          style: "currency",
                          currency: "USD",
                          minimumFractionDigits: 2,
                          maximumFractionDigits: 4,
                        }).format(Number(m.estimated_cost_usd ?? 0))}
                      </td>
                      <td>{Number(m.credits ?? 0).toLocaleString(undefined, { maximumFractionDigits: 2 })}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}

          {usageSummary.media_services?.length ? (
            <div className="usage-table-wrap">
              <h3 className="usage-section-title">Media &amp; TTS</h3>
              <p className="subtle">
                Rows grouped by service type (non-token usage): image/video generation, TTS characters, and other provider calls.
              </p>
              <table className="usage-table">
                <thead>
                  <tr>
                    <th>Service</th>
                    <th>Provider</th>
                    <th>Unit</th>
                    <th>Quantity</th>
                    <th>Calls</th>
                    <th>Est. USD</th>
                    <th>Credits</th>
                  </tr>
                </thead>
                <tbody>
                  {usageSummary.media_services.map((row) => (
                    <tr key={`${row.provider}:${row.service_type}:${row.unit_type}:row`}>
                      <td>{row.service_type}</td>
                      <td>{row.provider}</td>
                      <td>{row.unit_type || "—"}</td>
                      <td>{Number(row.units ?? 0).toLocaleString(undefined, { maximumFractionDigits: 2 })}</td>
                      <td>{row.calls ?? 0}</td>
                      <td>
                        {new Intl.NumberFormat("en-US", {
                          style: "currency",
                          currency: "USD",
                          minimumFractionDigits: 2,
                          maximumFractionDigits: 4,
                        }).format(Number(row.estimated_cost_usd ?? 0))}
                      </td>
                      <td>{Number(row.credits ?? 0).toLocaleString(undefined, { maximumFractionDigits: 2 })}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
        </>
      ) : !usageErr && usageLoading ? (
        <p className="subtle">Loading usage…</p>
      ) : null}
    </section>
  );
}
