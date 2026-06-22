/** Mirror backend OAuth redirect URI selection for Settings UI hints. */

const CALLBACK_PATH = "/v1/integrations/youtube/oauth-callback";

function stripBase(url) {
  return String(url || "")
    .trim()
    .replace(/\/$/, "");
}

function isLoopbackHost(host) {
  const h = String(host || "").toLowerCase();
  return h === "localhost" || h === "127.0.0.1" || h === "::1" || h.startsWith("127.");
}

export function oauthRedirectUriForBase(base) {
  const b = stripBase(base);
  return b ? `${b}${CALLBACK_PATH}` : null;
}

/** Pick active redirect URI from workspace config + browser host (auto mode). */
export function activeOAuthRedirectUri(appConfig, browserHost) {
  const local = stripBase(appConfig?.local_api_base_url);
  const pub = stripBase(appConfig?.public_api_base_url);
  const mode = String(appConfig?.oauth_redirect_base || "auto").toLowerCase();

  if (mode === "local") return oauthRedirectUriForBase(local || pub);
  if (mode === "public") return oauthRedirectUriForBase(pub || local);
  if (mode === "request") return null;

  if (isLoopbackHost(browserHost) && local) return oauthRedirectUriForBase(local);
  if (pub) return oauthRedirectUriForBase(pub);
  if (local) return oauthRedirectUriForBase(local);
  return null;
}
