# Command line

```
k8sfoams [--host HOST] [--port PORT] [-d] [--page-size N] [--no-watch]
         [--synthetic] [--seed N] [--churn-interval SECONDS]
         [--token TOKEN | --no-auth] [--allowed-host NAME] [-v]
```

| Argument | Default | Meaning |
| --- | --- | --- |
| `--host` | `127.0.0.1` | IP address the server listens on |
| `--port` | `8080` | port the server listens on |
| `-d` | off | run the server in debug mode |
| `--page-size` | `500` | objects fetched per LIST request; at least 1 |
| `--no-watch` | off | list nodes and pods on every request instead of keeping a [watch cache](concepts.md#listing-the-cluster) per context |
| `--synthetic` | off | show the generated [demo clusters](synthetic-mode.md) instead of your kubeconfig contexts; needs no cluster |
| `--seed` | `42` | seed of the `--synthetic` clusters; the same seed builds the same clusters |
| `--churn-interval` | `10` | seconds between random changes to the `--synthetic` clusters; `0` keeps them still |
| `--token` | generated off loopback | token every API call must carry; see [Network access](#network-access) |
| `--no-auth` | off | serve a non-loopback `--host` without a token |
| `--allowed-host` | | extra `Host` header the server answers to; repeat for every name or IP you open it by |
| `-v`, `--version` | | print the version and exit |

Examples:

```bash
k8sfoams --host 0.0.0.0 --allowed-host dash.internal --port 8080
k8sfoams --page-size 200 --no-watch
k8sfoams --synthetic --churn-interval 2
```

## Network access

By default the server listens on `127.0.0.1` and only this machine can reach it. Every request whose `Host` header is
not `localhost`, `127.0.0.1`, `[::1]`, the `--host` value or an `--allowed-host` gets `403`, so a web page cannot read
the dashboard through DNS rebinding. `POST`s whose `Origin` is another site get `403` too.

With any other `--host`, the dashboard shows your cluster, read with your kubeconfig credentials, to anyone who can
reach the port. k8sfoams then prints a warning and, unless you pass `--token` or `--no-auth`, a URL with a fresh
token:

```
Open http://0.0.0.0:8080/#token=…
```

Open it with the name you allowed, e.g. `http://dash.internal:8080/#token=…`. The token sits in the URL fragment, which
the browser never sends; the dashboard keeps it for the tab and drops it from the address bar, so shared links never
carry it. Scripts send `Authorization: Bearer <token>`; see [HTTP API](http-api.md#authentication). A `--token` value is
visible in `ps` and shell history; the generated one is not.

## Environment

| Variable | Meaning |
| --- | --- |
| `OPENAI_API_KEY` | turns on the [AI advisor](advisor.md); needs `pip install 'k8sfoams[advisor]'` |
| `OPENAI_BASE_URL` | OpenAI-compatible endpoint for the advisor, e.g. `http://localhost:11434/v1` for Ollama |
| `K8SFOAMS_ADVISOR_MODEL` | advisor model, default `gpt-4o-mini` |

The API key is never a flag, so it stays out of shell history and `ps`.

## Exporting the demo site

```bash
python -m k8sfoam.src.synthetic.export OUT_DIR [--seed N]
```

Writes the dashboard and the data of every synthetic cluster to `OUT_DIR` as static files, the way the
[live demo](demo/index.html) on this site is built. It needs the built UI (`make web`). See
[Synthetic mode](synthetic-mode.md#the-live-demo-on-this-site).
