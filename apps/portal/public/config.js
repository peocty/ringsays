// Runtime configuration. In the container image nginx serves this path from environment variables,
// so one image can be promoted from staging to production. Locally it is empty and build defaults apply.
window.__RINGSAYS_CONFIG__ = window.__RINGSAYS_CONFIG__ || {};
