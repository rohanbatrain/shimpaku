# Guest access for the live demo — research (retrieved 2026-10-24)

All sources retrieved 2026-10-24. Quotes are verbatim from the source fetched that day.

## 1. Sign In with LinkedIn using OpenID Connect

Sources:
- S1 https://learn.microsoft.com/en-us/linkedin/consumer/integrations/self-serve/sign-in-with-linkedin-v2 (page updated 2024-08-08)
- S2 https://learn.microsoft.com/en-us/linkedin/shared/authentication/getting-access (updated 2026-06-03)
- S3 https://learn.microsoft.com/en-us/linkedin/shared/authentication/authorization-code-flow (updated 2026-05-15)
- S4 https://www.linkedin.com/oauth/.well-known/openid-configuration (as reproduced in S1)
- S5 https://www.linkedin.com/legal/l/api-terms-of-use

**Self-serve:** yes. S2: "Open Permissions are the only permissions that are available to all developers without special approval." Listed under Open Permissions: "Sign in with LinkedIn using OpenID Connect | profile" and "| email"; "may be added via self-service through the LinkedIn Developer Portal, under the Products tab ... LinkedIn products can be enabled after creating a new application."

**Company Page required?** Not stated in any fetched doc. The developer portal's app-creation form is widely reported to require associating a LinkedIn Page, but this was NOT verified at source (no account created, per brief). Treat as an open item: check the form before committing.

**Scopes (S1):** `openid` ("Required to indicate the application wants to use OIDC"), `profile` ("member's lite profile including their id, name, and profile picture"), `email` ("member's email address").

**Claims (S4 claims_supported):** iss, aud, iat, exp, sub, name, given_name, family_name, picture, email, email_verified, locale. S1: "The 'email' and 'email_verified' fields are optional and may not be included in all responses."
`subject_types_supported: ["pairwise"]` — `sub` is per-app, not a global LinkedIn member ID, and is not a profile URL.

**Endpoints (S4):**
- issuer `https://www.linkedin.com`
- authorization `https://www.linkedin.com/oauth/v2/authorization`
- token `https://www.linkedin.com/oauth/v2/accessToken`
- userinfo `https://api.linkedin.com/v2/userinfo`
- JWKS `https://www.linkedin.com/oauth/openid/jwks`
- discovery `https://www.linkedin.com/oauth/.well-known/openid-configuration`
- `response_types_supported: ["code"]`, signing `RS256`.

**Identity caveat (S1):** "Keep in mind Sign In with LinkedIn using OpenID Connect does not verify user identities and should not be marketed as such."

**Token lifetimes (S3):** "the authorization code has a 30-minute lifespan"; "Currently, all access tokens are issued with a 60-day lifespan." "Programmatic refresh tokens are available for a limited set of partners." For sign-in only, discard the access token after reading the ID token / userinfo.

**Redirect URI (S3):** HTTPS, registered in the app; "URLs must be absolute"; "Parameters are ignored"; "URLs cannot include a #"; request value "must match one of the Redirect URLs defined in your application configuration." So any absolute HTTPS URL you control, pre-registered.

**Rate limits:** none stated for userinfo/OIDC in the fetched pages. Not verified.

**Mobile login (S3):** "Social login (Google/Apple) and passkey login are currently supported on desktop and Android web browsers only" — on iOS web the member signs in with LinkedIn credentials unless already logged in.

**Terms (S5):** "You must not capture, copy, cache, or store any Content ... except as expressly permitted." "You may store the Application-specific Member tokens and authentication tokens". "To the extent required under applicable law, you must obtain legally valid consent from a Member...before you may store that Member's Profile Data." "Before a User authenticates their LinkedIn account with your Application, you must obtain that User's legally valid consent." "You must immediately delete all Content collected through the APIs on behalf of a User...upon request by that User". Brand: licence "to display Our Brand Features solely within the Application to identify the LinkedIn integration."

## 2. Alternatives

| Mechanism | Verified identity of tester | Phone friction | Notes |
|---|---|---|---|
| LinkedIn OIDC | name + (optional) email as LinkedIn holds them; LinkedIn says it does not verify identity | low if LinkedIn app/session logged in; iOS web may need password | matches where you meet them; consent + deletion duties (S5); possible Page requirement |
| Signed single-use invite link | only that the holder of the link you sent clicked it; you know who you sent it to | lowest: one tap, no account | link can be forwarded; mitigate with single use + short expiry + bind to session |
| Email OTP / magic link to email | proves control of an email address | medium: switch to mail app | NIST: email "SHALL NOT be used for out-of-band authentication" (fine as low-assurance demo gate, not "authentication" in 800-63 sense) |
| GitHub OAuth | GitHub account; recruiters often lack one | high for recruiters | good for engineers only (not verified at source this session) |
| Passkeys | none on first use — need prior enrolment | high for a one-off trial | wrong fit for strangers' first visit (not verified at source this session) |

## 3. Invite-token best practice

Sources: OWASP Forgot Password CS https://cheatsheetseries.owasp.org/cheatsheets/Forgot_Password_Cheat_Sheet.html ; OWASP Session Management CS https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html ; NIST SP 800-63B-4 https://pages.nist.gov/800-63-4/sp800-63b.html

- OWASP FP: tokens "Generated using a cryptographically secure random number generator", "Long enough to protect against brute-force attacks", "Invalidated after they have been used", "Stored in a secure manner"; add Referrer-Policy `no-referrer`; "Don't rely on the Host header while creating the reset URLs".
- OWASP SM: session IDs need "at least 64 bits of entropy"; renew session ID after privilege change; cookies Secure, HttpOnly, SameSite, `__Host-` prefix; server-side idle + absolute timeouts; log lifecycle with hashed IDs.
- NIST 800-63B-4: minimum 112-bit security strength for secrets/keys; out-of-band authentication "SHALL be considered invalid unless completed within 10 minutes".

Applied design: 256-bit token from `secrets.token_urlsafe(32)`; store only SHA-256(token) with invitee label, scope, expires_at, used_at, revoked_at; on first GET show an interstitial "Start demo" POST (prevents link-preview bots in LinkedIn/WhatsApp from burning the token), atomically mark used, issue a fresh `__Host-` session cookie with absolute TTL (e.g. 2 h) and scope claim; revocation = set revoked_at, sessions checked per request; audit log every privileged action with invite id; Referrer-Policy no-referrer; build URLs from a fixed base URL.

## 4. DPDP Act 2023 / DPDP Rules 2025

Source: Act text https://www.indiacode.nic.in/bitstream/123456789/22037/1/a2023-22.pdf ; PIB note on Rules https://static.pib.gov.in/WriteReadData/specificdocs/documents/2025/nov/doc20251117695301.pdf

- s.3(c): the Act shall "not apply to— (i) personal data processed by an individual for any personal or domestic purpose". "Personal or domestic purpose" is undefined. A public portfolio demo used for job-seeking/professional promotion is arguably not "personal or domestic" — do not rely on the exemption; comply lightly.
- s.5(1): consent request "shall be accompanied or preceded by a notice ... informing her,— (i) the personal data and the purpose ...; (ii) the manner in which she may exercise her rights under sub-section (4) of section 6 and section 13; and (iii) the manner in which the Data Principal may make a complaint to the Board".
- s.6(1): consent "shall be free, specific, informed, unconditional and unambiguous with a clear affirmative action ... limited to such personal data as is necessary".
- s.8(7): "erase personal data, upon the Data Principal withdrawing her consent or as soon as it is reasonable to assume that the specified purpose is no longer being served, whichever is earlier".
- Rules: notified 14 Nov 2025; PIB: "The Rules introduce an eighteen-month period for phased compliance" and "Every Data Fiduciary must issue a separate consent notice that is clear and easy to understand."

Minimal practice: collect only name + email (or nothing, with invite links); one-screen notice (what, why = "to know who tried my demo and follow up", retention, how to withdraw/delete = email address, complaint to Data Protection Board); unticked checkbox / explicit "Agree and start"; auto-delete identity records 90 days after the session; delete on request.

## 5. Recommendation

- Primary: owner-issued signed single-use invite links (per person, 256-bit, hash-stored, 24–72 h to redeem, 2 h session, revocable, scoped role, all actions audited by invite id). Lowest friction on a phone; owner knows who received each link.
- Interstitial POST "Start demo" with notice + consent checkbox, so link-preview bots cannot consume tokens and DPDP s.5/s.6 is met at the same click.
- Fallback / identity upgrade: optional "Sign in with LinkedIn" (openid profile, no email needed) when the owner wants a LinkedIn-asserted name — store only sub + name, delete on request (S5).
- Anonymous visitors: read-only, no personal data collected.
- Elevated actions run against an isolated sandbox/lab, never real devices, regardless of auth path.
- Retention: purge invite + identity rows after 90 days; keep only aggregate counts.
- Skip email OTP, GitHub OAuth and passkeys for this audience.

## Open items (not verified)
- Whether creating a LinkedIn app requires a Company Page.
- LinkedIn rate limits for userinfo.
- GitHub OAuth / passkey claims not fetched this session.
