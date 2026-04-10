import { useCallback, useEffect, useState } from "react";
import { apiPath } from "../lib/api.js";
import { parseJson, apiErrorMessage, formatUserFacingError } from "../lib/apiHelpers.js";
import { PENDING_CHECKOUT_PLAN_KEY } from "../lib/constants.js";

const FEATURE_LABELS = [
  { key: "chat_enabled", label: "Chat studio" },
  { key: "telegram_enabled", label: "Telegram" },
  { key: "max_projects", label: "Max projects" },
  { key: "full_through_automation_enabled", label: "Full pipeline automation" },
  { key: "hands_off_unattended_enabled", label: "Hands-off runs" },
  { key: "subtitles_enabled", label: "Subtitles" },
  { key: "monthly_credits", label: "Monthly credits cap" },
  { key: "credits_enforce", label: "Credit budget enforced" },
];

function formatEntValue(key, value) {
  if (key === "max_projects") return value == null ? "Unlimited" : String(value);
  if (key === "monthly_credits") return value == null || value === "" ? "Unlimited" : String(value);
  if (typeof value === "boolean") return value ? "Yes" : "No";
  return value == null ? "—" : String(value);
}

/**
 * Public plan list (GET /v1/billing/plans). Choosing a plan stores the slug and sends the user to
 * sign-in; after login the app opens Stripe Checkout for that plan.
 * `embedded` — rendered inside the login shell glass card (no outer margin).
 */
export function StudioPricingPanel({ onBackToSignIn, embedded = false }) {
  const [plans, setPlans] = useState([]);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setErr("");
    setLoading(true);
    try {
      const r = await fetch(apiPath("/v1/billing/plans"));
      const body = await parseJson(r);
      if (!r.ok) {
        setErr(apiErrorMessage(body) || `Could not load plans (HTTP ${r.status})`);
        setPlans([]);
        return;
      }
      setPlans(Array.isArray(body.data?.plans) ? body.data.plans : []);
    } catch (e) {
      setErr(formatUserFacingError(e));
      setPlans([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const outerStyle = embedded
    ? { padding: 0 }
    : { maxWidth: 720, margin: "48px auto", padding: 24 };

  return (
    <div className={embedded ? undefined : "app-shell"} style={outerStyle}>
      <button type="button" className="secondary" style={{ marginBottom: 16 }} onClick={onBackToSignIn}>
        ← Back to sign in
      </button>
      <h1 style={{ fontSize: "1.35rem", marginBottom: 8 }}>Plans & pricing</h1>
      <p className="subtle" style={{ marginBottom: 20 }}>
        Browse and compare plans without an account. To subscribe, pick a plan below — you will sign in next, then we open
        Stripe Checkout for that plan. Subscriptions renew monthly until you cancel in the Stripe customer portal or from
        Account → Subscription.
      </p>
      {loading ? <p className="subtle">Loading plans…</p> : null}
      {err ? <p className="err" style={{ marginBottom: 12 }}>{err}</p> : null}
      {!loading && !err && plans.length === 0 ? (
        <p className="subtle">No public plans are configured yet.</p>
      ) : null}
      <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
        {plans.map((pl) => (
          <li
            key={pl.slug}
            className="panel"
            style={{
              padding: 16,
              marginBottom: 14,
              border: "1px solid var(--border, rgb(255 255 255 / 12%))",
              borderRadius: 8,
            }}
          >
            <div style={{ display: "flex", flexWrap: "wrap", alignItems: "baseline", gap: 8, marginBottom: 8 }}>
              <strong style={{ fontSize: "1.1rem" }}>{pl.display_name}</strong>
              <span className="subtle">
                {pl.billing_interval === "year" ? "Billed yearly" : "Billed monthly"} · <code>{pl.slug}</code>
              </span>
            </div>
            {pl.description ? (
              <p className="subtle" style={{ margin: "0 0 12px", lineHeight: 1.5 }}>
                {pl.description}
              </p>
            ) : null}
            <div className="subtle" style={{ fontSize: "0.88rem", marginBottom: 12 }}>
              <strong style={{ color: "var(--fg, inherit)" }}>Includes</strong>
              <ul style={{ margin: "6px 0 0", paddingLeft: 18, lineHeight: 1.5 }}>
                {FEATURE_LABELS.map(({ key, label }) => (
                  <li key={key}>
                    {label}: {formatEntValue(key, pl.entitlements?.[key])}
                  </li>
                ))}
              </ul>
            </div>
            <div className="action-row" style={{ flexWrap: "wrap", gap: 8, alignItems: "center" }}>
              <button
                type="button"
                disabled={!pl.stripe_price_configured}
                title={
                  pl.stripe_price_configured
                    ? "Sign in, then continue to Stripe Checkout for this plan"
                    : "Configure a Stripe price for this plan first"
                }
                onClick={() => {
                  if (!pl.stripe_price_configured) return;
                  try {
                    localStorage.setItem(PENDING_CHECKOUT_PLAN_KEY, pl.slug);
                  } catch {
                    /* ignore */
                  }
                  onBackToSignIn?.();
                }}
              >
                {pl.stripe_price_configured ? "Sign in & pay with Stripe" : "Unavailable — no Stripe price"}
              </button>
              {!pl.stripe_price_configured ? (
                <span className="subtle" style={{ fontSize: "0.85rem" }}>
                  Stripe price not configured for this plan — check Admin or server billing settings.
                </span>
              ) : null}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
