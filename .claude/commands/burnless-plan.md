# /burnless-plan

Write a Maestro plan for a multi-step objective and break it into delegations.

## Usage

```
/burnless-plan <objective>
```

## Steps

1. Check that Burnless is installed: run `burnless --version`. If not found, tell the user to run `pip install burnless` and stop.

2. Run: `burnless plan "$ARGUMENTS"`

3. Read `.burnless/maestro.md` after it's written and display the plan.

4. Ask the user: "Ready to start? I'll delegate the first task." If yes, identify the first delegation from the plan and run `/burnless-delegate <first task>`.

## Notes

- The plan lives in `.burnless/maestro.md` — edit it directly at any time.
- Each step in the plan becomes one `burnless delegate` call.
- Burnless picks the tier automatically based on routing keywords — override with `--tier gold` if needed.
