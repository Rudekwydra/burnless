# /burnless-status

Show current Burnless session state: plan, open delegations, and token metrics.

## Usage

```
/burnless-status
```

## Steps

1. Check that Burnless is installed: run `burnless --version`. If not found, tell the user to run `pip install burnless` and stop.

2. Run `burnless status` and display the output.

3. Run `burnless metrics` and display the token counter and estimated cost avoided.

4. If `.burnless/maestro.md` exists, read and summarize the current plan in 2-3 lines.

## Output format

Summarize in this order:
- Current plan (if any)
- Open delegations (dNNN — tier — status)
- Tokens saved this session + estimated cost avoided
