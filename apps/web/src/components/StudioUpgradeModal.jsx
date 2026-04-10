import { useCallback, useEffect, useState } from "react";
import { api } from "../lib/api.js";
import { parseJson, apiErrorMessage, formatUserFacingError } from "../lib/apiHelpers.js";

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
 * Authenticated-only: list plans and open Stripe Checkout for the selected plan.
 */
export function StudioUpgradeModal({ open, onClose, showToast }) {
  const [plans, setPlans] = useState([]);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);
  const [checkoutSlug, setCheckoutSlug] = useState(null);

  const load = useCallback(async () => {
    setErr("");
    setLoading(true);
    try {
      const r = await api("/v1/billing/plans");
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
    if (!open) return;
    void load();
  }, [open, load]);

  const startCheckout = useCallback(
    async (slug) => {
      setCheckoutSlug(slug);
      try {
        const r = await api("/v1/billing/checkout-session", {
          method: "POST",
          body: JSON.stringify({ plan_slug: slug }),
        });
        const body = await parseJson(r);
        if (!r.ok) {
          showToast?.(apiErrorMessage(body) || "Could not start checkout", { type: "error", durationMs: 8000 });
          return;
        }
        const url = body.data?.url;
        if (url) window.location.href = url;
        else showToast?.("No checkout URL returned", { type: "error" });
      } catch (e) {
        showToast?.(formatUserFacingError(e), { type: "error", durationMs: 8000 });
      } finally {
        setCheckoutSlug(null);
      }
    },
    [showToast],
  );

  if (!open) return null;

  return (
    <div
      className="restart-automation-modal-backdrop"
      role="presentation"
      onClick={onClose}
    >
      <div
        className="panel studio-upgrade-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="studio-upgrade-title"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => {
          if (e.key === "Escape") onClose();
        }}
        style={{
          maxWidth: 640,
          width: "min(96vw, 640px)",
          maxHeight: "90vh",
          overflow: "auto",
          padding: 20,
        }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12, marginBottom: 12 }}>
          <h2 id="studio-upgrade-title" style={{ margin: 0, fontSize: "1.2rem" }}>
            Upgrade plan
          </h2>
          <button type="button" className="secondary" onClick={onClose} aria-label="Close">
            Close
          </button>
        </div>
        <p className="subtle" style={{ marginTop: 0, marginBottom: 16 }}>
          Choose a plan to continue to Stripe Checkout. After payment you&apos;ll return here and your workspace subscription updates automatically.
        </p>
        {loading ? <p className="subtle">Loading plans…</p> : null}
        {err ? <p className="err" style={{ marginBottom: 12 }}>{err}</p> : null}
        {!loading && !err && plans.length === 0 ? (
          <p className="subtle">No plans are available yet. Ask an admin to configure subscription plans and Stripe prices.</p>
        ) : null}
        <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
          {plans.map((pl) => (
            <li
              key={pl.slug}
              className="panel"
              style={{
                padding: 14,
                marginBottom: 12,
                border: "1px solid var(--border, rgb(255 255 255 / 12%))",
                borderRadius: 8,
              }}
            >
              <div style={{ display: "flex", flexWrap: "wrap", alignItems: "baseline", gap: 8, marginBottom: 6 }}>
                <strong style={{ fontSize: "1.05rem" }}>{pl.display_name}</strong>
                <span className="subtle">
                  {pl.billing_interval === "year" ? "Billed yearly" : "Billed monthly"} · <code>{pl.slug}</code>
                </span>
              </div>
              {pl.description ? (
                <p className="subtle" style={{ margin: "0 0 10px", lineHeight: 1.5, fontSize: "0.9rem" }}>
                  {pl.description}
                </p>
              ) : null}
              <div className="subtle" style={{ fontSize: "0.85rem", marginBottom: 10 }}>
                <strong style={{ color: "var(--fg, inherit)" }}>Includes</strong>
                <ul style={{ margin: "4px 0 0", paddingLeft: 18, lineHeight: 1.45 }}>
                  {FEATURE_LABELS.map(({ key, label }) => (
                    <li key={key}>
                      {label}: {formatEntValue(key, pl.entitlements?.[key])}
                    </li>
                  ))}
                </ul>
              </div>
              <button
                type="button"
                disabled={!pl.stripe_price_configured || checkoutSlug !== null}
                title={
                  pl.stripe_price_configured
                    ? "Continue to Stripe Checkout"
                    : "Stripe price not configured for this plan"
                }
                onClick={() => void startCheckout(pl.slug)}
              >
                {checkoutSlug === pl.slug
                  ? "Redirecting…"
                  : pl.stripe_price_configured
                    ? "Continue to Stripe"
                    : "Unavailable"}
              </button>
              {!pl.stripe_price_configured ? (
                <span className="subtle" style={{ fontSize: "0.82rem", marginLeft: 8 }}>
                  Configure a Stripe price for this plan in Admin or billing settings.
                </span>
              ) : null}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
