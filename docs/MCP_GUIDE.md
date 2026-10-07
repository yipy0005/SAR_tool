# Use the SAR Workbench from an LLM (MCP)

The MCP server lets an assistant such as Kiro look up compounds, read results, run searches and start analyses in the web app. It signs in to the app the way you do, so project membership, CSRF protection and the audit trail all still apply. It uses only the Python standard library, so it adds no dependencies.

## Set up (once)

1. Start the web app: `pixi run start-local` (it listens on `http://127.0.0.1:5001`).
2. Store your web-app login in a private file: `pixi run mcp-credentials`. It writes `~/.config/sar-workbench/mcp.env` with owner-only permissions and tests the sign-in. The server refuses the file if anyone else can read it.
3. Check the connection: `pixi run mcp-check`.
4. Connect Kiro. This workspace already has `.kiro/settings/mcp.json`. For another workspace or user-level setup, run `pixi run mcp-config` and merge the printed entry into `.kiro/settings/mcp.json` (workspace) or `~/.kiro/settings/mcp.json` (user). [`docs/kiro-mcp.example.json`](kiro-mcp.example.json) shows the shape with placeholder paths. Then reconnect `sar-workbench` from the MCP Server view in Kiro.

If the app runs without a login screen (`SAR_ENV` not `production`), skip step 2.

## Permission modes

Set `SAR_MCP_MODE` in the `env` block of `mcp.json`. Tools outside the mode are hidden from the model and refused if called by name.

| Mode | Adds | Tools |
| --- | --- | --- |
| `read-only` | Nothing is stored (exports write an audit record) | 16 |
| `analyze` (default) | Stores derived analysis runs and prediction models | 19 |
| `full` | Creates projects and imports measurements | 22 |

Kiro asks before each call except the read tools listed in `autoApprove`. `sar_export_overview` is not auto-approved because each export writes an audit record. Keep stored-result and write tools out of `autoApprove`, so you see each one before it runs.

A stored analysis run is visible in the web app. The Find patterns page shows the latest run, so an assistant-triggered pharmacophore or R-group run can change what that page displays. Use `read-only` when you only want answers.

## Tools

| Tool | Tier | What it does |
| --- | --- | --- |
| `sar_status` | read | Reachability, who is signed in, active mode |
| `sar_list_projects` | read | Projects you can access |
| `sar_search_compounds` | read | Find compounds by id, name or structure text |
| `sar_get_compound` | read | One compound with raw measurements and summaries |
| `sar_list_endpoints` | read | Endpoint keys with units and exact versus censored counts |
| `sar_list_measurements` | read | Raw measurements with qualifiers and QC |
| `sar_list_result_summaries` | read | Per-compound summarised results |
| `sar_export_overview` | read | Export section sizes, or the first rows of one section |
| `sar_standardize_structure` | read | RDKit standardization of a SMILES or molfile |
| `sar_substructure_search` | read | Which compounds contain a SMILES or SMARTS |
| `sar_core_from_structure` | read | Core SMARTS for R-group analysis |
| `sar_propose_substituent` | read | Propose an R-site swap; nothing is saved |
| `sar_discover_series` | read | Group compounds by scaffold for an endpoint |
| `sar_open_in_workbench` | read | A link that opens a chosen page of the web app, optionally pinned to a compound, a stored run, a search or two assays |
| `sar_list_records` | read | Saved series, claims, recommendations, designs, models, relationships |
| `sar_get_analysis_run` | read | Read back a stored analysis run |
| `sar_run_analysis` | analyze | Measurement summaries, properties, MMP, R-group, pharmacophore R-group, activity cliffs, selectivity, cellular translation, ADME, Pareto, contradictions, information gap, recommendations |
| `sar_train_prediction_model` | analyze | Train a model; the app applies its fixed qualification thresholds |
| `sar_predict_compounds` | analyze | Predict with a stored model; blocked and out-of-domain rows are returned as such |
| `sar_create_project` | write | Create an empty project |
| `sar_import_preview` | write | Check CSV text without saving results |
| `sar_import_commit` | write | Save a previewed import; needs `user_confirmed=true` |

## Links to the web app

Most results start with a `web_link` that opens the matching page of the web app in your browser, so you can look at the output there. Other results add `related_links`, and `sar_open_in_workbench` builds a link on request ("show me this compound in the pattern explorer").

| Result of | Link opens |
| --- | --- |
| `sar_run_analysis` (R-group, MMP, cliffs, selectivity, ADME and so on) | Find patterns, showing that exact run |
| `sar_run_analysis` with `measurement_summaries` | Build summaries |
| `sar_run_analysis` with `information_gap` or `recommendations` | Choose next, where recommendations await review |
| `sar_substructure_search` | Find patterns with Find by structure open and the search already run |
| `sar_get_compound` | Check results, plus related links to the SAR series and the pattern explorer around that compound |
| `sar_discover_series`, series and relationship records | SAR series |
| `sar_list_projects`, `sar_search_compounds`, `sar_create_project` | Start (the SAR table) |
| `sar_export_overview` | `download_link`: a CSV download |

Opening a link uses your own browser sign-in, so what you can see follows your project membership, and a link to one project does not reveal another. Links contain only project, compound and run ids and search text, never a token or password. The address in a link is `SAR_MCP_BASE_URL`, or `SAR_MCP_PUBLIC_URL` when your browser reaches the app at a different address (for example through a proxy).

A link to a stored run shows a notice at the top of the page naming the run, when it was made, and whether a newer one exists. Everything else on the page remains the latest result. If the run does not exist in that project the page says so and shows the latest results. The web app's URL parameters are described in [`WEB_APP_GUIDE.md`](WEB_APP_GUIDE.md).

## What the assistant cannot do

The server does not expose these, in any mode, because they are review decisions for people:

- approving or rejecting generated recommendations;
- creating SAR claims, series or curated designs;
- changing project membership or roles;
- loosening the prediction qualification thresholds.

The assistant can still read all of these records. Deleting or overwriting data is not possible through any tool.

## Keeping data safe

- **Data leaves this machine.** Tool results are sent to the model provider behind your assistant. Connect only projects your organisation allows you to share with it. `SAR_MCP_PROJECT_IDS` (comma-separated project ids) confines the server to those projects. It is a guard rail: the app's own project membership remains the real access boundary.
- **Credentials.** They live in the private file, never in `mcp.json`. The server does not send them over plain `http://` to any host other than `localhost`, `127.0.0.1` or `::1`, and it follows redirects only within the configured address. Environment variables `SAR_MCP_EMAIL` and `SAR_MCP_PASSWORD` take precedence over the file. If you use Kiro's `${VAR}` syntax for them, approve the variables in Kiro settings ("Mcp Approved Env Vars"), and note that a Kiro started from the Dock may not see variables exported in your shell.
- **Untrusted text.** Compound names, notes and imported files are user content. The server tells the model to treat them as data, but no prompt is a complete defence; review what you approve.
- **Output size.** Drawings and molfiles are removed, and long lists are cut with a note saying how many rows were left out. Raise `SAR_MCP_MAX_CHARS` (default 40000) if needed.

## Settings

| Variable | Default | Meaning |
| --- | --- | --- |
| `SAR_MCP_BASE_URL` | `http://127.0.0.1:5001` | Web app address (origin only) |
| `SAR_MCP_MODE` | `analyze` | `read-only`, `analyze` or `full` |
| `SAR_MCP_CREDENTIALS_FILE` | `~/.config/sar-workbench/mcp.env` | Private file with the login |
| `SAR_MCP_PUBLIC_URL` | same as base URL | Address used in links, if your browser reaches the app differently |
| `SAR_MCP_PROJECT_IDS` | all accessible | Comma-separated allowlist |
| `SAR_MCP_TIMEOUT` | `60` | Seconds per request (analyses wait at least 180) |
| `SAR_MCP_MAX_CHARS` | `40000` | Largest result text returned |
| `SAR_MCP_ALLOW_INSECURE_HTTP` | off | Allow plain `http://` to a non-local host (trusted networks only) |
| `SAR_MCP_LOG_LEVEL` | `warning` | Logs go to stderr, never stdout |

## Troubleshooting

- **"Cannot reach the SAR Workbench"**: start the app with `pixi run start-local`, or correct `SAR_MCP_BASE_URL`.
- **"requires sign-in but no credentials"**: run `pixi run mcp-credentials`, then reconnect the server in Kiro.
- **"can be read by other users"**: run `chmod 600 ~/.config/sar-workbench/mcp.env`.
- **"Email or password was not accepted"**: rerun `pixi run mcp-credentials`. After three failures the server stops trying until it restarts.
- **A tool is missing**: it is hidden by `SAR_MCP_MODE`. `sar_status` shows the active mode.
- **A tool returns 403**: your account lacks the needed role on that project. Analyses need editor access.
- **No endpoints listed**: run `sar_run_analysis` with `analysis=measurement_summaries` first.

## Tests

`pixi run test` includes `tests/test_mcp_unit.py` and `tests/test_mcp_integration.py`. The integration tests start a real production-mode app and drive it through sign-in, CSRF, an import, analyses and searches, and run the real entry point over stdin and stdout.
