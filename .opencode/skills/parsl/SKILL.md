---
name: parsl
description: Use when writing, debugging, or configuring Parsl parallel/HPC Python workflows. Covers apps, executors, configs, MPI, data staging, auto-scaling, checkpointing, and Jupyter integration. Trigger keywords: parsl, @python_app, @bash_app, @join_app, HighThroughputExecutor, MPIExecutor, parsl.load, DataFuture, File, parsl_resource_specification.
---

# Parsl: Parallel Scripting Library — AI Coding Assistant Reference

## 1. Setup & Lifecycle

### Installation

```bash
pip install parsl          # base
pip install "parsl[all]"   # all optional providers & monitoring
```

### The parsl.load() pattern

**Rule:** Call `parsl.load(config)` exactly once per Python process. Calling it again in the same process without clearing raises `NoDataFlowKernelError`.

```python
import parsl
from parsl.config import Config
from parsl.executors import HighThroughputExecutor

config = Config(
    executors=[HighThroughputExecutor(label="htex_local")]
)

# Pattern 1: context manager (preferred — auto-cleans up)
with parsl.load(config):
    future = my_app()
    print(future.result())

# Pattern 2: explicit load/clear (use in scripts or long-running services)
dfk = parsl.load(config)
future = my_app()
print(future.result())
parsl.clear()  # releases workers; required before re-loading a new config

# Pattern 3: factory function (for Jupyter / repeated calls)
def get_dfk():
    parsl.clear()   # no-op if nothing loaded
    return parsl.load(config)

dfk = get_dfk()
```

**Jupyter caveat:** Jupyter kernels are long-lived. Always wrap config loading in a factory that calls `parsl.clear()` first, or use the context manager inside a dedicated cell. Re-running the load cell without clearing will raise errors.

---

## 2. App Types & Rules

### @python_app

```python
from parsl import python_app

@python_app
def add(x, y):
    # RULE: all imports must be inside the function body
    import math
    return math.sqrt(x + y)

future = add(3, 4)          # returns AppFuture immediately
result = future.result()    # blocks until done
```

**Rules for `@python_app`:**
- **All imports go inside the function body.** The function runs in a remote worker process that may not have the same namespace as the main script.
- No closures over mutable state — only serializable arguments.
- Return values must be picklable.
- Use `parsl.File` for file arguments (not plain strings) when you need data staging.

### @bash_app

```python
from parsl import bash_app
from parsl.data_provider.files import File

@bash_app
def compress(input: File, output: File, stderr=None, stdout=None):
    # Return a shell command string — NOT the result
    return f"gzip -c {input.filepath} > {output.filepath}"

infile  = File("/data/sample.txt")
outfile = File("/data/sample.txt.gz")
future  = compress(inputs=[infile], outputs=[outfile])
future.result()
```

**Rules for `@bash_app`:**
- The function **returns a shell command string**, not a value.
- `inputs` and `outputs` kwargs accept lists of `File` objects.
- `stdout` and `stderr` kwargs accept file paths for logging.
- The return value of the app is the exit code.

### @join_app

```python
from parsl import join_app, python_app

@python_app
def generate(n):
    return list(range(n))

@join_app
def pipeline(n):
    # Launch sub-apps and return futures — Parsl resolves the DAG
    futures = [process(i) for i in generate(n).result()]
    return futures
```

**Rules for `@join_app`:**
- Returns a list of `AppFuture` objects (not values).
- Used to build dynamic DAGs where the number of sub-tasks is unknown at submission time.

### MPI & Multi-node Apps

Use `@bash_app` with `MPIExecutor` for MPI workloads.

```python
from parsl import bash_app
from parsl.executors import MPIExecutor
from parsl.launchers import MpiexecLauncher

@bash_app(executors=["mpi"])
def mpi_sim(num_ranks, stdout=None, stderr=None,
            parsl_resource_specification={}):
    return "mpi_simulation_binary --input input.dat"

# Submit with explicit resource specification
future = mpi_sim(
    num_ranks=32,
    parsl_resource_specification={
        "num_nodes": 4,        # nodes to allocate
        "num_ranks": 32,       # MPI ranks
        "ranks_per_node": 8,   # ranks per node
    }
)
future.result()
```

**MPIExecutor config:**

```python
from parsl.executors import MPIExecutor
from parsl.launchers import MpiexecLauncher, SrunLauncher

Config(
    executors=[
        MPIExecutor(
            label="mpi",
            max_workers=2,           # concurrent MPI jobs (not ranks)
            launcher=SrunLauncher(), # or MpiexecLauncher(), MpirunLauncher()
            provider=SlurmProvider(
                partition="mpi_queue",
                nodes_per_block=8,
                init_blocks=0,
                max_blocks=4,
                walltime="02:00:00",
            ),
        )
    ]
)
```

**Available MPI launchers:** `MpiexecLauncher`, `MpirunLauncher`, `SrunLauncher`, `AprunLauncher`, `SimpleLauncher`.

**`parsl_resource_specification` keys:**
- `num_nodes` — nodes required
- `num_ranks` — total MPI ranks
- `ranks_per_node` — ranks per node
- `cores_per_rank` — threads/OpenMP (optional)

---

## 3. Configuration

### Full Config skeleton

```python
from parsl.config import Config
from parsl.executors import HighThroughputExecutor, ThreadPoolExecutor
from parsl.providers import SlurmProvider, LocalProvider
from parsl.launchers import SrunLauncher
from parsl.addresses import address_by_hostname
from parsl.monitoring.monitoring import MonitoringHub
import os

config = Config(
    executors=[
        HighThroughputExecutor(
            label="htex",
            address=address_by_hostname(),
            cores_per_worker=1,
            max_workers=64,
            provider=SlurmProvider(
                partition="compute",
                account="my_project",
                nodes_per_block=1,
                cores_per_node=32,
                init_blocks=1,
                min_blocks=0,
                max_blocks=10,
                walltime="01:00:00",
                scheduler_options="#SBATCH --mem=64G",
                worker_init="source activate myenv",
                launcher=SrunLauncher(),
            ),
        ),
        ThreadPoolExecutor(label="local", max_threads=4),
    ],

    # Auto-scaling strategy
    strategy="simple",         # "simple" | "htex_auto_scale" | "none"
    max_idletime=120,          # seconds before idle block is removed

    # Checkpointing
    checkpoint_mode="task_exit",   # "task_exit" | "periodic" | "dfk_exit" | "manual"
    checkpoint_files=parsl.utils.get_all_checkpoints(),

    # Run directory
    run_dir=os.path.join(os.getcwd(), "parsl_runinfo"),

    # Monitoring
    monitoring=MonitoringHub(
        hub_address=address_by_hostname(),
        hub_port=55055,
        monitoring_debug=False,
        resource_monitoring_interval=10,
    ),
    
    retries=2,  # retry failed tasks up to N times
)
```

### Provider quick reference

| Provider | Import |
|---|---|
| `LocalProvider` | `parsl.providers` |
| `SlurmProvider` | `parsl.providers` |
| `PBSProProvider` | `parsl.providers` |
| `CobaltProvider` | `parsl.providers` |
| `LSFProvider` | `parsl.providers` |
| `AWSProvider` | `parsl.providers` |
| `GoogleCloudProvider` | `parsl.providers` |
| `KubernetesProvider` | `parsl.providers` |
| `GridEngineProvider` | `parsl.providers` |

### Auto-scaling parameters

```python
SlurmProvider(
    init_blocks=1,     # blocks started at config load (before any tasks)
    min_blocks=0,      # minimum blocks to keep alive (0 = scale to zero)
    max_blocks=10,     # hard cap on blocks
    parallelism=1.0,   # fraction of pending tasks to over-provision (0.0-1.0)
                       # 1.0 = provision one block per N pending tasks
                       # 0.5 = more conservative scale-up
)
```

`strategy="simple"`: Parsl adds blocks when tasks are waiting, removes blocks when idle.  
`strategy="htex_auto_scale"`: more aggressive scaling, uses pilot job logic.  
`strategy="none"`: manual; blocks only change if you call `executor.scale_out()`/`scale_in()`.

### Executor routing

```python
@python_app(executors=["htex"])      # pin to executor by label
def heavy_task(): ...

@python_app(executors=["local"])
def light_task(): ...
```

---

## 4. Data & File Handling

### parsl.File objects

```python
from parsl.data_provider.files import File

# Local file
f = File("/path/to/file.txt")

# HTTP file (auto-fetched to worker)
f = File("http://example.com/data.csv")

# FTP file
f = File("ftp://user:pass@host/data.gz")

# Globus file
f = File("globus://endpoint-uuid/path/to/file")
```

**DataFuture:**

```python
@bash_app
def process(inp: File, outputs=[]):
    return f"cat {inp} > {outputs[0]}"

out = File("/tmp/result.txt")
app_future = process(File("/tmp/input.txt"), outputs=[out])

# app_future.outputs[0] is a DataFuture
# Pass it to the next app — Parsl resolves ordering automatically
next_app(app_future.outputs[0])
```

### Data staging providers

| Scheme | Provider | Notes |
|---|---|---|
| `file://` / plain path | None (local) | Direct path access |
| `http://`, `https://` | `HTTPInTaskStaging` | Downloaded on worker |
| `ftp://` | `FTPInTaskStaging` | Downloaded on worker |
| `globus://` | `GlobusStaging` | Requires Globus auth setup |
| rsync | `RSyncStaging` | SSH/rsync between hosts |

**Enabling staging providers:**

```python
from parsl.data_provider.http import HTTPInTaskStaging
from parsl.data_provider.ftp import FTPInTaskStaging
from parsl.data_provider.globus import GlobusStaging

config = Config(
    executors=[
        HighThroughputExecutor(
            storage_access=[
                HTTPInTaskStaging(),
                FTPInTaskStaging(),
                GlobusStaging(
                    endpoint_id="uuid-of-target-endpoint",
                    endpoint_path="/remote/base/",
                    local_path="/local/mount/",
                ),
            ],
            ...
        )
    ]
)
```

**GlobusStaging** requires `pip install parsl[globus]` and Globus credentials configured.

---

## 5. Workflow Patterns

### Linear chain

```python
a = app_a()
b = app_b(a)      # b depends on a; Parsl auto-orders
c = app_c(b)
print(c.result())
```

### Fan-out / fan-in

```python
# Fan-out
futures = [worker(i) for i in range(100)]

# Fan-in (gather)
@python_app
def merge(inputs=[]):
    return sum(inputs)

result = merge(inputs=futures).result()
```

### Dynamic DAG with @join_app

```python
@join_app
def dynamic_pipeline(data):
    # data.result() resolves the upstream future
    items = data.result()
    return [process(item) for item in items]
```

### Conditional branching

```python
@python_app
def branch(condition, inputs=[]):
    # Branch logic inside the app
    if condition:
        return inputs[0]
    return inputs[1]
```

### Checkpointing & memoization

```python
# Enable checkpointing at config level
config = Config(
    checkpoint_mode="task_exit",
    checkpoint_files=parsl.utils.get_all_checkpoints(),
)

# Disable memoization for a specific app
@python_app(cache=False)
def non_cached_app(x): ...

# Force re-run (ignore existing checkpoint)
@python_app(cache=True)
def cached_app(x): ...

# Manual checkpoint
dfk.checkpoint()
```

Checkpoint files are stored in `runinfo/<run_id>/checkpoint/`.

---

## 6. Monitoring

```python
from parsl.monitoring.monitoring import MonitoringHub
from parsl.addresses import address_by_hostname

config = Config(
    monitoring=MonitoringHub(
        hub_address=address_by_hostname(),
        hub_port=55055,
        monitoring_debug=False,
        resource_monitoring_interval=10,  # seconds
    ),
    ...
)
```

Launch the monitoring dashboard (separate process):

```bash
parsl-visualize
# opens http://localhost:8080 by default
# or:
parsl-visualize --port 8081 --database monitoring.db
```

The monitoring DB is `monitoring.db` in the run directory.

---

## 7. Common Pitfalls & Rules

| Pitfall | Rule |
|---|---|
| Imports outside `@python_app` body | **Always import inside the function body.** Workers don't share the main namespace. |
| Re-calling `parsl.load()` without `parsl.clear()` | Call `parsl.clear()` before loading a new config. In Jupyter, use a factory. |
| Passing plain strings as file paths to bash_app | Use `parsl.File(path)` and pass via `inputs`/`outputs` kwargs. |
| Calling `.result()` inside an app | Never block inside an app — it deadlocks the worker. Pass futures as arguments instead. |
| Missing `outputs=[]` default in bash_app signature | Always provide `outputs=[]` as a default parameter. |
| Forgetting `address=` in HTEX on multi-node clusters | Set `address=address_by_hostname()` or an explicit IP the workers can reach. |
| Worker can't connect back to manager | Check firewall rules; the HTEX manager port (default 54928) must be reachable from workers. |
| Serialization failures | Parsl uses `cloudpickle`; lambdas and some closures fail. Move logic into named functions decorated with `@python_app`. |
| Jupyter: re-running load cell | Use the factory pattern (`parsl.clear()` then `parsl.load()`). |
| MPI app submitted to HTEX | MPI apps must target an `MPIExecutor` via `executors=["mpi_label"]`. |
| `parsl_resource_specification` ignored | Only honoured by `MPIExecutor`; ignored by `HighThroughputExecutor`. |

---

## 8. API Cheat Sheet

### Core decorators

```python
@python_app(executors=["label"], cache=True, ignore_for_cache=["arg"])
def fn(*args, inputs=[], outputs=[], stdout=None, stderr=None): ...

@bash_app(executors=["label"], cache=True)
def fn(*args, inputs=[], outputs=[], stdout=None, stderr=None) -> str: ...

@join_app(executors=["label"])
def fn(*args) -> list[AppFuture]: ...
```

### AppFuture methods

```python
future.result(timeout=None)      # block and return value; raises on exception
future.exception()               # return exception or None
future.done()                    # bool, non-blocking
future.cancel()                  # attempt cancellation (best-effort)
future.add_done_callback(fn)     # fn(future) called on completion
future.outputs                   # list of DataFuture (for bash_app outputs)
```

### File

```python
from parsl.data_provider.files import File
File(url: str)   # url can be local path, http://, ftp://, globus://
file.filepath    # local path on worker
file.url         # original URL
```

### Config

```python
Config(
    executors: list[Executor],
    strategy: str = "simple",         # "simple" | "htex_auto_scale" | "none"
    max_idletime: float = 120.0,
    checkpoint_mode: str | None,      # "task_exit" | "periodic" | "dfk_exit" | "manual"
    checkpoint_files: list[str] = [],
    checkpoint_period: str = "00:30:00",  # for "periodic" mode
    retries: int = 0,
    run_dir: str = "runinfo",
    monitoring: MonitoringHub | None = None,
)
```

### HighThroughputExecutor

```python
HighThroughputExecutor(
    label: str = "htex",
    address: str,                      # IP/hostname reachable by workers
    cores_per_worker: float = 1.0,
    max_workers: int | float = float("inf"),
    mem_per_worker: float | None = None,  # GB
    worker_debug: bool = False,
    provider: ExecutionProvider,
    storage_access: list = [],
    poll_period: int = 10,            # ms
)
```

### SlurmProvider

```python
SlurmProvider(
    partition: str,
    account: str = "",
    nodes_per_block: int = 1,
    cores_per_node: int,
    init_blocks: int = 1,
    min_blocks: int = 0,
    max_blocks: int = 1,
    parallelism: float = 1.0,
    walltime: str = "00:10:00",
    scheduler_options: str = "",      # extra #SBATCH lines
    worker_init: str = "",            # shell commands before worker starts
    launcher: Launcher = SrunLauncher(),
    cmd_timeout: int = 30,
)
```

### MPIExecutor

```python
MPIExecutor(
    label: str = "mpi",
    max_workers: int = 1,             # concurrent MPI jobs (not ranks)
    launcher: Launcher,               # SrunLauncher, MpiexecLauncher, etc.
    provider: ExecutionProvider,
    mpi_command: list[str] | None = None,
)
```

### MonitoringHub

```python
MonitoringHub(
    hub_address: str,
    hub_port: int = 55055,
    monitoring_debug: bool = False,
    resource_monitoring_interval: int = 30,
    logging_endpoint: str | None = None,
    workflow_name: str | None = None,
)
```

### Utility functions

```python
parsl.load(config: Config) -> DataFlowKernel
parsl.clear()                              # unload current DFK
parsl.wait_for_current_tasks()             # block until all submitted tasks finish
parsl.utils.get_all_checkpoints(rundir="runinfo") -> list[str]
parsl.utils.get_last_checkpoint(rundir="runinfo") -> list[str]
dfk.checkpoint()                           # manual checkpoint
```

---

## 9. Jupyter Integration

```python
# Cell 1: imports (run once)
import parsl
from parsl.config import Config
from parsl.executors import HighThroughputExecutor

# Cell 2: config factory — safe to re-run
def load_parsl():
    parsl.clear()   # no-op if not loaded; clears if already loaded
    config = Config(executors=[HighThroughputExecutor(label="htex")])
    return parsl.load(config)

dfk = load_parsl()

# Cell 3: define apps (can re-run without issues)
from parsl import python_app

@python_app
def my_task(x):
    return x * 2

# Cell 4: run workflow
futures = [my_task(i) for i in range(10)]
results = [f.result() for f in futures]
print(results)
```

**Jupyter-specific gotchas:**
- `ipyparallel` and Parsl are separate — do not mix them.
- The Jupyter process itself counts as the "driver"; workers are separate processes.
- `%autoreload` does not affect app code already submitted to workers.
- Avoid defining `@python_app` functions in notebook cells that close over notebook-local variables — the closures won't serialize correctly.
- If a kernel restart is needed, workers from the old session may still be running; check your cluster scheduler or `ps` output.

---

## 10. Minimal Working Examples

### Local parallel execution

```python
import parsl
from parsl import python_app
from parsl.config import Config
from parsl.executors import HighThroughputExecutor

@python_app
def square(x):
    return x ** 2

with parsl.load(Config(executors=[HighThroughputExecutor()])):
    results = [square(i).result() for i in range(10)]
print(results)
```

### Slurm cluster

```python
import parsl
from parsl import python_app
from parsl.config import Config
from parsl.executors import HighThroughputExecutor
from parsl.providers import SlurmProvider
from parsl.launchers import SrunLauncher
from parsl.addresses import address_by_hostname

@python_app
def simulate(seed):
    import random
    random.seed(seed)
    return random.random()

config = Config(
    executors=[
        HighThroughputExecutor(
            label="slurm_htex",
            address=address_by_hostname(),
            provider=SlurmProvider(
                partition="compute",
                nodes_per_block=1,
                cores_per_node=32,
                init_blocks=1,
                max_blocks=5,
                walltime="01:00:00",
                worker_init="source activate myenv",
                launcher=SrunLauncher(),
            ),
        )
    ]
)

with parsl.load(config):
    futures = [simulate(seed) for seed in range(100)]
    results = [f.result() for f in futures]
```

### File pipeline with bash_app

```python
import parsl
from parsl import bash_app
from parsl.config import Config
from parsl.executors import HighThroughputExecutor
from parsl.data_provider.files import File

@bash_app
def download(url, outputs=[]):
    return f"curl -o {outputs[0]} {url}"

@bash_app
def process(inputs=[], outputs=[], stdout=None):
    return f"python process.py {inputs[0]} {outputs[0]}"

with parsl.load(Config(executors=[HighThroughputExecutor()])):
    raw  = File("/tmp/raw_data.txt")
    done = File("/tmp/processed.txt")

    dl  = download("https://example.com/data.txt", outputs=[raw])
    out = process(inputs=[dl.outputs[0]], outputs=[done])
    out.result()
```
