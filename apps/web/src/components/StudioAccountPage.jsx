import { useCallback, useEffect, useState } from "react";
import { apiPath } from "../lib/api.js";
import { parseJson, apiErrorMessage, formatUserFacingError } from "../lib/apiHelpers.js";
import { api } from "../lib/api.js";

function entLabel(key, value) {
  if (key === "max_projects") {
    if (value == null) return "Unlimited";
    return String(value);
  }
  if (typeof value === "boolean") return value ? "Yes" : "No";
  return value == null ? "—" : String(value);
}

const ENT_ROWS = [
  { key: "chat_enabled", label: "Chat studio" },
  { key: "telegram_enabled", label: "Telegram integration" },
  { key: "max_projects", label: "Max projects" },
  { key: "full_through_automation_enabled", label: "Auto → full video (agent)" },
  { key: "hands_off_unattended_enabled", label: "Hands-off (unattended) runs" },
  { key: "subtitles_enabled", label: "Subtitle generation" },
];

function formatPeriodEnd(iso) {
  if (!iso || typeof iso !== "string") return null;
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
  } catch {
    return iso;
  }
}

function shortWorkspaceId(id) {
  const s = id == null ? "" : String(id);
  if (s.length <= 16) return s;
  return `${s.slice(0, 6)}…${s.slice(-4)}`;
}

const sectionStyle = { marginBottom: 28 };
const h3Style = { fontSize: "1.05rem", marginBottom: 10, marginTop: 0 };
const subStyle = { marginTop: -6, marginBottom: 12 };

/**
 * Profile, password, workspaces, subscription, entitlements, sign-out.
 */
export function StudioAccountPage({
  authMode,
  accountProfile,
  onRefreshProfile,
  onSignOut,
  showToast,
}) {
  const [checkoutBusy, setCheckoutBusy] = useState(false);
  const [portalBusy, setPortalBusy] = useState(false);
  const [plans, setPlans] = useState([]);
  const [plansErr, setPlansErr] = useState("");
  const [profileBusy, setProfileBusy] = useState(false);
  const [passwordBusy, setPasswordBusy] = useState(false);
  const [formEmail, setFormEmail] = useState("");
  const [formFullName, setFormFullName] = useState("");
  const [formCity, setFormCity] = useState("");
  const [formState, setFormState] = useState("");
  const [formCountry, setFormCountry] = useState("");
  const [formZip, setFormZip] = useState("");
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");

  useEffect(() => {
    const p = accountProfile;
    if (!p || p.auth_enabled === false) return;
    setFormEmail(p.email ?? "");
    setFormFullName(p.full_name ?? "");
    setFormCity(p.city ?? "");
    setFormState(p.state ?? "");
    setFormCountry(p.country ?? "");
    setFormZip(p.zip_code ?? "");
  }, [
    accountProfile?.user_id,
    accountProfile?.email,
    accountProfile?.full_name,
    accountProfile?.city,
    accountProfile?.state,
    accountProfile?.country,
    accountProfile?.zip_code,
    accountProfile?.auth_enabled,
  ]);

  const loadPlans = useCallback(async () => {
    setPlansErr("");
    try {
      const r = await api("/v1/billing/plans");
      const body = await parseJson(r);
      if (!r.ok) {
        setPlansErr(apiErrorMessage(body) || "Could not load plans");
        return;
      }
      setPlans(body.data?.plans || []);
    } catch (e) {
      setPlansErr(formatUserFacingError(e));
    }
  }, []);

  const openCustomerPortal = useCallback(async () => {
    setPortalBusy(true);
    try {
      const r = await api("/v1/billing/customer-portal", { method: "POST" });
      const body = await parseJson(r);
      if (!r.ok) {
        showToast?.(apiErrorMessage(body) || "Could not open billing portal", { type: "error", durationMs: 8000 });
        return;
      }
      const url = body.data?.url;
      if (url) window.location.href = url;
      else showToast?.("No portal URL returned", { type: "error" });
    } catch (e) {
      showToast?.(formatUserFacingError(e), { type: "error", durationMs: 8000 });
    } finally {
      setPortalBusy(false);
    }
  }, [showToast]);

  const startCheckout = useCallback(
    async (slug) => {
      setCheckoutBusy(true);
      try {
        const r = await api("/v1/billing/checkout-session", {
          method: "POST",
          body: JSON.stringify({ plan_slug: slug }),
        });
        const body = await parseJson(r);
        if (!r.ok) {
          showToast?.(apiErrorMessage(body) || "Checkout failed", { type: "error", durationMs: 8000 });
          return;
        }
        const url = body.data?.url;
        if (url) window.location.href = url;
        else showToast?.("No checkout URL returned", { type: "error" });
      } catch (e) {
        showToast?.(formatUserFacingError(e), { type: "error", durationMs: 8000 });
      } finally {
        setCheckoutBusy(false);
      }
    },
    [showToast],
  );

  const saveProfile = useCallback(async () => {
    setProfileBusy(true);
    try {
      const r = await api("/v1/auth/me", {
        method: "PATCH",
        body: JSON.stringify({
          email: formEmail.trim(),
          full_name: formFullName.trim(),
          city: formCity.trim(),
          state: formState.trim(),
          country: formCountry.trim(),
          zip_code: formZip.trim(),
        }),
      });
      const body = await parseJson(r);
      if (!r.ok) {
        showToast?.(apiErrorMessage(body) || "Could not save profile", { type: "error" });
        return;
      }
      showToast?.("Profile saved", { type: "success" });
      await onRefreshProfile?.();
    } catch (e) {
      showToast?.(formatUserFacingError(e), { type: "error" });
    } finally {
      setProfileBusy(false);
    }
  }, [formEmail, formFullName, formCity, formState, formCountry, formZip, onRefreshProfile, showToast]);

  const changePassword = useCallback(async () => {
    if (newPassword.length < 8) {
      showToast?.("New password must be at least 8 characters", { type: "error" });
      return;
    }
    if (newPassword !== confirmPassword) {
      showToast?.("New password and confirmation do not match", { type: "error" });
      return;
    }
    setPasswordBusy(true);
    try {
      const r = await api("/v1/auth/change-password", {
        method: "POST",
        body: JSON.stringify({
          current_password: currentPassword,
          new_password: newPassword,
        }),
      });
      const body = await parseJson(r);
      if (!r.ok) {
        showToast?.(apiErrorMessage(body) || "Could not change password", { type: "error" });
        return;
      }
      showToast?.("Password updated", { type: "success" });
      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
    } catch (e) {
      showToast?.(formatUserFacingError(e), { type: "error" });
    } finally {
      setPasswordBusy(false);
    }
  }, [currentPassword, newPassword, confirmPassword, showToast]);

  if (authMode !== "saas") {
    return (
      <div className="panel account-page" style={{ padding: 24, maxWidth: 720 }}>
        <h2 style={{ marginTop: 0 }}>Account</h2>
        <p className="subtle">
          This install runs in <strong>local / single-tenant</strong> mode: API login is off, so there is no personal
          Directely account or subscription UI here.
        </p>
        <ul className="subtle" style={{ lineHeight: 1.6 }}>
          <li>Workspace and projects use the default tenant from your API configuration.</li>
          <li>To use email login, Stripe billing, and per-user profiles, enable auth in the API (see env for{" "}
            <code>DIRECTOR_AUTH_ENABLED</code>).
          </li>
        </ul>
      </div>
    );
  }

  const displayName = (accountProfile?.full_name || "").trim() || accountProfile?.email || "—";
  const email = accountProfile?.email || "—";
  const billing = accountProfile?.billing || {};
  const daysLeftInPeriod =
    billing.days_remaining_in_period != null && billing.days_remaining_in_period !== ""
      ? Number(billing.days_remaining_in_period)
      : null;
  const ent = accountProfile?.entitlements || {};
  const tenants = Array.isArray(accountProfile?.tenants) ? accountProfile.tenants : [];
  const activeTid = accountProfile?.active_tenant_id;

  const subStatusLabel = String(billing.status || "none");

  return (
    <div className="panel account-page" style={{ padding: 24, maxWidth: 820 }}>
      <header
        style={{
          marginBottom: 24,
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
          gap: 16,
          flexWrap: "wrap",
        }}
      >
        <div style={{ flex: "1 1 280px", minWidth: 0 }}>
          <h2 style={{ marginTop: 0, marginBottom: 8 }}>Account</h2>
          <p style={{ margin: 0, fontSize: "1.1rem" }}>
            <strong>{displayName}</strong>
            {accountProfile?.user_id ? (
              <span className="subtle" style={{ fontWeight: 400, marginLeft: 8 }}>
                User id <code title={String(accountProfile.user_id)}>{accountProfile.user_id}</code>
              </span>
            ) : null}
            {activeTid ? (
              <span className="subtle" style={{ fontWeight: 400, marginLeft: 8 }}>
                Tenant id <code title={activeTid}>{activeTid}</code>
              </span>
            ) : null}
          </p>
          <p className="subtle" style={{ margin: "8px 0 0" }}>
            Signed in as <strong>{email}</strong>. Session controls stay in the header (refresh, sign out).
          </p>
          <div className="action-row" style={{ marginTop: 14, flexWrap: "wrap", gap: 8 }}>
            <button type="button" className="secondary" disabled={profileBusy} onClick={() => void onRefreshProfile?.()}>
              Refresh from server
            </button>
            <button type="button" onClick={() => onSignOut?.()}>
              Sign out
            </button>
          </div>
        </div>
        <aside
          style={{
            flex: "0 1 260px",
            padding: "10px 12px",
            borderRadius: 8,
            border: "1px solid var(--border, rgb(255 255 255 / 12%))",
            background: "var(--panel-elevated, rgb(255 255 255 / 5%))",
            fontSize: "0.88rem",
            textAlign: "right",
          }}
          aria-label="Subscription summary"
        >
          <div style={{ fontWeight: 600, marginBottom: 6 }}>Subscription</div>
          {subStatusLabel === "none" ? (
            <>
              <p className="subtle" style={{ margin: 0 }}>
                No active subscription on this workspace.
              </p>
              {billing.stripe_customer_id ? (
                <div style={{ marginTop: 12 }}>
                  <button
                    type="button"
                    className="secondary"
                    style={{ width: "100%" }}
                    disabled={portalBusy}
                    onClick={() => void openCustomerPortal()}
                  >
                    {portalBusy ? "Opening…" : "Billing portal (invoices & history)"}
                  </button>
                </div>
              ) : null}
            </>
          ) : (
            <>
              <p style={{ margin: "0 0 4px", lineHeight: 1.45 }}>
                <span className="subtle">Status:</span> <strong>{subStatusLabel}</strong>
              </p>
              {billing.plan_display_name ? (
                <p style={{ margin: "0 0 6px", lineHeight: 1.45 }}>
                  <span className="subtle">Plan:</span> <strong>{billing.plan_display_name}</strong>
                </p>
              ) : null}
              {daysLeftInPeriod != null && !Number.isNaN(daysLeftInPeriod) ? (
                <p style={{ margin: 0, fontSize: "0.95rem" }}>
                  <strong>{daysLeftInPeriod}</strong> day{daysLeftInPeriod === 1 ? "" : "s"} left in period
                </p>
              ) : null}
              {formatPeriodEnd(billing.current_period_end) ? (
                <p className="subtle" style={{ margin: "6px 0 0", fontSize: "0.8rem" }}>
                  Renews / ends {formatPeriodEnd(billing.current_period_end)}
                </p>
              ) : null}
              {billing.stripe_customer_id ? (
                <div style={{ marginTop: 12 }}>
                  <button
                    type="button"
                    className="secondary"
                    style={{ width: "100%" }}
                    disabled={portalBusy}
                    onClick={() => void openCustomerPortal()}
                  >
                    {portalBusy ? "Opening…" : "Manage or cancel"}
                  </button>
                </div>
              ) : null}
            </>
          )}
        </aside>
      </header>

      <section style={sectionStyle} className="account-section">
        <h3 style={h3Style}>Profile &amp; contact</h3>
        <p className="subtle" style={subStyle}>
          Name and address are stored on your user record (same fields as in the admin console). Changing email keeps
          your login address for the next sign-in.
        </p>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))",
            gap: 12,
            marginBottom: 12,
          }}
        >
          <label className="subtle" style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            Full name
            <input value={formFullName} onChange={(e) => setFormFullName(e.target.value)} autoComplete="name" />
          </label>
          <label className="subtle" style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            Email
            <input
              type="email"
              value={formEmail}
              onChange={(e) => setFormEmail(e.target.value)}
              autoComplete="email"
            />
          </label>
          <label className="subtle" style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            City
            <input value={formCity} onChange={(e) => setFormCity(e.target.value)} autoComplete="address-level2" />
          </label>
          <label className="subtle" style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            State / region
            <input value={formState} onChange={(e) => setFormState(e.target.value)} autoComplete="address-level1" />
          </label>
          <label className="subtle" style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            Country
            <input value={formCountry} onChange={(e) => setFormCountry(e.target.value)} autoComplete="country-name" />
          </label>
          <label className="subtle" style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            Zip / postal code
            <input value={formZip} onChange={(e) => setFormZip(e.target.value)} autoComplete="postal-code" />
          </label>
        </div>
        <button type="button" disabled={profileBusy} onClick={() => void saveProfile()}>
          {profileBusy ? "Saving…" : "Save profile"}
        </button>
      </section>

      <section style={sectionStyle} className="account-section">
        <h3 style={h3Style}>Security</h3>
        <p className="subtle" style={subStyle}>
          Use your current password to set a new one. If you use a password manager, update the saved entry after
          changing.
        </p>
        <div style={{ display: "flex", flexDirection: "column", gap: 10, maxWidth: 400 }}>
          <label className="subtle" style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            Current password
            <input
              type="password"
              value={currentPassword}
              onChange={(e) => setCurrentPassword(e.target.value)}
              autoComplete="current-password"
            />
          </label>
          <label className="subtle" style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            New password (8+ characters)
            <input
              type="password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              autoComplete="new-password"
            />
          </label>
          <label className="subtle" style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            Confirm new password
            <input
              type="password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              autoComplete="new-password"
            />
          </label>
          <button type="button" className="secondary" disabled={passwordBusy} onClick={() => void changePassword()}>
            {passwordBusy ? "Updating…" : "Change password"}
          </button>
        </div>
      </section>

      <section style={sectionStyle} className="account-section">
        <h3 style={h3Style}>Workspaces</h3>
        <p className="subtle" style={subStyle}>
          Tenants you belong to. The active workspace for API calls is set from the header selector (
          <code>X-Tenant-Id</code>
          ).
        </p>
        {tenants.length === 0 ? (
          <p className="subtle">No workspaces on this account.</p>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table className="usage-table" style={{ width: "100%", fontSize: "0.9rem" }}>
              <thead>
                <tr>
                  <th style={{ textAlign: "left" }}>Name</th>
                  <th style={{ textAlign: "left" }}>Your role</th>
                  <th style={{ textAlign: "left" }}>Workspace id</th>
                  <th style={{ textAlign: "left" }}>Active</th>
                </tr>
              </thead>
              <tbody>
                {tenants.map((t) => (
                  <tr key={t.id}>
                    <td>{t.name || "—"}</td>
                    <td>{t.role || "—"}</td>
                    <td className="mono" title={t.id}>
                      {shortWorkspaceId(t.id)}
                    </td>
                    <td>{t.id === activeTid ? "Yes" : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section style={sectionStyle} className="account-section">
        <h3 style={h3Style}>Subscription</h3>
        <p className="subtle" style={subStyle}>
          Status for the <strong>current</strong> workspace (see table above). Stripe Checkout needs{" "}
          <code>STRIPE_SECRET_KEY</code> and a price on the plan. Enable{" "}
          <strong>Customer portal</strong> in the Stripe Dashboard (Settings → Billing → Customer portal) so users can
          cancel or update payment methods.
        </p>
        {billing.stripe_customer_id ? (
          <div className="action-row" style={{ marginBottom: 14, flexWrap: "wrap", gap: 8 }}>
            <button type="button" disabled={portalBusy} onClick={() => void openCustomerPortal()}>
              {portalBusy ? "Opening…" : "Manage subscription & billing"}
            </button>
            <span className="subtle" style={{ fontSize: "0.88rem", alignSelf: "center" }}>
              Opens Stripe — cancel at period end, change card, or view invoices.
            </span>
          </div>
        ) : null}
        <p className="subtle" style={{ marginTop: -4, marginBottom: 10 }}>
          Status: <strong>{billing.status || "none"}</strong>
          {billing.plan_display_name ? (
            <>
              {" "}
              · Plan: <strong>{billing.plan_display_name}</strong>
            </>
          ) : null}
          {billing.plan_slug ? (
            <>
              {" "}
              · <code>{billing.plan_slug}</code>
            </>
          ) : null}
          {daysLeftInPeriod != null && !Number.isNaN(daysLeftInPeriod) ? (
            <>
              {" "}
              · <strong>{daysLeftInPeriod}</strong> day{daysLeftInPeriod === 1 ? "" : "s"} left in period
            </>
          ) : null}
          {formatPeriodEnd(billing.current_period_end) ? (
            <>
              {" "}
              · Renews / ends: <strong>{formatPeriodEnd(billing.current_period_end)}</strong>
            </>
          ) : null}
        </p>
        <div className="action-row" style={{ marginBottom: 14, flexWrap: "wrap", gap: 8 }}>
          <button type="button" className="secondary" onClick={() => void loadPlans()}>
            Load available plans
          </button>
        </div>
        {plansErr ? <p className="err">{plansErr}</p> : null}
        {plans.length ? (
          <ul style={{ listStyle: "none", padding: 0, margin: "0 0 16px" }}>
            {plans.map((pl) => (
              <li
                key={pl.slug}
                className="panel"
                style={{ padding: 12, marginBottom: 10, border: "1px solid var(--border, #333)" }}
              >
                <strong>{pl.display_name}</strong>{" "}
                <span className="subtle">
                  ({pl.billing_interval}) · slug: <code>{pl.slug}</code>
                </span>
                {pl.description ? <p className="subtle" style={{ margin: "8px 0" }}>{pl.description}</p> : null}
                <button
                  type="button"
                  disabled={checkoutBusy || !pl.stripe_price_configured}
                  title={
                    pl.stripe_price_configured
                      ? "Open Stripe Checkout"
                      : "Configure Stripe price id for this plan (env or database)"
                  }
                  onClick={() => void startCheckout(pl.slug)}
                >
                  {checkoutBusy ? "Redirecting…" : "Subscribe with Stripe"}
                </button>
              </li>
            ))}
          </ul>
        ) : null}
      </section>

      <section style={sectionStyle} className="account-section">
        <h3 style={h3Style}>Access on this workspace</h3>
        <p className="subtle" style={subStyle}>
          Effective entitlements for the current tenant (plan + any overrides).
        </p>
        <table className="usage-table" style={{ width: "100%", fontSize: "0.9rem" }}>
          <thead>
            <tr>
              <th style={{ textAlign: "left" }}>Feature</th>
              <th style={{ textAlign: "left" }}>Allowed</th>
            </tr>
          </thead>
          <tbody>
            {ENT_ROWS.map((row) => (
              <tr key={row.key}>
                <td>{row.label}</td>
                <td>{entLabel(row.key, ent[row.key])}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <p className="subtle" style={{ marginTop: 8, fontSize: "0.8rem" }}>
        Stripe webhook URL: <code>{apiPath("/v1/billing/stripe/webhook")}</code>
      </p>
    </div>
  );
}
