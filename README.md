# Netra MCP Server - Confluence Write Tool

This tool lets your AI assistant (Claude, GitHub Copilot, or any MCP-compatible client) read and edit Confluence pages for you. You describe what to change in plain language, and the AI calls the right tool.

**What it can do:**
- Show you every JQL query on a Confluence page, extracted cleanly with its location
- Replace version tokens or JQL values across an entire page in one shot
- Preview every change before anything is written (dry-run is on by default)
- Clone a release report page to a new version with all macros updated

---

## Before you start

You need:

- **Python 3.12 or newer** - check with `python --version`
- **uv** (Python package manager) - install from [docs.astral.sh/uv](https://docs.astral.sh/uv/getting-started/installation/)
- A **Confluence Cloud** account
- A **Confluence API token** - create one at: Atlassian account -> Security -> API tokens -> Create API token

---

## Setup (one time)

**1. Clone the repository**

```bash
git clone <repo-url>
cd netra-confluence-mcp
```

**2. Install dependencies**

```bash
uv sync
```

**3. Create your configuration file**

```bash
cp .env.example .env
```

Open `.env` and fill in your details:

```ini
CONFLUENCE_BASE_URL=https://your-org.atlassian.net
CONFLUENCE_API_TOKEN=your-api-token-here
CONFLUENCE_USER_EMAIL=your-email@example.com
CONFLUENCE_SITE_URL=https://your-org.atlassian.net
LOG_LEVEL=INFO
```

`CONFLUENCE_BASE_URL` and `CONFLUENCE_SITE_URL` are usually the same value.

**4. Test that it starts**

```bash
uv run python server.py
```

You should see the server start without errors. Press Ctrl+C to stop it - your AI client will start it automatically when needed.

---

## Connect your AI client

### Claude Desktop

1. Open your Claude Desktop config file:
   - **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
   - **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

2. Add this block (adjust the `cwd` path to where you cloned the repo):

```json
{
  "mcpServers": {
    "netra-confluence-writer": {
      "command": "uv",
      "args": ["run", "python", "server.py"],
      "cwd": "/absolute/path/to/netra-confluence-mcp"
    }
  }
}
```

3. Restart Claude Desktop. The Confluence tools will appear in the tool list.

### VS Code with GitHub Copilot

1. Open your VS Code user settings (`Ctrl+Shift+P` -> "Open User Settings JSON")

2. Add:

```json
{
  "mcp": {
    "servers": {
      "netra-confluence-writer": {
        "command": "uv",
        "args": ["run", "python", "server.py"],
        "cwd": "/absolute/path/to/netra-confluence-mcp"
      }
    }
  }
}
```

3. Reload VS Code. The tools are available in Copilot Chat when you click the tools icon.

### Other MCP clients

Use `uv run python server.py` as the server command with the project directory as the working directory. By default the server uses stdio transport (a child process spawned per client) - the standard transport for local MCP clients.

---

## Production transport (http)

For cloud or shared deployment the server runs standalone as a web service instead of being spawned as a child process - one server process serves many clients over HTTP. Every tool call here is a single, independent read-transform-write against Confluence (there is no clarification loop or multi-turn session), so the server runs **stateless**: no session store, no Valkey, nothing to keep in memory between requests.

Start it in HTTP mode:

```bash
SERVER_TRANSPORT=http uv run python server.py
```

(Or set `SERVER_TRANSPORT=http` in `.env` instead.) `main()` in `server.py` reads the settings and passes `host`, `port`, and `stateless_http=True` directly to FastMCP's `run()` for this transport, starting uvicorn bound to `127.0.0.1:8765` by default. The `/mcp` path is FastMCP's default endpoint for the streamable HTTP protocol:

```
http://127.0.0.1:8765/mcp
```

To change the bind address or port, set `SERVER_HOST` / `SERVER_PORT` before starting (e.g. host `0.0.0.0` to accept connections from other machines). A `/health` endpoint is also exposed for load balancer / CF health checks:

```bash
curl http://127.0.0.1:8765/health   # {"status":"ok"}
```

Connect with the MCP Inspector (`npx @modelcontextprotocol/inspector`, transport "Streamable HTTP", URL `http://127.0.0.1:8765/mcp`) or register it with Claude Code:

```bash
claude mcp add --transport http netra-confluence http://127.0.0.1:8765/mcp
```

**No auth yet.** There is no API-key gate on the HTTP endpoint (see Phase 4 in `docs/netra-mcp-confluence-write-phased-design.md`). Keep the default loopback bind (`127.0.0.1`) unless you are on a trusted network or sitting behind an auth-terminating proxy.

---

## Finding your Confluence page ID

The page ID is in the URL when you open a Confluence page:

```
https://your-org.atlassian.net/wiki/spaces/MYSPACE/pages/1234567890/Page+Title
                                                          ^^^^^^^^^^
                                                          this is your page ID
```

---

## Recipe: list all JQL queries on a page

Before you decide what to change, you can ask the AI to just show you what is on the page. This is read-only - nothing is written.

> "Show me all the JQL queries on Confluence page 34334."

The AI calls `inspect_page_jql("34334")` and returns:

```json
{
  "status": "INSPECTION",
  "page_id": "34334",
  "title": "R1.0 Release Report ProjectX",
  "jira_macro_count": 7,
  "jql_queries": [
    {
      "macro_id": "abc-123-...",
      "location_path": "table[0]/row[1]/cell[0]",
      "jql": "project = MYPROJ AND \"release status app1\" in (V1.0, V2.0) AND fixVersion = R1.0",
      "columns": "type,key,summary,status",
      "server": "MyJira",
      "max_issues": "20"
    },
    {
      "macro_id": "def-456-...",
      "location_path": "table[1]/row[0]/cell[0]",
      "jql": "project = MYPROJ AND label = app1_R1.0_Baseline AND fixVersion = R1.0",
      "columns": "type,key,summary",
      "server": "MyJira",
      "max_issues": "20"
    }
  ],
  "unique_strings": ["MYPROJ", "R1.0", "V1.0", "V2.0", "app1_R1.0", "app1_R1.0_Baseline"]
}
```

Key fields in the response:

- `status: "INSPECTION"` - always present; confirms this is the read-only path. No write happened.
- `jira_macro_count` - how many Jira macros the page has. If this is `0`, the page has no Jira macros and there is nothing to replace.
- `jql_queries[].jql` - the exact JQL string stored in each macro. Copy this verbatim if you plan to write a replacement rule - whitespace and quoting must match exactly.
- `jql_queries[].location_path` - human-readable ADF path so you know which macro you are looking at (`table[0]/row[1]/cell[0]`).
- `jql_queries[].macro_id` - the macro's UUID. Preserved on update, regenerated on clone.
- `unique_strings` - deduplicated token candidates already split on JQL syntax (whitespace, operators, quotes). Useful as a starting point to spot candidates, but **not** a direct replacement rule - it is a list of candidates, not the full substrings you would replace.

When to use this recipe:

- Before any update, to see what release tokens, project names, or version labels appear on the page.
- After an update, to verify the change landed where you expected.
- When you are handed an unfamiliar page and need to know what is on it before deciding what to do.

You can also pass a full Confluence URL instead of a bare page ID - the AI will extract the ID:

> "Show me the JQL on https://your-org.atlassian.net/wiki/spaces/ENG/pages/34334/R1.0-Report"

### Controlling the output format

The tool always returns the full JSON shape (with `status`, `page_id`, `title`, `jira_macro_count`, `jql_queries`, and `unique_strings`). The agent has the full response in its context but can choose to show you only the JQL strings. So:

| Your prompt | What the agent shows you |
|---|---|
| "Show me all the JQL queries on page 34334." | The full JSON dump (default behavior, what we documented in the recipe above) |
| "List just the JQL strings on page 34334." | A plain list of only the `jql` field values |
| "Show me the JQL queries on page 34334 as a bullet list." | One bullet per JQL, no metadata |
| "Show me only the JQLs and nothing else from page 34334." | Same as above, with explicit instruction to suppress the rest |
| "Give me a markdown table of the JQL queries on page 34334." | A formatted table - the agent can decide which columns to include |

A competent MCP client (Claude, Rovo) will honor all of these. The user always sees what they asked for.

---

## Worked example: change version V10.0 to V20.0

Say you have a release report page at page ID `2811939182` and every JQL query on it references `V10.0`. Here is how to update the whole page in three steps.

### Step 1 - Inspect the page

Ask your AI:

> "What JQL queries are on Confluence page 2811939182?"

The AI calls `inspect_page_jql` and returns something like:

```json
{
  "status": "INSPECTION",
  "page_id": "2811939182",
  "title": "V10.0 Release Report",
  "jira_macro_count": 12,
  "jql_queries": [
    {
      "macro_id": "abc-001",
      "location_path": "table[0]/row[1]/cell[2]",
      "jql": "project = MYPROJ AND fixVersion = ver_V10.0 AND status != Done",
      "columns": "key,summary,status"
    }
  ],
  "unique_strings": ["Done", "MYPROJ", "ver_V10.0", "ver_V10.0_Baseline", "V10.0"]
}
```

Look at `unique_strings` - these are the values you can replace. You can see `ver_V10.0_Baseline`, `ver_V10.0`, and `V10.0` all need to change.

### Step 2 - Preview the changes (dry run)

Ask your AI:

> "Preview replacing V10.0 with V20.0 on page 2811939182. Include label variants."

The AI calls `update_release_version` in dry-run mode:

```json
{
  "status": "DRY_RUN",
  "current_title": "V10.0 Release Report",
  "new_title": "V20.0 Release Report",
  "current_version": 14,
  "total_changes": 47,
  "change_summary": "jql: 36 changes\ntext: 8 changes\ntitle: 3 changes",
  "change_log": [...],
  "message": "Preview only. Call again with dry_run=False to apply."
}
```

Nothing was written yet. You can see exactly what would change.

### Step 3 - Apply the changes

If the preview looks correct, ask your AI:

> "Apply it."

The AI calls the same tool with `dry_run=False`:

```json
{
  "status": "UPDATED",
  "page_id": "2811939182",
  "title": "V20.0 Release Report",
  "version": 15,
  "url": "https://your-org.atlassian.net/wiki/spaces/MYSPACE/pages/2811939182",
  "total_changes": 47
}
```

Your Confluence page is now updated. Open the URL to verify.

---

## Other things you can do

### Replace a specific JQL value only

If you only want to change values inside JQL queries and not text or title:

> "Replace OLD_VALUE with NEW_VALUE in JQL queries only on page 99887766."

This uses `scope="jql"` which is precise - it will not touch column header names or other macro parameters even if they contain the same string.

### Clone a page to a new release

> "Clone page 2811939182 from version V10.0 to V20.0 with delivery date 2026-09-15. Create it as a draft."

The AI calls `clone_release_report`. The clone gets:
- All version tokens updated
- All Jira macro UUIDs regenerated (so the clone has no ID collisions with the original)
- All date nodes updated to the new delivery date

### Create a page from scratch

> "Create a new Confluence page in space MYSPACE titled 'Project Plan' using this ADF body: {...}"

---

## How it works

```mermaid
flowchart TD
    subgraph Client["Your AI (Claude / Copilot)"]
        A[Your message]
    end

    subgraph Server["Netra MCP Server"]
        subgraph Tools["MCP Tools"]
            T0[inspect_page_jql]
            T1[update_page_macros]
            T2[update_release_version]
            T3[clone_release_report]
            T4[create_page_from_adf]
        end
        subgraph Engine["ADF Engine"]
            R[Find and replace]
            V[Validate before write]
        end
    end

    subgraph Confluence["Confluence Cloud"]
        API[REST API]
    end

    A --> T0 & T1 & T2 & T3 & T4
    T1 & T2 & T3 --> R --> V
    T0 & V --> API
```

The server reads pages as ADF (Atlassian Document Format - the native JSON format Confluence uses internally). It finds JQL values at their exact JSON path, replaces them, validates the result, and writes back. No regex is used at any point.

---

## Tool reference

| Tool | What it does | Writes? |
|---|---|---|
| `inspect_page_jql` | Lists all Jira macro JQL queries on a page with location paths | Never |
| `update_page_macros` | Replaces values in macro parameters on any page | Yes, if `dry_run=False` |
| `update_release_version` | Like above, also updates date node timestamps | Yes, if `dry_run=False` |
| `clone_release_report` | Clones a page, updates all version tokens and macro IDs | Yes, if `dry_run=False` |
| `create_page_from_adf` | Creates a new page from an ADF body | Yes, if `dry_run=False` |

All write tools default to `dry_run=True`. You must explicitly say "apply it" or pass `dry_run=False` to make any change.

### Response status values

| Status | Meaning |
|---|---|
| `INSPECTION` | Read-only result; `jql_queries` and `unique_strings` present |
| `DRY_RUN` | Preview only; nothing written; `change_log` present |
| `UPDATED` | Page updated; `version` and `url` present |
| `CREATED` | New page created; `page_id` and `url` present |
| `NO_CHANGES` | No occurrences of the search term were found |
| `VALIDATION_FAILED` | ADF structure check failed; write blocked; `errors` list present |
| `VERSION_CONFLICT` | Someone else edited the page at the same time; retry |
| `ERROR` | API or network error; `error` field has details |

---

## Troubleshooting

**"Missing required fields" or 401 error**
Check your `.env` file. Make sure `CONFLUENCE_USER_EMAIL` matches the email on your Atlassian account, and `CONFLUENCE_API_TOKEN` is a valid token (not your password).

**"Page not found" (404)**
Double-check the page ID from the URL. The ID is the number in `.../pages/1234567890/...`.

**"Permission denied" (403)**
Your account needs at least View permission on the page to inspect it, and Edit permission to update it.

**The server is not appearing in my AI client**
Make sure the `cwd` path in your client config is the absolute path to the folder containing `server.py`. On Windows use forward slashes or escape backslashes.

**"Version conflict" after applying**
Someone else saved the page between your inspect and your apply. Just repeat the operation - the server re-reads the latest version before writing.

---

## Docker and cloud deployment

The server ships as a self-contained, stateless Docker image - no Valkey, no external datastore. Full runbook (CF first deploy, blue-green updates, env var reference): `docs/docker_cf_deployment.md`.

### Local dev with docker-compose

```bash
# From repo root (never from docker/)
docker-compose up
curl http://localhost:8765/health   # {"status":"ok"}
curl http://localhost:8765/mcp      # MCP endpoint
```

### Build the image locally (single arch, fast)

Requires Docker Desktop 4.x+ with buildx (shipped by default).

```bash
bash scripts/docker-build-local.sh
docker run -p 8765:8765 --env-file .env ghcr.io/sunishbharat/netra-confluence-mcp:dev
```

### Build and push multi-platform (amd64 + arm64) and deploy to CF

```bash
docker buildx create --name multiarch --driver docker-container --use   # one-time
docker buildx inspect --bootstrap

export REGISTRY=ghcr.io/sunishbharat
./scripts/cf-deploy.sh   # builds both platforms, pushes to GHCR, then cf push
```

Set Confluence credentials as CF secrets once (they survive `cf push`/`cf restage`):

```bash
cf set-env netra-confluence-mcp CONFLUENCE_BASE_URL https://your-org.atlassian.net
cf set-env netra-confluence-mcp CONFLUENCE_SITE_URL https://your-org.atlassian.net
cf set-env netra-confluence-mcp CONFLUENCE_USER_EMAIL service-account@example.com
cf set-env netra-confluence-mcp CONFLUENCE_API_TOKEN <token>
cf restage netra-confluence-mcp
```

CI builds and pushes a multi-arch image to GHCR automatically on every `v*.*.*` tag push (`.github/workflows/docker.yml`).

---

## Running tests

Unit tests (no Confluence connection required):

```bash
uv run python -m pytest
```

Integration tests against a real Confluence page:

```bash
CONFLUENCE_TEST_PAGE_ID=<page-id> uv run python -m pytest -m integration
```

---

## Development commands

```bash
uv sync                                                    # install all deps
uv run ruff check .                                        # lint
uv run ruff format --check .                               # format check
uv run mypy --strict .                                     # type check
uv run python -m pytest --cov=confluence --cov-report=term-missing
```
