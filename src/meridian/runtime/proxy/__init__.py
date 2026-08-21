"""The model proxy.

One small component that solves four problems at once, which is why it earns its
place in an MVP:

- **Secrets never enter the trial container.** The container has no credential;
  it talks to the proxy, and the proxy attaches the real key. A compromised agent
  cannot exfiltrate a key it was never given.
- **Egress is controlled by construction.** The trial network is `internal` and
  has exactly two members. The agent cannot reach the internet because there is
  no route, not because a filter said no.
- **Budget is enforced where the spend happens.** The proxy counts tokens and
  returns a structured 429 once a ceiling is crossed. The agent sees a failed
  call; the trial is classified `fail`, not `harness_error`.
- **Replay is exact.** Recording removes the model as a source of variance, which
  is what turns Rule 4 from an aspiration into a guarantee.
"""
