---
tracker:
  kind: gitcode
  owner: chadwweng
  repo: AgentLearning
  api_key: $GITCODE_TOKEN
  active_states:
    - open

workspace:
  repo_clone_url: https://token:WV9arqgZdsAPdL3TQYgASrrR@gitcode.com/chadwweng/AgentLearning.git
  clone_depth: 1
  checkout_issue_branch: true

hooks:
  before_run: echo "Before run for issue $ISSUE_IDENTIFIER"
  after_run: echo "After run completed"

agent:
  max_concurrent_agents: 2
  max_turns: 20

observability:
  dashboard_enabled: true
---