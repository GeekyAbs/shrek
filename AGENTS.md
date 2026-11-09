# Project rules

## Plan mode: grill before planning

When entering plan mode (or beginning to plan any feature/change), load `.agents/skills/grilling` and follow it exactly:

- Work the design tree in rounds; each round asks the whole frontier of currently-unblocked decisions.
- Present every question as a multiple-choice question (MCQ) using the `question` tool, with 2–4 options each and your recommended option first, labeled "Recommended".
- Word each question so "yes" accepts the recommended answer.
- Find facts (codebase, filesystem, tools) yourself or via sub-agents; bring decisions to the user.
- Keep grilling until the frontier is empty and the user confirms shared understanding. Do not act on the plan until they confirm.
