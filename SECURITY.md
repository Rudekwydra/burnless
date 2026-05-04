# Security Notes

Burnless is local-first. Project state lives under `.burnless/`; provider keys
and release tokens must live outside the repository.

## Secret Handling

- Do not commit `.env`, `.env.*`, `.pypirc`, private keys, or generated logs.
- Keep local secrets in `~/.config/burnless/*.env` with `chmod 600`.
- Use `.env.example` only as a schema.
- PyPI uploads should use `TWINE_USERNAME=__token__` and `TWINE_PASSWORD=pypi-...`
  loaded from `~/.config/burnless/pypi.env`.

## Public Browser Keys

The static site may contain publishable/browser keys such as Supabase
publishable keys. Treat those as public identifiers, not secrets. Any table or
function reachable from a browser key must be protected with server-side
authorization or Supabase RLS.

## Reporting Issues

Open a private security report or contact the maintainer directly if a secret,
credential, or release-token issue is found.
