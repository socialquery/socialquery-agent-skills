# SocialQuery agent skills

Installable skills for agents working with public social data through [SocialQuery](https://socialquery.dev). Includes MCP guidance and a self-contained Python REST helper.

## Install

```bash
npx skills add ricciflow-api/socialquery-agent-skills
```

Or copy `skills/socialquery-data` into your project's `.agents/skills/` directory. The skill is discoverable as `socialquery-data`.

## Use

Connect [SocialQuery MCP](https://github.com/ricciflow-api/socialquery-mcp) at `https://socialquery.dev/mcp` using Streamable HTTP and an `Authorization: Bearer <API key>` header, or use the standard-library Python helper (Python 3.9+):

```bash
python3 skills/socialquery-data/scripts/socialquery_api.py discover --platform hackernews
python3 skills/socialquery-data/scripts/socialquery_api.py inspect get_hackernews_search
```

For a data call, create a [SocialQuery API key](https://socialquery.dev/dashboard/api-keys), set `SOCIALQUERY_API_KEY` locally, and choose an explicit credit budget:

```bash
python3 skills/socialquery-data/scripts/socialquery_api.py call get_hackernews_search \
  --params '{"query":"developer tools","hitsPerPage":10}' --max-credits 1
python3 skills/socialquery-data/scripts/socialquery_api.py balance
```

The helper makes one bounded request, checks its live catalog price, rejects redirects, and never automatically retries or paginates. GET is the default. For a requested POST, PATCH, or DELETE, add `--allow-mutation` and a stable `--idempotency-key`; inspect the endpoint and its effects first. Discovery is free. Catalog inclusion is not a guarantee of live upstream availability. See the [skill](skills/socialquery-data/SKILL.md) for workflow and evidence handling.

## Validate

```bash
npm test
npm run test:live
```

Tests use mock credentials and responses. Live checks run public discovery without a key or credit spend.

[Documentation](https://docs.socialquery.dev) · [API examples](https://github.com/ricciflow-api/socialquery-api-examples) · [MCP](https://github.com/ricciflow-api/socialquery-mcp) · [MIT](LICENSE)
