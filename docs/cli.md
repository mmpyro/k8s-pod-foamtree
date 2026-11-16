# Command line

```
k8sfoams [serve] [--host HOST] [--port PORT] [-d] [--page-size N] [--no-watch]
         [--synthetic] [--seed N] [--churn-interval SECONDS]
         [--token TOKEN | --no-auth] [--allowed-host NAME] [-v]
```

`serve` runs the dashboard. It is the default command, so `k8sfoams --port 9000` and `k8sfoams serve --port 9000`
are the same. [`report`](#report) writes the export without the dashboard.

| Argument | Default | Meaning |
| --- | --- | --- |
| `--host` | `127.0.0.1` | IP address the server listens on |
| `--port` | `8080` | port the server listens on |
| `-d` | off | run the server in debug mode. Loopback `--host` only, because the debugger skips the token and `Host` checks |
| `--page-size` | `500` | objects fetched per LIST request. At least 1 |
| `--no-watch` | off | list nodes and pods on every request instead of keeping a [watch cache](concepts.md#listing-the-cluster) per context |
| `--synthetic` | off | show the generated [demo clusters](synthetic-mode.md) instead of your kubeconfig contexts. Needs no cluster |
| `--seed` | `42` | seed of the `--synthetic` clusters. The same seed builds the same clusters |
| `--churn-interval` | `10` | seconds between random changes to the `--synthetic` clusters. `0` keeps them still |
| `--token` | generated off loopback | token every API call must carry. Must not be empty. See [Network access](#network-access) |
| `--no-auth` | off | serve a non-loopback `--host` without a token |
| `--allowed-host` | | extra `Host` header the server answers to. Repeat it for every name or IP you open the dashboard by |
| `-v`, `--version` | | print the version and exit |

Examples:

```bash
k8sfoams --host 0.0.0.0 --allowed-host dash.internal --port 8080
k8sfoams --page-size 200 --no-watch
k8sfoams --synthetic --churn-interval 2
```

## Report

```
k8sfoams report [--context NAME] [--format json|csv] [--output FILE] [--fail-on POLICIES]
                [--timeout SECONDS] [--synthetic] [--seed N]
```

`report` writes the dashboard's [JSON or CSV report](export.md) for one context and exits. Use it in CI pipelines and
cron jobs.

The schema and units are the same as in the browser export: same columns, CPU in millicores, memory in MiB,
one column per extended resource. There is no query, so `matched` stays blank in the CSV and the JSON leaves it out.
`metric` is `cpu`. Node prices come from the bundled [price snapshot](cost.md). The JSON report opens in the dashboard
as a [snapshot](export.md#snapshots), for example to compare last night's report with today's.

| Argument | Default | Meaning |
| --- | --- | --- |
| `--context` | current context | kubeconfig context to report on |
| `--format` | `json` | `json` or `csv` |
| `--output` | stdout | file to write the report to |
| `--fail-on` | | comma-separated policies. Exit `1` when any pod or node breaks one (see below) |
| `--timeout` | `30` | seconds per API request. An unreachable cluster exits `2` and does not hang. The client retries a failed connect 3 times, so the worst case is about 4× this value. `0` waits forever |
| `--synthetic` | off | report on a generated [demo cluster](synthetic-mode.md). Needs no cluster |
| `--seed` | `42` | seed of the `--synthetic` clusters |

`--fail-on` takes the [query bar](filtering.md)'s `audit:<rule>` and `health:<warning>` tokens, e.g.
`audit:missing-requests,audit:naked-pod,health:not-ready`. The tokens are case-insensitive. An unknown token is an error.

| Exit code | Meaning |
| --- | --- |
| `0` | report written, no policy violated |
| `1` | report written, a `--fail-on` policy is violated. stderr lists every offending `namespace/pod` or node |
| `2` | error: bad arguments, unknown policy or context, cluster unreachable |

```bash
k8sfoams report --context prod --format csv --output prod.csv
k8sfoams report --fail-on audit:missing-requests,health:not-ready > /dev/null
```

A GitHub Actions job that fails when a pod has no requests, and keeps the report as an artifact:

```yaml
- run: pip install k8sfoams
- run: k8sfoams report --context prod --format json --output report.json --fail-on audit:missing-requests
  env:
    KUBECONFIG: ${{ runner.temp }}/kubeconfig   # written by an earlier step
- uses: actions/upload-artifact@v4
  if: always()
  with:
    name: k8sfoams-report
    path: report.json
```

A nightly CSV with cron:

```
0 6 * * * k8sfoams report --context prod --format csv --output "$HOME/reports/prod-$(date +\%F).csv"
```

## Network access

By default the server listens on `127.0.0.1`, and only this machine can reach it. The server answers `403` to every
request with a `Host` header other than `localhost`, `127.0.0.1`, `[::1]`, the `--host` value or an `--allowed-host`.
This stops a web page from reading the dashboard through DNS rebinding. `POST`s whose `Origin` is another site get `403`
too.

With any other `--host`, anyone who can reach the port sees your cluster, read with your kubeconfig credentials.
k8sfoams then prints a warning. Unless you pass `--token` or `--no-auth`, it also prints a URL with a fresh token:

```
Open http://0.0.0.0:8080/#token=…
```

Open it with the name you allowed, for example `http://dash.internal:8080/#token=…`. The token is in the URL fragment,
and the browser never sends the fragment. The dashboard keeps the token for the tab and removes it from the address bar,
so shared links never contain it. Scripts send `Authorization: Bearer <token>`. See [HTTP API](http-api.md#authentication).

A `--token` value shows in `ps` and in shell history. The generated token does not.

## Environment

| Variable | Meaning |
| --- | --- |
| `OPENAI_API_KEY` | turns on the [AI advisor](advisor.md). Needs `pip install 'k8sfoams[advisor]'` |
| `OPENAI_BASE_URL` | OpenAI-compatible endpoint for the advisor, e.g. `http://localhost:11434/v1` for Ollama |
| `K8SFOAMS_ADVISOR_MODEL` | advisor model, default `gpt-4o-mini` |

The API key is never a flag, so it stays out of shell history and `ps`.

## Exporting the demo site

```bash
python -m k8sfoam.src.synthetic.export OUT_DIR [--seed N]
```

This command writes the dashboard and the data of every synthetic cluster to `OUT_DIR` as static files. The
[live demo](demo/index.html) on this site is built the same way. It needs the built UI (`make web`). See
[Synthetic mode](synthetic-mode.md#the-live-demo-on-this-site).
