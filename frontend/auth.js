// Cognito Hosted UI, Authorization Code + PKCE -- a public client with no
// secret, since this all runs in the browser (infra/envs/dev/cognito.tf).
// The ID token (not the access token) is sent to the API: Cognito access
// tokens carry `client_id`, not `aud`, so the HTTP API JWT authorizer's
// audience check only works against the ID token -- which conveniently also
// carries the `email` claim the UI wants to display.
const Auth = (() => {
  const ID_TOKEN_KEY = "montage_id_token";
  const EMAIL_KEY = "montage_email";
  const VERIFIER_KEY = "montage_pkce_verifier";

  function redirectUri() {
    return new URL("callback.html", window.location.href).toString();
  }

  function randomString(length) {
    const bytes = new Uint8Array(length);
    crypto.getRandomValues(bytes);
    return Array.from(bytes, (b) => "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"[b % 66]).join("");
  }

  function base64UrlEncode(buffer) {
    let str = "";
    const bytes = new Uint8Array(buffer);
    for (const byte of bytes) str += String.fromCharCode(byte);
    return btoa(str).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }

  async function codeChallenge(verifier) {
    const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
    return base64UrlEncode(digest);
  }

  async function signIn() {
    const verifier = randomString(64);
    sessionStorage.setItem(VERIFIER_KEY, verifier);
    const challenge = await codeChallenge(verifier);

    const url = new URL("/oauth2/authorize", window.MONTAGE_CONFIG.cognitoDomain);
    url.searchParams.set("response_type", "code");
    url.searchParams.set("client_id", window.MONTAGE_CONFIG.cognitoClientId);
    url.searchParams.set("redirect_uri", redirectUri());
    url.searchParams.set("scope", "openid email");
    url.searchParams.set("code_challenge_method", "S256");
    url.searchParams.set("code_challenge", challenge);
    window.location.assign(url.toString());
  }

  async function handleCallback() {
    const params = new URLSearchParams(window.location.search);
    const code = params.get("code");
    if (!code) throw new Error("no authorization code in the redirect");

    const verifier = sessionStorage.getItem(VERIFIER_KEY);
    if (!verifier) throw new Error("missing PKCE verifier -- sign in again");
    sessionStorage.removeItem(VERIFIER_KEY);

    const body = new URLSearchParams({
      grant_type: "authorization_code",
      client_id: window.MONTAGE_CONFIG.cognitoClientId,
      code,
      redirect_uri: redirectUri(),
      code_verifier: verifier,
    });

    const resp = await fetch(new URL("/oauth2/token", window.MONTAGE_CONFIG.cognitoDomain), {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body,
    });
    if (!resp.ok) throw new Error(`token exchange failed (${resp.status})`);
    const tokens = await resp.json();

    localStorage.setItem(ID_TOKEN_KEY, tokens.id_token);
    localStorage.setItem(EMAIL_KEY, decodeIdTokenEmail(tokens.id_token) || "");
  }

  function decodeIdTokenEmail(idToken) {
    try {
      const payload = JSON.parse(atob(idToken.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")));
      return payload.email;
    } catch {
      return null;
    }
  }

  function idToken() {
    return localStorage.getItem(ID_TOKEN_KEY);
  }

  function email() {
    return localStorage.getItem(EMAIL_KEY);
  }

  function isSignedIn() {
    return Boolean(idToken());
  }

  function signOut() {
    localStorage.removeItem(ID_TOKEN_KEY);
    localStorage.removeItem(EMAIL_KEY);
    const url = new URL("/logout", window.MONTAGE_CONFIG.cognitoDomain);
    url.searchParams.set("client_id", window.MONTAGE_CONFIG.cognitoClientId);
    url.searchParams.set("logout_uri", new URL("index.html", window.location.href).toString());
    window.location.assign(url.toString());
  }

  return { signIn, handleCallback, idToken, email, isSignedIn, signOut };
})();
