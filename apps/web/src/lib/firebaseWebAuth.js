import { initializeApp, getApps, getApp } from "firebase/app";
import { getAuth, getRedirectResult, GoogleAuthProvider, signInWithRedirect } from "firebase/auth";

/**
 * Optional: set in `apps/web/.env.development` so the Google button appears even if the API
 * does not echo Firebase web config yet (token exchange still requires API service account).
 */
export function viteFirebaseWebConfig() {
  const apiKey = import.meta.env.VITE_FIREBASE_API_KEY;
  const projectId = import.meta.env.VITE_FIREBASE_PROJECT_ID;
  const authDomain = import.meta.env.VITE_FIREBASE_AUTH_DOMAIN;
  const appId = import.meta.env.VITE_FIREBASE_APP_ID;
  if (!apiKey || !projectId || !authDomain || !appId) return null;
  return {
    api_key: String(apiKey),
    auth_domain: String(authDomain),
    project_id: String(projectId),
    app_id: String(appId),
  };
}

/** @type {import("firebase/auth").Auth | null} */
let _auth = null;

/**
 * Initialize Firebase for the browser (idempotent). `config` matches GET /v1/auth/config `data.firebase`.
 * @param {{ api_key: string, auth_domain: string, project_id: string, app_id: string }} config
 * @returns {import("firebase/auth").Auth | null}
 */
export function initFirebaseWeb(config) {
  if (!config?.api_key || !config?.project_id) return null;
  if (getApps().length === 0) {
    initializeApp({
      apiKey: config.api_key,
      authDomain: config.auth_domain,
      projectId: config.project_id,
      appId: config.app_id,
    });
  }
  _auth = getAuth(getApp());
  return _auth;
}

/**
 * Start Google OAuth via full-page redirect (avoids popup + Cross-Origin-Opener-Policy issues on
 * production sites). The page navigates to Google; on return, call {@link completeGoogleRedirectIdToken}.
 */
export async function startGoogleRedirectSignIn() {
  if (!_auth) throw new Error("Firebase Auth is not initialized");
  const provider = new GoogleAuthProvider();
  provider.setCustomParameters({ prompt: "select_account" });
  await signInWithRedirect(_auth, provider);
}

/**
 * After redirect back to this app, returns an ID token if the user completed Google sign-in.
 * Safe to call on every load; returns `null` when there was no redirect pending.
 * @returns {Promise<string | null>}
 */
export async function completeGoogleRedirectIdToken() {
  if (!_auth) return null;
  const result = await getRedirectResult(_auth);
  if (!result?.user) return null;
  return result.user.getIdToken();
}

/**
 * Human-readable text for common Firebase Auth failures (console setup issues).
 * @param {unknown} err
 * @returns {string}
 */
export function describeFirebaseAuthError(err) {
  const code = err && typeof err === "object" && "code" in err ? String(err.code) : "";
  const msg = err && typeof err === "object" && "message" in err ? String(err.message) : String(err ?? "");
  const setup =
    "In Firebase Console open Authentication → Get started (if you have not), then Sign-in method → enable Google. " +
    "Under Authentication → Settings → Authorized domains, ensure `localhost` is listed for local dev.";
  if (code === "auth/configuration-not-found" || /configuration-not-found/i.test(msg)) {
    return `Firebase Authentication is not enabled for this project (${code || "configuration-not-found"}). ${setup}`;
  }
  if (code === "auth/unauthorized-domain" || /unauthorized-domain/i.test(msg)) {
    return `This domain is not allowed for Firebase sign-in. In Firebase Console → Authentication → Settings → Authorized domains, add your production host (e.g. directely.com). ${setup}`;
  }
  return msg || "Google sign-in failed.";
}
