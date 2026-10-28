# Command line

```
k8sfoams [--host HOST] [--port PORT] [-d] [--page-size N] [--no-watch]
         [--synthetic] [--seed N] [--churn-interval SECONDS] [-v]
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
| `-v`, `--version` | | print the version and exit |

Examples:

```bash
k8sfoams --host 0.0.0.0 --port 8080 -d
k8sfoams --page-size 200 --no-watch
k8sfoams --synthetic --churn-interval 2
```

## Exporting the demo site

```bash
python -m k8sfoam.src.synthetic.export OUT_DIR [--seed N]
```

Writes the dashboard and the data of every synthetic cluster to `OUT_DIR` as static files, the way the
[live demo](demo/index.html) on this site is built. It needs the built UI (`make web`). See
[Synthetic mode](synthetic-mode.md#the-live-demo-on-this-site).
