// Single source of truth for the two fixed test tenants these E2E specs run
// against. Raw API keys live here (not pre-computed hashes) so global-setup
// and the specs can never drift from each other or from the gateway's own
// hashing (app/auth.py hash_key: sha256 hex of the raw key, same algorithm
// used below in global-setup.ts).
//
// Two tenants, not one, because the rate-limit spec needs a policy tight
// enough to trip in a handful of requests without burning through the
// 100 rps budget the auth/proxy specs share.
export const E2E_TENANTS = {
  auth: {
    name: "e2e-auth",
    apiKey: process.env.E2E_API_KEY_AUTH ?? "sk_test_e2e_auth_fixedkey_localdev",
    rateLimitRps: 100,
  },
  ratelimit: {
    name: "e2e-ratelimit",
    apiKey: process.env.E2E_API_KEY_RATELIMIT ?? "sk_test_e2e_ratelimit_fixedkey_localdev",
    rateLimitRps: 3,
  },
} as const;
