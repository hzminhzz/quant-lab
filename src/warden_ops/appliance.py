"""Trusted-owner maintenance lifecycle; scientific authority stays with Warden.

An on-demand CLI runtime, one operational journal and the existing canonical ledger.
No signing service, independent-authentication claim, market read or database rewind.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import io
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from warden.lease import MutationBusy, mutation_lease
from warden.registry import SCHEMA_VERSION, ResearchRegistry
from warden_ops.control_contracts import digest
from warden_ops.integrity import check_ast_boundaries, check_worktree
from warden_ops.ledger_maintenance import assert_preserved, backup_readonly, file_sha256, read_state
from warden_ops.release import verify_candidate, verify_materialization
from warden_ops.verification import (
    WITNESS, clean_test_environment, collect_inventory, environment_preflight,
    fast_source_checks, junit_report, run_suite, selection_matches, verification_command,
)

MODEL = "trusted-research-appliance"
HEX = re.compile(r"^[a-f0-9]{64}$")
REQUIRED_CHECKS = ("full-suite", "protected-inventory", "ast-boundaries",
                   "source-integrity", "environment-integrity", "environment-preflight",
                   "collection-inventory", "stage1-performance-witness", "fast-source-checks")
BINDINGS = ("registry", "data/exploratory", "cache", ".workspace/hypotheses")


def _json(path):
    return json.loads(Path(path).read_text())


def _write(path, value):
    """Atomic publication, including the parent directory durability boundary."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    pending = path.with_name(path.name + f".pending-{os.getpid()}")
    try:
        with pending.open("x") as handle:
            json.dump(value, handle, sort_keys=True, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(pending, path)
        _sync_dir(path.parent)
    finally:
        pending.unlink(missing_ok=True)


def _sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _command(argv, *, cwd=None, env=None, timeout=30):
    return subprocess.run(argv, cwd=cwd, env=env, check=True, capture_output=True,
                          text=True, timeout=timeout).stdout.strip()


def _environment(python):
    code = ("import hashlib,importlib.metadata,json,sys;"
            "p=sorted((d.metadata[\"Name\"],d.version) for d in importlib.metadata.distributions());"
            "print(json.dumps({\"version\":sys.version,\"packages\":p}))")
    value = json.loads(_command([str(python), "-I", "-c", code]))
    return {"python": str(python), "python_sha256": file_sha256(python), "identity": digest(value)}


class Appliance:
    def __init__(self, home):
        self.home = Path(home).resolve(strict=True)
        self.config = _json(self.home / "config.json")
        required = {"schema_version", "authority_model", "canonical", "python", "writer_units", "scientific_authority"}
        if (set(self.config) != required or self.config["schema_version"] != 1
                or self.config["authority_model"] != MODEL or self.config["scientific_authority"] is not False
                or not isinstance(self.config["writer_units"], list)
                or not all(isinstance(self.config[key], str) and Path(self.config[key]).is_absolute()
                           for key in ("canonical", "python"))):
            raise ValueError("unsupported appliance configuration")
        self.canonical = Path(self.config["canonical"])
        self.db = self.canonical / "registry/registry.db"

    @property
    def python(self):
        """Active release interpreter; config retains only the bootstrap interpreter."""
        release = self.active_release()
        return self._python(release) if release else Path(self.config["python"])

    def _python(self, release):
        installed = self._last("installed", release)
        if not installed or not installed.get("python"):
            raise ValueError("release has no installed dependency environment")
        return Path(installed["python"])

    @classmethod
    def install(cls, *, home, source, candidate, canonical, python, writer_units):
        home, source, candidate, canonical = [Path(p).absolute() for p in (home, source, candidate, canonical)]
        if any(p.is_symlink() for p in (home, *home.parents)):
            raise ValueError("appliance home must not be a symlink")
        if home.is_relative_to(canonical) or home.is_relative_to(source) or "holdout" in home.parts:
            raise ValueError("appliance home must be independent of development and data")
        if any(not re.fullmatch(r"[A-Za-z0-9_.@-]+\.(timer|service)", unit) for unit in writer_units):
            raise ValueError("invalid configured writer unit")
        checked = verify_candidate(candidate)
        if not checked["valid"]:
            raise ValueError("candidate integrity: " + checked["reason"])
        manifest, release = checked["manifest"], checked["release_id"]
        if manifest["source"]["dirty"]:
            raise ValueError("runtime requires committed clean source")
        commit = manifest["source"]["commit"]
        if _command(["git", "rev-parse", commit + "^{tree}"], cwd=source) != manifest["source"]["tree"]:
            raise ValueError("source commit/tree mismatch")
        if not (canonical / "registry/registry.db").is_file():
            raise ValueError("canonical ledger must already exist")
        config = {"schema_version": 1, "authority_model": MODEL, "canonical": str(canonical.resolve()),
                  "python": str(Path(python).absolute()), "writer_units": sorted(set(writer_units)),
                  "scientific_authority": False}
        home.mkdir(parents=True, exist_ok=True, mode=0o700)
        with mutation_lease(home / "operations.db"):
            path = home / "config.json"
            if path.exists():
                existing = _json(path)
                if {**config, "python": existing.get("python")} != existing:
                    raise ValueError("existing appliance binding differs; explicit configuration migration required")
            if not path.exists():
                _write(path, config)
            appliance = cls(home)
            appliance._init_journal()
            installed = appliance._last("installed", release)
            if installed and installed.get("python") != config["python"]:
                raise ValueError("release environment already bound; preserve it and build a new release")
            _environment(Path(config["python"]))
            stored = home / "releases" / release
            if not stored.exists():
                stored.parent.mkdir(exist_ok=True)
                shutil.copytree(candidate, stored)
            if not verify_candidate(stored)["valid"]:
                raise ValueError("installed candidate integrity differs")
            runtime = appliance.runtime(release)
            if not runtime.exists():
                runtime.parent.mkdir(exist_ok=True)
                # Git owns checkout/index/provenance; sparse checkout never materializes data/history.
                _command(["git", "worktree", "add", "--detach", "--no-checkout", str(runtime), commit], cwd=source)
                _command(["git", "sparse-checkout", "init", "--no-cone"], cwd=runtime)
                patterns = "\n".join("/" + name for name in sorted(manifest["inventory"])) + "\n"
                subprocess.run(["git", "sparse-checkout", "set", "--no-cone", "--stdin"], cwd=runtime,
                               input=patterns, text=True, check=True, capture_output=True, timeout=30)
                _command(["git", "read-tree", "-mu", "HEAD"], cwd=runtime)
                for name in manifest["inventory"]:
                    (runtime / name).chmod(0o555 if manifest["inventory"][name]["executable"] else 0o444)
                for name in BINDINGS:
                    target, link = canonical / name, runtime / name
                    target.mkdir(parents=True, exist_ok=True)
                    link.parent.mkdir(parents=True, exist_ok=True)
                    if link.exists() or link.is_symlink():
                        raise ValueError("runtime state path overlaps frozen source: " + name)
                    link.symlink_to(target.resolve(), target_is_directory=True)
            appliance._check_runtime(release)
            appliance._install_launcher()
            appliance._append("installed", release, {"source": manifest["source"], "runtime": str(runtime),
                                                    "python": config["python"], "scientific_authority": False})
            return appliance

    def _install_launcher(self):
        path = self.home / "warden"
        content = (f"#!{self.config['python']}\n"
                   "import json,os,sqlite3,sys\nfrom pathlib import Path\n"
                   "home=Path(__file__).resolve().parent\n"
                   "root=(home/\"current\").resolve(strict=True)\n"
                   "with sqlite3.connect((home/\"operations.db\").as_uri()+\"?mode=ro\",uri=True) as conn:\n"
                   "    row=conn.execute(\"SELECT payload FROM lifecycle_events WHERE kind='installed' "
                   "AND subject=? ORDER BY sequence DESC LIMIT 1\",(root.name,)).fetchone()\n"
                   "python=json.loads(row[0])[\"python\"]\n"
                   "os.environ.update(PYTHONPATH=str(root/\"src\")+\":\"+str(root),"
                   "PYTHONDONTWRITEBYTECODE=\"1\",PYTHONNOUSERSITE=\"1\")\n"
                   "args=sys.argv[1:]\n"
                   "if args[:1]==[\"appliance\"]:\n"
                   "    args=args[1:]+[\"--home\",str(home)]\n"
                   "else:\n"
                   "    args=[\"run\",\"--home\",str(home),\"--\",*args]\n"
                   "os.execv(python,[python,\"-m\",\"warden_ops.appliance\",*args])\n")
        if path.exists() and path.read_text() != content:
            raise ValueError("installed launcher drift; preserve and review before replacement")
        if not path.exists():
            with path.open("x") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            path.chmod(0o700)
            _sync_dir(self.home)

    def runtime(self, release):
        if not HEX.fullmatch(release):
            raise ValueError("invalid release identity")
        return self.home / "runtimes" / release

    def _init_journal(self):
        with contextlib.closing(sqlite3.connect(self.home / "operations.db")) as conn, conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS lifecycle_events(
                    sequence INTEGER PRIMARY KEY, kind TEXT NOT NULL, subject TEXT NOT NULL,
                    payload TEXT NOT NULL, created_at REAL NOT NULL);
                CREATE TRIGGER IF NOT EXISTS lifecycle_no_update BEFORE UPDATE ON lifecycle_events
                    BEGIN SELECT RAISE(ABORT,'append-only lifecycle evidence'); END;
                CREATE TRIGGER IF NOT EXISTS lifecycle_no_delete BEFORE DELETE ON lifecycle_events
                    BEGIN SELECT RAISE(ABORT,'append-only lifecycle evidence'); END;
            """)

    def _append(self, kind, subject, payload):
        with contextlib.closing(sqlite3.connect(self.home / "operations.db")) as conn, conn:
            conn.execute("INSERT INTO lifecycle_events(kind,subject,payload,created_at) VALUES(?,?,?,?)",
                         (kind, subject, json.dumps(payload, sort_keys=True, allow_nan=False), time.time()))

    def history(self):
        with contextlib.closing(sqlite3.connect((self.home / "operations.db").as_uri() + "?mode=ro", uri=True)) as conn:
            return [{"kind": kind, "subject": subject, "payload": json.loads(payload), "created_at": at}
                    for kind, subject, payload, at in conn.execute(
                        "SELECT kind,subject,payload,created_at FROM lifecycle_events ORDER BY sequence")]

    def history_summary(self, *, after=None, limit=10):
        """Bounded lifecycle index. Full evidence remains in the append-only journal."""
        if not isinstance(limit, int) or not 1 <= limit <= 20:
            raise ValueError("history limit must be between 1 and 20")
        if after is not None and (not isinstance(after, int) or after < 0):
            raise ValueError("history cursor must be a nonnegative sequence")
        with contextlib.closing(sqlite3.connect(
                (self.home / "operations.db").as_uri() + "?mode=ro", uri=True)) as conn:
            conn.execute("BEGIN")
            total, latest = conn.execute(
                "SELECT COUNT(*), COALESCE(MAX(sequence),0) FROM lifecycle_events").fetchone()
            if after is None:
                rows = list(reversed(conn.execute(
                    "SELECT sequence,kind,subject,payload,created_at FROM lifecycle_events "
                    "ORDER BY sequence DESC LIMIT ?", (limit,)).fetchall()))
            else:
                rows = conn.execute(
                    "SELECT sequence,kind,subject,payload,created_at FROM lifecycle_events "
                    "WHERE sequence>? ORDER BY sequence LIMIT ?", (after, limit)).fetchall()
        events = []
        for sequence, kind, subject, raw, created_at in rows:
            payload = json.loads(raw)
            event = {"sequence": sequence, "kind": kind[:80], "subject": subject[:100],
                     "created_at": created_at, "payload_sha256": digest(payload)}
            for key in ("state", "ok", "verification_id", "release_id"):
                value = payload.get(key)
                if isinstance(value, (bool, int, float)):
                    event[key] = value
                elif isinstance(value, str):
                    event[key] = value[:100]
            events.append(event)
        cursor = events[-1]["sequence"] if events else (after or 0)
        return {"total": total, "latest_sequence": latest, "events": events,
                "next_after": cursor if cursor < latest else None,
                "older_events_omitted": bool(events and events[0]["sequence"] > 1),
                "full_evidence": "appliance history --full", "scientific_authority": False}

    def _last(self, kind, subject):
        with contextlib.closing(sqlite3.connect((self.home / "operations.db").as_uri() + "?mode=ro", uri=True)) as conn:
            row = conn.execute("SELECT payload FROM lifecycle_events WHERE kind=? AND subject=? "
                               "ORDER BY sequence DESC LIMIT 1", (kind, subject)).fetchone()
        return json.loads(row[0]) if row else None

    def _env(self, root, *, maintenance=False):
        env = {**os.environ, "PYTHONPATH": f"{root / 'src'}:{root}",
               "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1", "WARDEN_NON_INTERACTIVE": "1"}
        # Maintenance health/tests do not receive scientific signing authority or deployment context.
        env.pop("WARDEN_HUMAN_SIGNING_KEY", None)
        env.pop("QUANT_LAB_APPLIANCE_HOME", None)
        if maintenance:
            env.pop("PYTEST_ADDOPTS", None)
            env.pop("PYTEST_PLUGINS", None)
        if not maintenance:
            env["QUANT_LAB_APPLIANCE_HOME"] = str(self.home)
        return env

    def _check_runtime(self, release):
        root, candidate = self.runtime(release), self.home / "releases" / release
        manifest = verify_materialization(candidate, root)
        for name in BINDINGS:
            link = root / name
            if not link.is_symlink() or os.readlink(link) != str((self.canonical / name).resolve()):
                raise ValueError("runtime state binding drift: " + name)
        return manifest

    def verify(self, release):
        """Serialize attempts; preserve incomplete and successful evidence separately."""
        with mutation_lease(self.home / "operations.db"):
            self.runtime(release)  # Validate the identity before making evidence paths.
            directory = self.home / "verification" / release
            directory.mkdir(parents=True, exist_ok=True)
            prior = self._last("verification-started", release)
            if prior:
                terminal = [self._last(kind, release) for kind in
                            ("verified", "verification-incomplete", "verification-failed")]
                if not any(event and event.get("evidence_directory") == prior["evidence_directory"]
                           for event in terminal):
                    result = {**prior, "state": "INCOMPLETE",
                              "reason": "previous owner exited without terminal receipt"}
                    _write(Path(prior["evidence_directory"]) / "incomplete.json", result)
                    self._append("verification-incomplete", release, result)
            attempt = Path(tempfile.mkdtemp(prefix="attempt-", dir=directory))
            started = {"state": "STARTED", "release_id": release, "evidence_directory": str(attempt),
                       "observed_at": time.time(), "scientific_authority": False}
            _write(attempt / "started.json", started)
            self._append("verification-started", release, started)
            try:
                return self._verify(release, attempt)
            except (KeyboardInterrupt, subprocess.TimeoutExpired) as exc:
                result = {**started, "state": "INCOMPLETE", "reason": type(exc).__name__}
                _write(attempt / "incomplete.json", result)
                self._append("verification-incomplete", release, result)
                raise
            except Exception as exc:
                result = {**started, "state": "FAILED", "reason": str(exc)[:1000]}
                _write(attempt / "failed.json", result)
                self._append("verification-failed", release, result)
                raise

    def _verification_source(self, release, directory, manifest):
        """Complete committed test assets without canonical registry/data bindings."""
        source = directory / "source"
        runtime = self.runtime(release)
        commit = manifest["source"]["commit"]
        tracked = _command(["git", "ls-tree", "-r", "--name-only", commit], cwd=runtime).splitlines()
        if any(name.startswith(("data/", "registry/registry.db")) for name in tracked):
            raise ValueError("verification source contains market data or a canonical database")
        if not source.exists():
            _command(["git", "worktree", "add", "--detach", str(source), commit], cwd=runtime)
            _command(["git", "sparse-checkout", "disable"], cwd=source)
        if (source / "registry/registry.db").exists() or (source / "data").exists():
            raise ValueError("verification source must not have registry/data bindings")
        verify_materialization(self.home / "releases" / release, source)
        _command(["git", "diff", "--quiet", "HEAD", "--"], cwd=source)
        return source

    def _verify(self, release, attempt):
        """Fixed inventory and full suite plus isolated strict performance witness."""
        manifest = self._check_runtime(release)
        candidate = self.home / "releases" / release
        directory = attempt.parent
        source = self._verification_source(release, attempt, manifest)
        env = self._env(source, maintenance=True)
        # Synthetic approval fixtures establish their own context; no production state is mounted.
        env.pop("WARDEN_NON_INTERACTIVE", None)
        env, cleared = clean_test_environment(env)
        python = self._python(release)
        before_environment = _environment(python)
        preflight = environment_preflight(python, source, env)
        fast_checks = fast_source_checks(python, source, env)
        base = verification_command(python, source, attempt)
        nodes = collect_inventory(base, source, env, attempt)
        _write(attempt / "collection.json", {"node_ids": nodes, "count": len(nodes)})
        xml = attempt / "suite.xml"
        witness_xml = attempt / "witness.xml"
        argv = [*base, "-n", "4", "--dist", "worksteal", "--deselect", WITNESS,
                "--warden-selection", str(attempt / "suite-selection"),
                f"--junitxml={xml}", "tests"]
        witness_cpus = sorted(os.sched_getaffinity(0))[-2:]
        witness_argv = ["/usr/bin/taskset", "--cpu-list", ",".join(map(str, witness_cpus)),
                        *base, "-n", "0", "--warden-selection", str(attempt / "witness-selection"),
                        f"--junitxml={witness_xml}", WITNESS]
        suite_rc = run_suite(argv, source, env, attempt / "suite.log")
        # The strict 100 x 20k witness never competes with the xdist workers.
        witness_rc = run_suite(witness_argv, source, env, attempt / "witness.log")
        suite_counts, suite_skips = junit_report(xml)
        witness, witness_skips = junit_report(witness_xml)
        counts = {key: suite_counts[key] + witness[key] for key in suite_counts}
        inventory_ok = (counts["tests"] == len(nodes)
                        and suite_counts["tests"] == len(nodes) - 1
                        and selection_matches(attempt, nodes))
        witness_ok = (witness_rc == 0 and witness["tests"] == 1
                      and witness["failures"] == witness["errors"] == witness["skipped"] == 0)
        with contextlib.redirect_stdout(io.StringIO()):
            integrity = check_worktree(candidate)
        source_error = None
        try:
            self._check_runtime(release)
            verify_materialization(candidate, source)
            _command(["git", "diff", "--quiet", "HEAD", "--"], cwd=source)
            if any((source / name).is_symlink() for name in BINDINGS):
                raise ValueError("test source acquired external state bindings")
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            source_error = str(exc)
        checks = {"full-suite": suite_rc == 0 and witness_ok and inventory_ok
                  and counts["failures"] == counts["errors"] == 0,
                  "protected-inventory": integrity, "ast-boundaries": not check_ast_boundaries(candidate),
                  "source-integrity": source_error is None,
                  "environment-integrity": _environment(python) == before_environment,
                  "environment-preflight": True, "collection-inventory": inventory_ok,
                  "stage1-performance-witness": witness_ok, "fast-source-checks": True}
        artifact_names = ("collection.log", "collection.json", "suite.xml", "suite.log",
                          "witness.xml", "witness.log",
                          *(path.name for path in sorted(attempt.glob("*-selection-*.json"))))
        artifacts = {name: file_sha256(attempt / name) for name in artifact_names
                     if (attempt / name).is_file()}
        result = {"schema_version": 1, "release_id": release, "ok": all(checks.values()),
                  "test_source": str(source), "test_source_commit": manifest["source"]["commit"],
                  "canonical_state_mounted": False, "source_error": source_error,
                  "evidence_directory": str(attempt),
                  "required_checks": list(REQUIRED_CHECKS), "checks": checks, "suite": counts,
                  "collection_count": len(nodes), "witness": witness, "preflight": preflight,
                  "fast_checks": fast_checks,
                  "cleared_test_environment": cleared, "skip_reasons": suite_skips + witness_skips,
                  "artifacts": artifacts, "suite_sha256": artifacts.get("suite.xml"),
                  "log_sha256": artifacts.get("suite.log"), "command": argv,
                  "witness_command": witness_argv,
                  "environment": before_environment, "dependency_lock_sha256": manifest["dependency_lock_sha256"],
                  "baseline": "full committed tests + isolated strict Stage 1 witness + inventory + AST",
                  "scientific_authority": False, "observed_at": time.time()}
        result["verification_id"] = digest(result)
        _write(attempt / "receipt.json", result)
        self._append("verified", release, result)
        _write(directory / "current.json", {"attempt": attempt.name, "verification_id": result["verification_id"]})
        return result

    def _verified(self, release, *, manifest=None):
        # A caller may pass only its freshly verified materialization within this
        # observation. Mutating callers always start a fresh verification.
        if manifest is None:
            manifest = self._check_runtime(release)
        directory = self.home / "verification" / release
        current = _json(directory / "current.json")
        path = directory / current["attempt"]
        if path.is_symlink() or path.resolve().parent != directory.resolve():
            raise ValueError("verification pointer outside immutable evidence")
        value = _json(path / "receipt.json")
        started = self._last("verification-started", release)
        invalidated = any(event and event.get("evidence_directory") == str(path)
                          for event in (self._last("verification-incomplete", release),
                                        self._last("verification-failed", release)))
        artifacts = value.get("artifacts", {})
        if (value["release_id"] != release or not value["ok"] or invalidated
                or not started or started["evidence_directory"] != str(path)
                or current["verification_id"] != value["verification_id"]
                or self._last("verified", release) != value
                or value["test_source_commit"] != manifest["source"]["commit"]
                or value["dependency_lock_sha256"] != manifest["dependency_lock_sha256"]
                or value["required_checks"] != list(REQUIRED_CHECKS)
                or not all(value["checks"].get(check) is True for check in REQUIRED_CHECKS)
                or digest({k: v for k, v in value.items() if k != "verification_id"}) != value["verification_id"]
                or not {"collection.log", "collection.json", "suite.xml", "suite.log",
                        "witness.xml", "witness.log", "collect-selection-controller.json",
                        "witness-selection-controller.json",
                        *(f"suite-selection-gw{i}.json" for i in range(4))} <= set(artifacts)
                or any(Path(name).name != name for name in artifacts)
                or any((path / name).is_symlink() or file_sha256(path / name) != sha
                       for name, sha in artifacts.items())
                or value["environment"] != _environment(self._python(release))):
            raise ValueError("verification missing, incomplete, changed or no longer valid")
        return value

    def writer_state(self):
        result = {}
        for unit in self.config["writer_units"]:
            raw = _command(["systemctl", "--user", "show", unit, "-p", "LoadState", "-p", "ActiveState",
                            "-p", "MainPID", "-p", "UnitFileState", "-p", "FragmentPath"])
            value = dict(line.split("=", 1) for line in raw.splitlines() if "=" in line)
            if value.get("LoadState") != "loaded":
                raise ValueError("configured writer unit unavailable: " + unit)
            result[unit] = value
        return result

    def pause_writers(self):
        before = self.writer_state()
        self._append("writers-pause-intent", "configured-writers", before)
        # Timers first; stopping a running service is allowed only after it exits naturally.
        timers = [unit for unit in self.config["writer_units"] if unit.endswith(".timer")]
        definitions, restored = {}, []
        for unit in timers:
            fragment = before[unit].get("FragmentPath")
            if not fragment:
                raise ValueError("configured timer definition unavailable: " + unit)
            target = Path(fragment).resolve(strict=True)
            if not target.is_file():
                raise ValueError("configured timer definition is not a file: " + unit)
            definitions[unit] = (target, file_sha256(target))
        if timers:
            _command(["systemctl", "--user", "disable", "--now", *timers])
        for unit, (target, sha256) in definitions.items():
            # disable also removes externally linked unit definitions on this host.
            # Restore the same definition, without recreating enablement or starting it.
            if _command(["systemctl", "--user", "show", unit, "-p", "LoadState", "--value"]) == "not-found":
                if file_sha256(target) != sha256:
                    raise ValueError("timer definition changed while pausing: " + unit)
                _command(["systemctl", "--user", "link", str(target)])
                _command(["systemctl", "--user", "reset-failed", unit])
                self._append("writer-unit-link-restored", unit,
                             {"definition": str(target), "sha256": sha256, "enabled": False})
                restored.append(unit)
        after = self.writer_state()
        self._append("writers-observed", "configured-writers", after)
        return {"before": before, "after": after, "quiescent": self._quiescent(after),
                "restored_unit_links": restored}

    @staticmethod
    def _quiescent(state):
        return all(s.get("ActiveState") in {"inactive", "failed"} and s.get("MainPID", "0") == "0"
                   and (not unit.endswith(".timer") or s.get("UnitFileState") not in {"enabled", "enabled-runtime"})
                   for unit, s in state.items())

    @contextlib.contextmanager
    def _writers(self):
        with mutation_lease(self.db):
            fd = os.open(self.db.parent / "warden-writer.lock", os.O_CREAT | os.O_RDWR, 0o600)
            try:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise MutationBusy("legacy scheduler writer is active") from exc
                if not self._quiescent(self.writer_state()):
                    raise ValueError("configured writers are not quiescent; pause and observe before transition")
                from warden_ops.readonly import open_readonly
                from warden_ops.jobs import _HELD

                view = open_readonly(self.db).read_control_snapshot(limit=1)
                if any(state in _HELD and count for state, count in
                       (view.get("job_states") or {}).items()):
                    raise MutationBusy("operational jobs still hold resources; resolve before transition")
                yield
            finally:
                os.close(fd)

    def _publish(self, release):
        target = self.runtime(release)
        pending = self.home / f".current-{os.getpid()}"
        try:
            pending.symlink_to(target, target_is_directory=True)
            os.replace(pending, self.home / "current")
            _sync_dir(self.home)
        finally:
            pending.unlink(missing_ok=True)

    def transition(self, release, *, request_id, effect="maintenance"):
        if effect != "maintenance":
            raise ValueError("outside maintenance scope: scientific effects require existing Warden authority")
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", request_id):
            raise ValueError("invalid request identity")
        fingerprint = digest({"release_id": release, "canonical": str(self.db.resolve()), "effect": effect})
        with mutation_lease(self.home / "operations.db"):
            intent = self._last("transition-intent", request_id)
            if intent and intent["fingerprint"] != fingerprint:
                raise ValueError("request identity conflict")
            completed = self._last("transition-result", request_id)
            if completed and completed["state"] == "ACTIVE":
                halted = self.home / "halted.json"
                if (halted.exists() and _json(halted).get("request_id") == request_id
                        and self.active_release() == release and self._probe(release)["healthy"]):
                    halted.unlink()
                    _sync_dir(self.home)
                return completed
            verification = self._verified(release)
            manifest = self._check_runtime(release)
            if not manifest["ledger_min"] <= SCHEMA_VERSION <= manifest["ledger_max"]:
                raise ValueError("runtime is incompatible with canonical migration schema")
            with self._writers():
                if intent is None:
                    parent = self.home / "transitions"
                    parent.mkdir(exist_ok=True)
                    directory = Path(tempfile.mkdtemp(prefix=request_id + "-", dir=parent))
                    rehearsal_db = self.home / "rehearsals" / directory.name / "registry.db"
                    backup = backup_readonly(self.db, directory / "before.db")
                    before = read_state(directory / "before.db")
                    backup_readonly(directory / "before.db", rehearsal_db, allow_snapshot_name=True)
                    ResearchRegistry(rehearsal_db)
                    rehearsal = assert_preserved(before, rehearsal_db)
                    after_rehearsal = read_state(rehearsal_db)
                    ResearchRegistry(rehearsal_db)
                    if after_rehearsal != read_state(rehearsal_db):
                        raise ValueError("migration is not idempotent")
                    assert_preserved(before, self.db)
                    intent = {"fingerprint": fingerprint, "release_id": release, "before": before,
                              "backup": backup, "rehearsal": {**rehearsal, "path": str(rehearsal_db), "idempotent": True},
                              "verification_id": verification["verification_id"],
                              "previous_runtime": self.active_release(), "scientific_authority": False}
                    self._append("transition-intent", request_id, intent)
                elif intent["verification_id"] != verification["verification_id"]:
                    raise ValueError("verification changed during interrupted transition")
                _write(self.home / "halted.json", {"reason": "transition in progress", "request_id": request_id})
                try:
                    assert_preserved(intent["before"], self.db)
                    current = read_state(self.db)
                    if current["version"] < SCHEMA_VERSION:
                        ResearchRegistry(self.db)
                    elif current["version"] != SCHEMA_VERSION:
                        raise ValueError("canonical ledger newer than this runtime; halt without rewind")
                    preservation = assert_preserved(intent["before"], self.db)
                    if not self._last("migration-complete", request_id):
                        self._append("migration-complete", request_id, preservation)
                    self._publish(release)
                    observed = self._probe(release)
                    if not observed["healthy"] or self.active_release() != release:
                        raise ValueError("actual runtime health/identity failed after activation")
                    result = {"state": "ACTIVE", "request_id": request_id, "release_id": release,
                              "verification_id": verification["verification_id"], "preservation": preservation,
                              "observed": observed, "authority_model": MODEL, "scientific_authority": False}
                    self._append("transition-result", request_id, result)
                    (self.home / "halted.json").unlink(missing_ok=True)
                    _sync_dir(self.home)
                    return result
                except Exception as exc:
                    self._append("transition-result", request_id, {"state": "HALTED", "release_id": release,
                                                                 "reason": str(exc), "database_rewound": False})
                    raise

    def active_release(self):
        path = self.home / "current"
        if not path.is_symlink():
            return None
        resolved = path.resolve()
        if resolved.parent != self.home / "runtimes" or not HEX.fullmatch(resolved.name):
            raise ValueError("active runtime pointer outside installed releases")
        return resolved.name

    def _probe(self, release):
        manifest = self._check_runtime(release)
        root, python = self.runtime(release), self._python(release)
        if (Path(__file__).resolve().parents[1] == root / "src"
                and Path(sys.executable).absolute() == python.absolute()
                and "WARDEN_HUMAN_SIGNING_KEY" not in os.environ):
            # The stable launcher already selected this exact interpreter and
            # pinned module tree. Do not spawn another identical observer.
            value = _local_probe(self, release, manifest=manifest)
        else:
            result = _command([str(python), "-m", "warden_ops.appliance", "_probe",
                               "--home", str(self.home), "--release", release], cwd=root,
                              env=self._env(root, maintenance=True), timeout=60)
            value = json.loads(result)
        if (value["module_root"] != str(root / "src") or value["release_id"] != release
                or Path(value["python"]).absolute() != python.absolute()):
            raise ValueError("observed executable identity differs from installed runtime")
        return value

    def observe(self):
        release = self.active_release()
        if release is None:
            return {"status": "NOT_INSTALLED", "release_id": None, "healthy": False,
                    "authority_model": MODEL, "scientific_authority": False}
        try:
            value = self._probe(release)
            if self.active_release() != release:
                raise ValueError("active runtime changed during observation; refresh")
            if (self.home / "halted.json").exists():
                value.update(status="HALTED", healthy=False, halt=_json(self.home / "halted.json"))
            return value
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            return {"status": "UNAVAILABLE", "release_id": release, "healthy": False, "reason": str(exc)[:500],
                    "authority_model": MODEL, "scientific_authority": False}

    def halt(self, reason):
        with mutation_lease(self.home / "operations.db"):
            result = {"state": "HALTED", "reason": str(reason)[:1000], "release_id": self.active_release(),
                      "database_rewound": False}
            _write(self.home / "halted.json", result)
            self._append("halted", result["release_id"] or "none", result)
            return result

    def run(self, argv):
        """Launch pinned code only after installed inventory and schema checks."""
        observed = self.observe()
        if not observed["healthy"]:
            raise ValueError("appliance is not healthy: " + observed["status"])
        root = self.runtime(observed["release_id"])
        # Only the typed read-only observation may consume this request's proof.
        # Every other operation goes through the native CLI and fresh admission.
        if (argv[:1] == ["control"] and Path(__file__).resolve().parents[1] == root / "src"
                and Path(sys.executable).absolute() == self._python(observed["release_id"]).absolute()):
            from warden.cli import build_parser
            from warden.policy import authorize
            from warden_ops.control import Control

            args = build_parser().parse_args(argv)
            try:
                payload = json.loads(args.request)
            except ValueError:
                payload = None
            if (isinstance(payload, dict) and payload.get("operation") == "observe"
                    and authorize("control").allow):
                control = Control(root, runtime_probe=lambda: observed, caller_root=Path.cwd())
                result = control.handle(payload)
                print(json.dumps(result))
                return 0 if result["ok"] else 2
        env = self._env(root)
        env["QUANT_LAB_CALLER_ROOT"] = str(Path.cwd())
        return subprocess.run([str(self._python(observed["release_id"])), "-m", "warden.cli", *argv], cwd=root,
                              env=env, check=False).returncode


def _local_probe(appliance, release, *, manifest=None):
    root = appliance.runtime(release)
    if manifest is None:
        manifest = appliance._check_runtime(release)
    verification = appliance._verified(release, manifest=manifest)
    with contextlib.closing(sqlite3.connect(appliance.db.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        conn.execute("PRAGMA query_only=ON")
        schema = conn.execute("PRAGMA user_version").fetchone()[0]
        integrity = conn.execute("PRAGMA quick_check").fetchone()[0]
    with contextlib.redirect_stdout(io.StringIO()):
        source_ok = check_worktree(root)
    return {"status": "ACTIVE", "release_id": release, "source_commit": manifest["source"]["commit"],
            "module_root": str(Path(__file__).resolve().parents[1]), "pid": os.getpid(),
            "python": sys.executable, "runtime_kind": "on-demand CLI",
            "environment": verification["environment"], "verification_id": verification["verification_id"],
            "dependency_lock_sha256": manifest["dependency_lock_sha256"],
            "canonical_ledger": str(appliance.db.resolve()), "ledger_schema": schema,
            "healthy": source_ok and integrity == "ok" and manifest["ledger_min"] <= schema <= manifest["ledger_max"],
            "authority_model": MODEL, "scientific_authority": False, "sealed_access": False,
            "observed_at": time.time()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["install", "verify", "transition", "observe", "pause-writers", "halt", "run", "_probe", "history"])
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--release")
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--canonical", type=Path)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--writer-unit", action="append", default=[])
    parser.add_argument("--request-id")
    parser.add_argument("--reason", default="operator maintenance halt")
    parser.add_argument("--full", action="store_true", help="Return full lifecycle evidence.")
    parser.add_argument("--after", type=int)
    parser.add_argument("--limit", type=int, default=10)
    args, rest = parser.parse_known_args(argv)
    try:
        if args.operation != "run" and rest:
            raise ValueError("unknown lifecycle arguments")
        if args.operation == "install":
            app = Appliance.install(home=args.home, source=args.source, candidate=args.candidate,
                                    canonical=args.canonical, python=args.python, writer_units=args.writer_unit)
            value = {"state": "PREPARED", "home": str(app.home), "scientific_authority": False}
        else:
            app = Appliance(args.home)
            if args.operation == "run":
                return app.run(rest[1:] if rest[:1] == ["--"] else rest)
            if args.operation == "_probe":
                value = _local_probe(app, args.release)
            elif args.operation == "verify":
                value = app.verify(args.release)
            elif args.operation == "transition":
                value = app.transition(args.release, request_id=args.request_id)
            elif args.operation == "pause-writers":
                value = app.pause_writers()
            elif args.operation == "halt":
                value = app.halt(args.reason)
            elif args.operation == "history":
                value = app.history() if args.full else app.history_summary(
                    after=args.after, limit=args.limit)
            else:
                value = app.observe()
        print(json.dumps(value, sort_keys=True))
        return 0 if not isinstance(value, dict) or value.get("ok", True) else 2
    except MutationBusy as exc:
        print(json.dumps({"state": "BUSY", "reason": str(exc)}))
        return 75
    except (ValueError, TypeError, OSError, sqlite3.Error, subprocess.SubprocessError) as exc:
        print(json.dumps({"state": "REFUSED", "reason": str(exc), "scientific_authority": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
