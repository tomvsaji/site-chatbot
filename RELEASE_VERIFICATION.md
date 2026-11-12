# Consolidated release verification

The security-only branch's earlier verification history is retained in
IMPLEMENTATION_REPORT.md. The consolidated release preserves the native LFM3
agent, 8192-token context, small site passages and private compressed memory.

Current checks and production status are recorded in INTEGRATION_REPORT.md.
Turnstile is disabled; its tests use fixtures, not real verification keys.
Cross-site native frontend memory requires a coordinated same-origin proxy;
it is not enabled by permissive CORS.
